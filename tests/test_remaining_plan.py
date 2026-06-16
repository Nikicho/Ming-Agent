import json

import pytest

from ming.config import AgentConfig, LLMConfig, MingConfig
from ming.core.agent import Agent
from ming.core.llm import LLMResponse, Message
from ming.core.progress import ProgressAssessment, ToolEvent
from ming.core.recovery import ErrorClassifier, format_llm_failure, format_tool_stall
from ming.core.trace import CheckpointStore
from ming.memory.store import MemoryStore
from ming.skills.index import SkillIndex, ToolNeedProposal
from ming.tools.web import WebResearchTool


def test_error_classifier_marks_retryable_and_irrecoverable_errors():
    classifier = ErrorClassifier()

    assert classifier.classify("TimeoutError: request timed out").retryable is True
    assert classifier.classify("[Permission denied] reset hard").recoverable is False
    assert classifier.classify("old_string not found in file.").category == "tool_input"


def test_llm_timeout_failure_is_user_facing_with_hidden_technical_detail():
    class TimeoutLikeError(Exception):
        pass

    exc = TimeoutLikeError(
        "litellm.Timeout: Timeout Error: DeepseekException - "
        "Connection timed out. Timeout passed=600.0, time taken=600.308 seconds"
    )

    failure = format_llm_failure(exc)

    assert "模型服务 10 分钟没有响应" in failure.user_message
    assert "已保留当前进度" in failure.user_message
    assert "DeepseekException" not in failure.user_message
    assert "litellm.Timeout" in failure.technical_detail
    assert failure.category == "timeout"
    assert failure.retryable is True


def test_tool_stall_failure_explains_why_agent_paused():
    assessment = ProgressAssessment(
        decision="stop",
        reason="连续 3 次工具调用没有产生有效新证据，停止继续尝试同类策略。",
    )
    events = [
        ToolEvent("bash", "bash", "ok", 1, 0, "no_signal"),
        ToolEvent("bash", "bash", "ok", 1, 0, "no_signal"),
        ToolEvent("file_read", "file_read", "error", 0, 0, "no_signal"),
    ]

    failure = format_tool_stall(assessment, events)

    assert "我暂停了本轮执行" in failure.user_message
    assert "连续 3 次工具调用没有拿到可用的新信息" in failure.user_message
    assert "bash" in failure.user_message
    assert "换一种工具" in failure.user_message
    assert "no_signal" not in failure.user_message
    assert "no_signal" in failure.technical_detail
    assert failure.category == "tool_stall"


def test_tool_stall_failure_names_internal_tool_strategy_errors():
    assessment = ProgressAssessment(
        decision="stop",
        reason="工具调用策略失败：连续工具参数错误或写入策略失败。",
    )
    events = [
        ToolEvent(
            "file_write",
            "file_write",
            "error",
            83,
            0,
            "tool_input_error",
            diagnostic="Invalid JSON arguments: Unterminated string",
        ),
        ToolEvent(
            "bash",
            "bash",
            "error",
            34,
            0,
            "tool_strategy_error",
            diagnostic="Command line is too long",
        ),
    ]

    failure = format_tool_stall(assessment, events)

    assert failure.category == "tool_strategy_error"
    assert "工具调用格式或写入策略失败" in failure.user_message
    assert "Ming 内部执行策略问题" in failure.user_message
    assert "补充文件" not in failure.user_message
    assert "Invalid JSON arguments" in failure.technical_detail


@pytest.mark.asyncio
async def test_agent_reenters_loop_after_t3_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = MingConfig(
        llm=LLMConfig(model="test-model", api_key="test"),
        agent=AgentConfig(max_iterations=8),
    )
    calls = 0

    async def fake_llm(messages, config, tools=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "file_write",
                            "arguments": json.dumps({"path": "out.txt", "content": "wrong"}),
                        },
                    }
                ],
            )
        if calls == 2:
            return LLMResponse(content="FINAL: 已写入 right", finish_reason="stop")
        if calls == 3:
            return LLMResponse(
                content="FAIL: 工具写入 wrong，但最终答复说 right",
                finish_reason="stop",
            )
        if calls == 4:
            assert "T3 核验失败" in messages[-1].content
            return LLMResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "call-2",
                        "type": "function",
                        "function": {
                            "name": "file_write",
                            "arguments": json.dumps({"path": "out.txt", "content": "right"}),
                        },
                    }
                ],
            )
        if calls == 5:
            return LLMResponse(content="FINAL: 已写入 right", finish_reason="stop")
        return LLMResponse(content="PASS: 工具结果支持最终答复", finish_reason="stop")

    monkeypatch.setattr("ming.core.agent.call_llm", fake_llm)

    agent = Agent(config=config, working_dir=str(tmp_path))
    result = await agent.chat("创建 out.txt，内容为 right")

    assert result == "已写入 right"
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "right"
    assert calls == 6


@pytest.mark.asyncio
async def test_web_research_builds_filtered_evidence_pack(tmp_path):
    tool = WebResearchTool(cache_root=tmp_path)

    async def fake_search(query, max_results=5, provider="auto", **kwargs):
        return {
            "query": query,
            "provider": "fake",
            "results": [
                {
                    "title": "Allowed Fresh",
                    "url": "https://docs.example.com/new",
                    "snippet": "fresh source 2026",
                    "score": 0.9,
                    "published": "2026-01-01",
                },
                {
                    "title": "Denied",
                    "url": "https://blog.example.net/old",
                    "snippet": "old source",
                    "score": 0.1,
                    "published": "2020-01-01",
                },
            ],
        }

    async def fake_fetch(url, max_chars=12000, **kwargs):
        return {
            "url": url,
            "title": "Allowed Fresh",
            "text": "This source explains tool evidence packs.",
            "content_type": "text/html",
        }

    tool._search = fake_search
    tool._fetch = fake_fetch

    result = await tool.execute(
        query="tool evidence",
        allow_domains=["docs.example.com"],
        deny_domains=["blog.example.net"],
        freshness_days=365,
        today="2026-06-04",
    )

    payload = json.loads(result.output)
    assert payload["citations"][0]["url"] == "https://docs.example.com/new"
    assert payload["evidence"][0]["quote"].startswith("This source")
    assert list(tmp_path.glob("web_research_*.json"))


def test_checkpoint_supports_named_resume_and_cleanup(tmp_path):
    store = CheckpointStore(tmp_path)
    first = store.save(
        "turn-a",
        [Message(role="user", content="hello")],
        tmp_path / "notes-a.md",
        todo={"items": []},
        changed_files=["a.txt"],
        name="first checkpoint",
    )
    second = store.save(
        "turn-b",
        [Message(role="user", content="bye")],
        tmp_path / "notes-b.md",
        todo={"items": []},
        changed_files=[],
        name="second checkpoint",
    )

    assert store.resolve("turn-a") == first
    assert store.resolve("latest") == second
    assert store.load(first)["messages_summary"] == "user: hello"
    assert store.cleanup(keep=1) == 1
    assert not first.exists()


@pytest.mark.asyncio
async def test_memory_store_extracts_session_and_project_memory_and_marks_stale(tmp_path):
    store = MemoryStore(tmp_path)
    await store.extract_session_summary(
        [
            Message(role="user", content="记住我喜欢 pytest"),
            Message(role="assistant", content="项目结构：src/ming/core 是核心"),
        ]
    )

    assert any(entry.type == "user" for entry in store.get_all())
    assert any(entry.type == "project" for entry in store.get_all())

    stale = store.save("old_fact", "old", "project", "旧事实")
    store.mark_stale(stale, reason="file changed")

    assert "stale" in stale.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_memory_store_uses_llm_structured_extraction_and_reload(tmp_path):
    store = MemoryStore(tmp_path)

    async def fake_llm(messages):
        assert "JSON 数组" in messages[0].content
        return LLMResponse(
            content=(
                '[{"type":"project","name":"core-path","description":"core path",'
                '"content":"src/ming/core is the orchestration layer"}]'
            ),
            finish_reason="stop",
        )

    saved = await store.extract_session_summary(
        [
            Message(role="user", content="我们项目里 src/ming/core 是 orchestration 入口。" * 5),
            Message(role="assistant", content="我会把这个当成项目事实。" * 5),
        ],
        llm_call=fake_llm,
    )

    assert len(saved) == 1
    assert store.get_all()[0].name == "core-path"

    (tmp_path / "external.md").write_text(
        "---\nname: external\ndescription: outside edit\ntype: project\n---\n\nmanual edit\n",
        encoding="utf-8",
    )
    assert store.reload_if_changed() is True
    assert any(entry.name == "external" for entry in store.get_all())


def test_skill_index_and_tool_need_proposal_are_metadata_only(tmp_path):
    index = SkillIndex(tmp_path)
    index.register(
        name="web_research",
        description="Build evidence packs",
        trust_level="local",
        allowed_tools=["web_search", "web_fetch"],
    )

    assert index.search("evidence")[0]["name"] == "web_research"
    proposal = ToolNeedProposal(
        need="pdf_extract",
        reason="Need stronger PDF parsing",
        tests=["test_pdf_extract_handles_text"],
    )

    assert proposal.to_dict()["status"] == "needs_human_approval"
