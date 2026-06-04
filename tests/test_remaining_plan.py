import json

import pytest

from ming.config import AgentConfig, LLMConfig, MingConfig
from ming.core.agent import Agent
from ming.core.llm import LLMResponse, Message
from ming.core.recovery import ErrorClassifier
from ming.core.trace import CheckpointStore, RunTrace
from ming.memory.store import MemoryStore
from ming.skills.index import SkillIndex, ToolNeedProposal
from ming.tools.web import WebResearchTool


def test_error_classifier_marks_retryable_and_irrecoverable_errors():
    classifier = ErrorClassifier()

    assert classifier.classify("TimeoutError: request timed out").retryable is True
    assert classifier.classify("[Permission denied] reset hard").recoverable is False
    assert classifier.classify("old_string not found in file.").category == "tool_input"


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


def test_trace_records_observations_and_expands_events(tmp_path):
    trace = RunTrace("turn-1", "创建文件")
    trace.add_observation("tool", "file_write produced evidence")
    trace.tool_events.append({"event_id": "evt-1", "tool_name": "file_write", "output": "hello"})

    path = trace.save(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["observations"][0]["summary"] == "file_write produced evidence"
    assert RunTrace.expand_event(path, "evt-1")["tool_name"] == "file_write"


def test_checkpoint_supports_named_resume_and_cleanup(tmp_path):
    store = CheckpointStore(tmp_path)
    first = store.save(
        "turn-a",
        [Message(role="user", content="hello")],
        tmp_path / "trace-a.json",
        tmp_path / "notes-a.md",
        todo={"items": []},
        changed_files=["a.txt"],
        name="first checkpoint",
    )
    second = store.save(
        "turn-b",
        [Message(role="user", content="bye")],
        tmp_path / "trace-b.json",
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


def test_memory_store_extracts_session_and_project_memory_and_marks_stale(tmp_path):
    store = MemoryStore(tmp_path)
    store.extract_session_summary(
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
