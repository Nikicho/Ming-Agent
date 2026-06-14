# ruff: noqa: E501
"""Local Ming Agent Workbench.

This stdlib HTTP app is the current Web UI stage described in the Ming design:
local Python server + JSON state + SSE events. It intentionally avoids a
frontend build chain while the product interaction is still changing quickly.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ming.core.live_events import LiveEventStore
from ming.ui.chat_runtime import ChatRuntime

WORKBENCH_SCHEMA_VERSION = "ming-workbench-v1"


class TraceConsoleState:
    """Build a UI-friendly snapshot from local Ming trace/checkpoint files."""

    def __init__(self, workspace_root: str | Path | None = None):
        self.workspace_root = Path(workspace_root or Path.cwd())
        self.ming_root = self.workspace_root / ".ming"

    def load(self) -> dict[str, Any]:
        session_trace_path = self._latest_file(self.ming_root / "session_traces", "*.json")
        checkpoint_path = self._latest_file(self.ming_root / "checkpoints", "*/checkpoint.json")
        session = self._read_json(session_trace_path)
        checkpoint = self._read_json(checkpoint_path)

        turn = self._latest_turn(session)
        turn_id = turn.get("turn_id") or checkpoint.get("turn_id") or ""
        task_text = turn.get("user_input") or checkpoint.get("name") or "暂无任务"
        state = self._agent_state(turn)
        timeline = self._build_timeline(turn)
        sessions = self._build_sessions(session, checkpoint)
        artifacts = self._build_artifacts(session_trace_path, checkpoint_path, checkpoint)
        context = self._build_context(session, turn, artifacts)
        trace_tabs = self._build_trace_tabs(session, turn, timeline, artifacts)

        process_panel = {
            "todo": checkpoint.get("todo") or {"items": []},
            "artifacts": artifacts,
            "context": context,
            "locked": False,
        }

        return {
            "schema_version": WORKBENCH_SCHEMA_VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "workspace": str(self.workspace_root),
            "sessions": sessions,
            "current_task": {
                "turn_id": turn_id,
                "text": task_text,
                "started_at": turn.get("timestamp") or checkpoint.get("created_at") or "",
                "status": state,
            },
            "agent": {
                "state": state,
                "mode": turn.get("execution", "single"),
                "summary": self._agent_summary(turn, timeline),
                "thought_summary": self._thought_summary(turn),
                "last_event": timeline[-1]["title"] if timeline else "暂无事件",
            },
            "process_panel": process_panel,
            "trace_tabs": trace_tabs,
            "todo": process_panel["todo"],
            "timeline": timeline,
            "subagents": self._subagents(turn, state),
            "artifacts": artifacts,
        }

    def _latest_turn(self, session: dict[str, Any]) -> dict[str, Any]:
        turns = session.get("turns") or []
        return turns[-1] if turns else {}

    def _build_sessions(
        self,
        session: dict[str, Any],
        checkpoint: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for turn in session.get("turns") or []:
            rows.append(
                {
                    "turn_id": turn.get("turn_id", ""),
                    "title": self._shorten(turn.get("user_input", "") or "未命名会话", 48),
                    "status": self._agent_state(turn),
                    "started_at": turn.get("timestamp", ""),
                }
            )
        if not rows and checkpoint:
            rows.append(
                {
                    "turn_id": checkpoint.get("turn_id", ""),
                    "title": checkpoint.get("name") or checkpoint.get("turn_id") or "未命名会话",
                    "status": "checkpoint",
                    "started_at": checkpoint.get("created_at", ""),
                }
            )
        return rows[-12:][::-1]

    def _build_timeline(self, turn: dict[str, Any]) -> list[dict[str, Any]]:
        if not turn:
            return [
                {
                    "id": "empty",
                    "kind": "empty",
                    "title": "等待首个 trace",
                    "status": "idle",
                    "summary": "运行一次 Ming 任务后，这里会展示 agent-loop 的步骤。",
                    "collapsed": False,
                    "details": {},
                }
            ]

        cards: list[dict[str, Any]] = [
            {
                "id": "task",
                "kind": "task",
                "title": "收到用户任务",
                "status": "done",
                "summary": turn.get("user_input", ""),
                "collapsed": False,
                "details": {
                    "turn_id": turn.get("turn_id"),
                    "started_at": turn.get("timestamp"),
                },
            }
        ]

        gate = turn.get("gate") or {}
        if gate.get("mode"):
            cards.append(
                {
                    "id": "gate",
                    "kind": "route",
                    "title": f"选择策略：{gate['mode']}",
                    "status": "done",
                    "summary": ", ".join(gate.get("triggered_rules", [])) or "无触发规则，走默认执行路径",
                    "collapsed": False,
                    "details": gate,
                }
            )

        single = turn.get("single_agent") or {}
        for step in single.get("steps", []):
            if step.get("response_content_length"):
                cards.append(
                    {
                        "id": f"thinking-{step.get('step_id')}",
                        "kind": "thinking",
                        "title": f"模型思考，第 {step.get('iteration')} 轮",
                        "status": "done" if step.get("is_final") else "running",
                        "summary": f"输出 {step.get('response_content_length', 0)} chars",
                        "collapsed": True,
                        "details": step,
                    }
                )
            for tc in step.get("tool_calls", []):
                cards.append(
                    {
                        "id": tc.get("id") or f"tool-{step.get('step_id', 0)}",
                        "kind": "tool",
                        "title": f"工具执行：{tc.get('name', 'unknown')}",
                        "status": "error" if tc.get("result_is_error") else "done",
                        "summary": (
                            f"loop={tc.get('loop_status', 'ok')}，"
                            f"输出 {tc.get('result_output_length', 0)} chars"
                        ),
                        "collapsed": True,
                        "details": {"tool_call": tc, "step": step},
                    }
                )

        adversarial = turn.get("adversarial") or {}
        if adversarial:
            cards.append(
                {
                    "id": "adversarial",
                    "kind": "verdict",
                    "title": "Ming 的判断分歧",
                    "status": "done",
                    "summary": (
                        f"观点 A {adversarial.get('alpha_output_length', 0)} chars，"
                        f"观点 B {adversarial.get('beta_output_length', 0)} chars，"
                        f"裁决 {adversarial.get('gamma_phase1_consistency', 'unknown')}"
                    ),
                    "collapsed": False,
                    "details": adversarial,
                }
            )

        if turn.get("error"):
            cards.append(
                {
                    "id": "exception",
                    "kind": "notice",
                    "title": "本轮遇到问题",
                    "status": "needs_attention",
                    "summary": self._shorten(turn.get("error", ""), 220),
                    "collapsed": False,
                    "details": {"error": turn.get("error")},
                }
            )

        if turn.get("final_output"):
            cards.append(
                {
                    "id": "final",
                    "kind": "final",
                    "title": "最终回复",
                    "status": "done",
                    "summary": self._shorten(turn.get("final_output", ""), 220),
                    "collapsed": False,
                    "details": {"final_output": turn.get("final_output", "")},
                }
            )

        return cards

    def _subagents(self, turn: dict[str, Any], state: str) -> list[dict[str, Any]]:
        adversarial = turn.get("adversarial") or {}
        is_adv = turn.get("execution") == "adversarial"
        main_status = "idle" if state == "idle" else state
        inactive = "本轮未触发对抗分支"
        return [
            {
                "name": "Ming Main",
                "role": "主循环",
                "status": main_status,
                "summary": self._main_lane_summary(turn),
            },
            {
                "name": "Alpha",
                "role": "观点 A",
                "status": "observed" if is_adv else "idle",
                "summary": f"{adversarial.get('alpha_output_length', 0)} chars" if is_adv else inactive,
            },
            {
                "name": "Beta",
                "role": "观点 B",
                "status": "observed" if is_adv else "idle",
                "summary": f"{adversarial.get('beta_output_length', 0)} chars" if is_adv else inactive,
            },
            {
                "name": "Gamma",
                "role": "裁决",
                "status": "observed" if is_adv else "idle",
                "summary": adversarial.get("gamma_phase1_consistency", inactive) if is_adv else inactive,
            },
        ]

    def _build_artifacts(
        self,
        session_trace_path: Path | None,
        checkpoint_path: Path | None,
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        session_trace_text = self._path_text(session_trace_path)
        return {
            "trace_path": session_trace_text,
            "session_trace_path": session_trace_text,
            "checkpoint_path": self._path_text(checkpoint_path),
            "notepad_path": checkpoint.get("notepad_path", ""),
            "changed_files": checkpoint.get("changed_files", []),
            "messages_summary": checkpoint.get("messages_summary", ""),
        }

    def _build_context(
        self,
        session: dict[str, Any],
        turn: dict[str, Any],
        artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        metrics = session.get("session_metrics") or {}
        turn_metrics = turn.get("turn_metrics") or {}
        return {
            "session_trace_path": artifacts["session_trace_path"],
            "schema_version": session.get("schema_version", ""),
            "total_turns": metrics.get("total_turns", 0),
            "total_llm_calls": metrics.get("total_llm_calls", 0),
            "total_prompt_tokens": metrics.get("total_prompt_tokens", 0),
            "total_completion_tokens": metrics.get("total_completion_tokens", 0),
            "turn_llm_calls": turn_metrics.get("total_llm_calls", 0),
            "turn_latency_ms": turn_metrics.get("total_latency_ms", 0),
            "estimated_cost_usd": turn_metrics.get("estimated_cost_usd", 0.0),
        }

    def _build_trace_tabs(
        self,
        session: dict[str, Any],
        turn: dict[str, Any],
        timeline: list[dict[str, Any]],
        artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "timeline": timeline,
            "exception": {
                "error": turn.get("error", ""),
                "notice": self._exception_notice(turn),
            },
            "session_trace": {
                "path": artifacts["session_trace_path"],
                "schema_version": session.get("schema_version", ""),
                "session_id": session.get("session_id", ""),
            },
            "settings": {
                "model": (session.get("agent") or {}).get("model", ""),
                "agent_version": (session.get("agent") or {}).get("version", ""),
            },
        }

    def _exception_notice(self, turn: dict[str, Any]) -> str:
        if turn.get("error"):
            return "本轮已经保存 trace/checkpoint。可查看详情后调整任务、换工具或继续。"
        single = turn.get("single_agent") or {}
        if single.get("l5_ceiling_hit"):
            return f"触发执行上限：{single['l5_ceiling_hit']}。Ming 已暂停，避免空转。"
        return "暂无异常。"

    def _agent_state(self, turn: dict[str, Any]) -> str:
        if not turn:
            return "idle"
        if turn.get("error"):
            return "blocked"
        if turn.get("final_output"):
            return "completed"
        return "running"

    def _agent_summary(self, turn: dict[str, Any], timeline: list[dict[str, Any]]) -> str:
        if not turn:
            return "还没有可展示的 Ming 运行记录。"
        if turn.get("final_output"):
            return self._shorten(turn.get("final_output", ""), 160)
        return timeline[-1]["summary"] if timeline else "正在等待下一步事件。"

    def _thought_summary(self, turn: dict[str, Any]) -> str:
        if not turn:
            return "暂无可公开思路摘要。"
        feedback = turn.get("feedback") or {}
        if feedback.get("tier_signal"):
            return f"tier={feedback['tier_signal']} automaticity={feedback.get('automaticity_after', '?')}"
        return "本轮没有额外观察记录；打开详情可查看结构化事件。"

    def _main_lane_summary(self, turn: dict[str, Any]) -> str:
        if not turn:
            return "等待任务。"
        single = turn.get("single_agent") or {}
        tool_count = sum(len(step.get("tool_calls", [])) for step in single.get("steps", []))
        step_count = len(single.get("steps", []))
        return f"已记录 {step_count} 步，{tool_count} 个工具调用。"

    def _latest_file(self, root: Path, pattern: str) -> Path | None:
        if not root.exists():
            return None
        files = sorted(root.glob(pattern), key=lambda path: path.stat().st_mtime)
        return files[-1] if files else None

    def _read_json(self, path: Path | None) -> dict[str, Any]:
        if not path or not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _path_text(self, path: Path | None) -> str:
        return str(path) if path else ""

    def _shorten(self, text: str, max_chars: int) -> str:
        clean = " ".join(str(text).split())
        if len(clean) <= max_chars:
            return clean
        return clean[: max_chars - 1] + "…"


class TraceConsoleApp:
    """Tiny stdlib HTTP app for the Ming Agent Workbench."""

    def __init__(self, workspace_root: str | Path | None = None, chat_runtime: Any | None = None):
        self.workspace_root = Path(workspace_root or Path.cwd())
        self.state_builder = TraceConsoleState(self.workspace_root)
        self.live_events = LiveEventStore(self.workspace_root / ".ming" / "live")
        self._chat_runtime = chat_runtime

    def state(self) -> dict[str, Any]:
        return self.state_builder.load()

    def state_json(self) -> str:
        return json.dumps(self.state(), ensure_ascii=False, indent=2)

    def render_index(self) -> str:
        return INDEX_HTML

    def chat_runtime(self):
        if self._chat_runtime is None:
            self._chat_runtime = ChatRuntime(self.workspace_root, live_events=self.live_events)
        return self._chat_runtime

    def submit_chat(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        message = str(payload.get("message") or "").strip()
        if not message:
            return 400, {"status": "invalid", "error": "message is required"}
        result = self.chat_runtime().submit(message)
        if result.get("status") == "busy":
            return 409, result
        if result.get("status") == "invalid":
            return 400, result
        return 202, result

    def stop_current_turn(self) -> tuple[int, dict[str, Any]]:
        result = self.chat_runtime().stop()
        if result.get("status") == "idle":
            return 409, result
        return 200, result

    def format_sse(self, event: dict[str, Any]) -> str:
        event_name = str(event.get("stage") or event.get("type") or "message")
        data = json.dumps(event, ensure_ascii=False)
        return f"id: {event.get('seq', 0)}\nevent: {event_name}\ndata: {data}\n\n"

    def default_event_start_seq(self) -> int:
        events = self.live_events.since(0)
        if not events:
            return 0
        return max(int(event.get("seq", 0)) for event in events)

    def event_stream(
        self,
        last_seq: int = 0,
        poll_seconds: float = 1.0,
        heartbeat_seconds: float = 10.0,
    ):
        last_heartbeat = time.monotonic()
        while True:
            events = self.live_events.since(last_seq)
            for event in events:
                last_seq = max(last_seq, int(event.get("seq", 0)))
                yield self.format_sse(event)
            if poll_seconds <= 0:
                return
            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_seconds:
                last_heartbeat = now
                yield self.format_sse(
                    {
                        "seq": last_seq,
                        "time": datetime.now().isoformat(timespec="seconds"),
                        "turn_id": "",
                        "stage": "heartbeat",
                        "message": "keep-alive",
                        "detail": "",
                        "type": "heartbeat",
                    }
                )
            time.sleep(poll_seconds)

    def serve(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        app = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path in {"/", "/index.html"}:
                    self._send(200, app.render_index(), "text/html; charset=utf-8")
                    return
                if path == "/api/state":
                    self._send(200, app.state_json(), "application/json; charset=utf-8")
                    return
                if path == "/api/events":
                    self._send_sse()
                    return
                self._send(404, "Not found", "text/plain; charset=utf-8")

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                if path == "/api/chat":
                    status, payload = app.submit_chat(self._read_json_body())
                    self._send_json(status, payload)
                    return
                if path == "/api/turns/current/stop":
                    status, payload = app.stop_current_turn()
                    self._send_json(status, payload)
                    return
                self._send(404, "Not found", "text/plain; charset=utf-8")

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send(self, status: int, body: str, content_type: str) -> None:
                payload = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                self._send(
                    status,
                    json.dumps(payload, ensure_ascii=False),
                    "application/json; charset=utf-8",
                )

            def _read_json_body(self) -> dict[str, Any]:
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                except ValueError:
                    length = 0
                if length <= 0:
                    return {}
                try:
                    raw = self.rfile.read(length).decode("utf-8")
                    payload = json.loads(raw)
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    return {}
                return payload if isinstance(payload, dict) else {}

            def _send_sse(self) -> None:
                last_event_id = self.headers.get("Last-Event-ID")
                if last_event_id is None:
                    last_seq = app.default_event_start_seq()
                else:
                    try:
                        last_seq = int(last_event_id or "0")
                    except ValueError:
                        last_seq = 0
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    for chunk in app.event_stream(last_seq=last_seq):
                        self.wfile.write(chunk.encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return

        server = ThreadingHTTPServer((host, port), Handler)
        try:
            print(f"Ming Agent Workbench: http://{host}:{port}")
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nMing Agent Workbench stopped.")
        finally:
            server.server_close()


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ming Agent Workbench</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f6f8;
      --panel: #ffffff;
      --line: #d8dee8;
      --text: #182230;
      --muted: #667085;
      --brand: #0f766e;
      --brand-weak: #ecfdf5;
      --attention: #b42318;
      --warn: #b54708;
      --ok: #067647;
      --focus: #175cd3;
      --shadow: 0 12px 32px rgba(16, 24, 40, .07);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, "Segoe UI", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
      letter-spacing: 0;
    }
    button, textarea { font: inherit; }
    button {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      border-radius: 6px;
      min-height: 34px;
      padding: 7px 10px;
      cursor: pointer;
    }
    button.icon { width: 36px; padding: 0; display: inline-grid; place-items: center; }
    button.primary { background: var(--brand); border-color: var(--brand); color: #fff; }
    button:disabled { opacity: .5; cursor: not-allowed; }
    .topbar {
      height: 58px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 0 16px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, .96);
      position: sticky;
      top: 0;
      z-index: 10;
    }
    .brand { display: flex; align-items: center; gap: 10px; min-width: 0; }
    .brand h1 { margin: 0; font-size: 17px; line-height: 1.2; }
    .meta, .muted { color: var(--muted); font-size: 12px; line-height: 1.45; overflow-wrap: anywhere; }
    .top-actions { display: flex; align-items: center; gap: 8px; }
    .state-pill {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      background: #fff;
      font-size: 12px;
      white-space: nowrap;
    }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
    .dot.running { background: var(--focus); }
    .dot.completed { background: var(--ok); }
    .dot.blocked { background: var(--attention); }
    .app-shell {
      display: grid;
      grid-template-columns: 52px minmax(0, 1fr) minmax(320px, 390px);
      min-height: calc(100vh - 58px);
    }
    .session-rail, .process-rail {
      border-right: 1px solid var(--line);
      background: #fff;
      min-width: 0;
    }
    .session-rail {
      overflow: hidden;
      transition: width .16s ease;
    }
    .session-rail.expanded { width: 260px; }
    .session-rail:not(.expanded) .rail-body { display: none; }
    .rail-toolbar {
      height: 52px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-bottom: 1px solid var(--line);
    }
    .rail-body { padding: 12px; }
    .session-item {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px;
      margin-bottom: 8px;
      background: #fff;
    }
    .workspace {
      min-width: 0;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      padding: 14px;
      gap: 12px;
    }
    .task-strip {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 12px;
    }
    .task-title { font-weight: 700; margin-bottom: 5px; }
    .conversation {
      display: grid;
      gap: 10px;
      overflow: auto;
      min-height: 260px;
      padding: 2px 2px 8px;
    }
    .message {
      max-width: 860px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 10px 12px;
      line-height: 1.55;
      font-size: 14px;
      overflow-wrap: anywhere;
    }
    .message.user { justify-self: end; background: #f0fdfa; border-color: #99f6e4; }
    .message.assistant { justify-self: start; background: #eff6ff; border-color: #bfdbfe; }
    .message.event, .message.system { justify-self: center; color: var(--muted); font-size: 12px; }
    .composer {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      box-shadow: var(--shadow);
      padding: 10px;
    }
    textarea {
      width: 100%;
      min-height: 74px;
      max-height: 220px;
      resize: vertical;
      border: 0;
      outline: none;
      line-height: 1.5;
    }
    .composer-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; border-top: 1px solid var(--line); padding-top: 9px; }
    .process-rail {
      border-right: 0;
      border-left: 1px solid var(--line);
      padding: 14px;
      overflow: auto;
    }
    .panel-block { margin-bottom: 14px; }
    .panel-title { font-size: 13px; font-weight: 700; margin: 0 0 8px; }
    .todo-item {
      display: grid;
      grid-template-columns: 18px 1fr;
      gap: 8px;
      align-items: start;
      margin-bottom: 8px;
      font-size: 13px;
      line-height: 1.45;
    }
    .check {
      width: 16px;
      height: 16px;
      border-radius: 4px;
      border: 1px solid var(--line);
      background: #fff;
      display: inline-grid;
      place-items: center;
      color: #fff;
      font-size: 11px;
    }
    .check.completed { background: var(--ok); border-color: var(--ok); }
    .check.in_progress { background: var(--warn); border-color: var(--warn); }
    .progress-list { display: grid; gap: 9px; }
    .progress-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fff;
    }
    .progress-card.active { border-color: var(--brand); background: var(--brand-weak); }
    .progress-card.notice { border-color: #fda29b; background: #fffafa; }
    .card-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .card-title { font-size: 13px; font-weight: 700; }
    .pill { border: 1px solid var(--line); border-radius: 999px; padding: 3px 8px; font-size: 12px; color: var(--muted); white-space: nowrap; }
    .summary { margin-top: 7px; color: #344054; font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
    .tool-details { display: none; margin-top: 8px; }
    .progress-card.expanded .tool-details { display: block; }
    .subagent { border: 1px solid var(--line); border-radius: 8px; padding: 9px; margin-bottom: 8px; background: #fff; }
    .subagent-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-weight: 700; font-size: 13px; }
    .live-events { display: grid; gap: 8px; max-height: 180px; overflow: auto; }
    .live-event { border: 1px solid var(--line); border-radius: 8px; padding: 8px; background: #fff; font-size: 12px; line-height: 1.45; }
    .live-event.error, .live-event.cancelled { border-color: #fda29b; }
    pre {
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: #101828;
      color: #e5e7eb;
      padding: 10px;
      border-radius: 8px;
      font-size: 12px;
      max-height: 240px;
      overflow: auto;
    }
    .modal {
      position: fixed;
      inset: 0;
      background: rgba(15, 23, 42, .34);
      display: none;
      align-items: center;
      justify-content: center;
      padding: 22px;
      z-index: 20;
    }
    .modal.open { display: flex; }
    .modal-panel {
      width: min(920px, 100%);
      max-height: 86vh;
      overflow: hidden;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 24px 70px rgba(16, 24, 40, .18);
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr);
    }
    .modal-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px; border-bottom: 1px solid var(--line); }
    .tabs { display: flex; gap: 6px; padding: 10px 14px; border-bottom: 1px solid var(--line); overflow: auto; }
    .tab.active { background: var(--brand); border-color: var(--brand); color: #fff; }
    .modal-body { padding: 14px; overflow: auto; }
    @media (max-width: 1060px) {
      .app-shell { grid-template-columns: 1fr; }
      .session-rail { display: none; }
      .process-rail { border-left: 0; border-top: 1px solid var(--line); }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand">
      <button class="icon" id="toggleSessionsBtn" type="button" title="会话列表">☰</button>
      <div>
        <h1>Ming Agent Workbench</h1>
        <div class="meta">Ming 任务工作台 · Ming 的判断分歧只在需要人类裁决时展示</div>
        <div class="meta" id="workspace"></div>
      </div>
    </div>
    <div class="top-actions">
      <span class="state-pill"><span class="dot" id="stateDot"></span><span id="stateText">loading</span></span>
      <button class="icon" id="brainBtn" type="button" title="查看大脑详情">◎</button>
      <button class="icon" id="processBtn" type="button" title="过程面板">▣</button>
      <button class="icon" id="settingsBtn" type="button" title="设置与模型">⚙</button>
      <button id="refreshBtn" type="button">刷新</button>
    </div>
  </header>

  <main class="app-shell">
    <aside class="session-rail" id="sessionRail">
      <div class="rail-toolbar"><button class="icon" type="button" id="newChatBtn" title="新会话">＋</button></div>
      <div class="rail-body">
        <div class="panel-title">会话</div>
        <div id="sessionList"></div>
      </div>
    </aside>

    <section class="workspace" id="mainWorkspace">
      <div class="task-strip">
        <div class="task-title" id="taskText">暂无任务</div>
        <div class="meta" id="taskMeta"></div>
      </div>
      <div class="conversation" id="conversation"></div>
      <form class="composer" id="chatForm">
        <textarea id="messageInput" name="message" placeholder="输入任务，Ming 会在本地运行。Enter 发送，Shift+Enter 换行。"></textarea>
        <div class="composer-actions">
          <span class="meta" id="chatStatus">ready</span>
          <button class="primary" id="sendBtn" type="submit">发送</button>
          <button id="stopTurnBtn" type="button" disabled>停止思考</button>
        </div>
      </form>
    </section>

    <aside class="process-rail" id="processRail">
      <section class="panel-block">
        <p class="panel-title">Agent 状态</p>
        <div class="summary" id="agentSummary"></div>
      </section>
      <section class="panel-block">
        <p class="panel-title">执行过程</p>
        <div class="progress-list" id="runTimeline"></div>
      </section>
      <section class="panel-block">
        <p class="panel-title">TODO</p>
        <div id="todoList"></div>
      </section>
      <section class="panel-block">
        <p class="panel-title">Artifacts</p>
        <pre id="artifacts"></pre>
      </section>
      <section class="panel-block">
        <p class="panel-title">Context / Token</p>
        <pre id="contextPanel"></pre>
      </section>
      <section class="panel-block">
        <p class="panel-title">可公开思考摘要</p>
        <div class="summary" id="thoughtSummary"></div>
      </section>
      <section class="panel-block">
        <p class="panel-title">Subagents</p>
        <div id="subagents"></div>
      </section>
      <section class="panel-block">
        <p class="panel-title">SSE 实时事件</p>
        <div class="meta" id="liveStatus">connecting</div>
        <div class="live-events" id="liveEvents"></div>
      </section>
    </aside>
  </main>

  <div class="modal" id="brainModal" role="dialog" aria-modal="true">
    <div class="modal-panel">
      <div class="modal-head">
        <strong>诊断详情</strong>
        <button id="closeModalBtn" type="button">关闭</button>
      </div>
      <div class="tabs">
        <button class="tab active" data-tab="timeline" type="button">做了什么</button>
        <button class="tab" data-tab="exception" type="button">异常原因</button>
        <button class="tab" data-tab="session_trace" type="button">SessionTrace</button>
        <button class="tab" data-tab="settings" type="button">设置与模型</button>
      </div>
      <div class="modal-body"><pre id="details">点击执行过程中的步骤，查看结构化详情。</pre></div>
    </div>
  </div>

  <script>
    let selectedId = "";
    let stateTimeline = [];
    let traceTabs = {};
    const conversation = [];
    const liveEvents = [];
    const liveRunEvents = [];

    async function loadState() {
      const response = await fetch("/api/state", { cache: "no-store" });
      const state = await response.json();
      render(state);
    }

    function render(state) {
      document.getElementById("workspace").textContent = state.workspace;
      document.getElementById("taskText").textContent = state.current_task.text;
      document.getElementById("taskMeta").textContent =
        `${state.current_task.turn_id || "no turn"} · ${state.current_task.started_at || "no time"}`;
      document.getElementById("stateText").textContent = `${state.agent.state} · ${state.agent.mode}`;
      const dot = document.getElementById("stateDot");
      dot.className = `dot ${state.agent.state}`;
      document.getElementById("agentSummary").textContent = state.agent.summary;
      document.getElementById("thoughtSummary").textContent = state.agent.thought_summary;
      stateTimeline = state.timeline || [];
      traceTabs = state.trace_tabs || {};
      renderSessions(state.sessions || []);
      renderTodo((state.process_panel && state.process_panel.todo.items) || (state.todo && state.todo.items) || []);
      renderRunTimeline();
      renderSubagents(state.subagents || []);
      renderArtifacts((state.process_panel && state.process_panel.artifacts) || state.artifacts || {});
      renderContext((state.process_panel && state.process_panel.context) || {});
    }

    function renderSessions(sessions) {
      const root = document.getElementById("sessionList");
      root.innerHTML = "";
      if (!sessions.length) {
        root.innerHTML = `<div class="muted">暂无历史会话</div>`;
        return;
      }
      for (const session of sessions) {
        const node = document.createElement("div");
        node.className = "session-item";
        node.innerHTML =
          `<strong>${escapeHtml(session.title)}</strong>` +
          `<div class="meta">${escapeHtml(session.turn_id)} · ${escapeHtml(session.status)}</div>`;
        root.appendChild(node);
      }
    }

    function renderTodo(items) {
      const root = document.getElementById("todoList");
      root.innerHTML = "";
      if (!items.length) {
        root.innerHTML = `<div class="muted">暂无 TODO</div>`;
        return;
      }
      for (const item of items) {
        const row = document.createElement("div");
        row.className = "todo-item";
        row.innerHTML =
          `<span class="check ${escapeHtml(item.status)}">${item.status === "completed" ? "✓" : ""}</span>` +
          `<span>${escapeHtml(item.text)}<br><span class="muted">${escapeHtml(item.status)}</span></span>`;
        root.appendChild(row);
      }
    }

    function renderRunTimeline(cards) {
      const root = document.getElementById("runTimeline");
      const data = cards || (liveRunEvents.length ? liveRunEvents : stateTimeline);
      root.innerHTML = "";
      for (const card of data) {
        root.appendChild(renderProgressCard(card, data));
      }
    }

    function renderProgressCard(card, allCards) {
      const node = document.createElement("article");
      node.className = `progress-card ${card.kind === "notice" ? "notice" : ""} ${selectedId === card.id ? "active" : ""}`;
      node.dataset.cardId = card.id;
      node.innerHTML =
        `<div class="card-head"><span class="card-title">${escapeHtml(card.title)}</span>` +
        `<button class="pill" type="button">${escapeHtml(card.kind)} · ${escapeHtml(card.status)}</button></div>` +
        `<div class="summary">${escapeHtml(card.summary)}</div>` +
        `<pre class="tool-details">${escapeHtml(JSON.stringify(card.details || {}, null, 2))}</pre>`;
      node.addEventListener("click", () => {
        selectedId = card.id;
        document.getElementById("brainModal").classList.add("open");
        document.getElementById("details").textContent = JSON.stringify(card.details || {}, null, 2);
        if (card.kind === "tool" || card.collapsed) {
          toggleToolCard(node);
        }
        renderRunTimeline(allCards);
      });
      return node;
    }

    function toggleToolCard(node) {
      node.classList.toggle("expanded");
    }

    function renderSubagents(agents) {
      const root = document.getElementById("subagents");
      root.innerHTML = "";
      for (const agent of agents) {
        const lane = document.createElement("div");
        lane.className = "subagent";
        lane.innerHTML =
          `<div class="subagent-head"><span>${escapeHtml(agent.name)}</span><span class="pill">${escapeHtml(agent.status)}</span></div>` +
          `<div class="meta">${escapeHtml(agent.role)}</div>` +
          `<div class="summary">${escapeHtml(agent.summary)}</div>`;
        root.appendChild(lane);
      }
    }

    function renderArtifacts(artifacts) {
      document.getElementById("artifacts").textContent = JSON.stringify(artifacts, null, 2);
    }

    function renderContext(context) {
      document.getElementById("contextPanel").textContent = JSON.stringify(context, null, 2);
    }

    async function submitChat(event) {
      event.preventDefault();
      const input = document.getElementById("messageInput");
      const message = input.value.trim();
      if (!message) return;
      appendConversation("user", message);
      setChatRunning(true, "submitting");
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      const payload = await response.json();
      if (response.status === 202) {
        input.value = "";
        setChatRunning(true, `running ${payload.turn_id}`);
        appendConversation("event", `turn ${payload.turn_id} started`);
      } else {
        setChatRunning(false, payload.status || "error");
        appendConversation("system", payload.error || payload.status || "submit failed");
      }
    }

    async function stopTurn() {
      setChatRunning(true, "stopping");
      const response = await fetch("/api/turns/current/stop", { method: "POST" });
      const payload = await response.json();
      if (response.status === 200) {
        appendConversation("system", "已停止本轮思考");
      }
      setChatRunning(false, payload.status || "idle");
    }

    function setChatRunning(running, label) {
      document.getElementById("chatStatus").textContent = label;
      document.getElementById("sendBtn").disabled = running;
      document.getElementById("stopTurnBtn").disabled = !running;
    }

    function appendConversation(role, content) {
      conversation.push({ role, content });
      if (conversation.length > 80) conversation.shift();
      renderConversation();
    }

    function renderConversation() {
      const root = document.getElementById("conversation");
      root.innerHTML = "";
      if (!conversation.length) {
        root.innerHTML = `<div class="message system">等待输入任务。</div>`;
        return;
      }
      for (const item of conversation) {
        const node = document.createElement("div");
        node.className = `message ${escapeHtml(item.role)}`;
        node.textContent = item.content;
        root.appendChild(node);
      }
      root.scrollTop = root.scrollHeight;
    }

    function formatRunEvent(event) {
      const labels = {
        submitted: "收到任务",
        context: "整理上下文",
        route: "选择策略",
        llm: "模型思考",
        tool: "工具执行",
        verify: "校验结果",
        done: "保存进度",
        final: "最终回复",
        error: "遇到问题",
        cancelled: "已停止",
      };
      const stage = event.stage || event.type || "event";
      return {
        id: `live-${event.seq || Date.now()}-${stage}`,
        kind: stage,
        title: labels[stage] || stage,
        status: stage === "error" ? "needs_attention" : stage === "final" ? "done" : "running",
        summary: event.message || event.detail || "",
        collapsed: stage === "tool",
        details: event,
      };
    }

    function appendRunEvent(event) {
      const card = formatRunEvent(event);
      if (!liveRunEvents.some(item => item.id === card.id)) liveRunEvents.push(card);
      if (liveRunEvents.length > 80) liveRunEvents.shift();
      renderRunTimeline();
    }

    function handleConversationEvent(event) {
      appendRunEvent(event);
      if (event.stage === "submitted") {
        appendConversation("event", `收到任务：${event.message}`);
        return;
      }
      if (event.stage === "final") {
        appendConversation("assistant", event.detail || event.message);
        setChatRunning(false, "ready");
        loadState();
        return;
      }
      if (event.stage === "error") {
        appendConversation("system", event.detail || event.message);
        setChatRunning(false, "error");
        loadState();
        return;
      }
      if (event.stage === "cancelled") {
        appendConversation("system", event.message);
        setChatRunning(false, "cancelled");
        loadState();
        return;
      }
      if (["context", "route", "llm", "tool", "verify", "done"].includes(event.stage)) {
        const card = formatRunEvent(event);
        appendConversation("event", `${card.title}：${event.message}`);
        if (event.stage === "done") loadState();
      }
    }

    function connectLiveEvents() {
      const status = document.getElementById("liveStatus");
      const source = new EventSource("/api/events");
      const stages = ["submitted", "context", "route", "llm", "tool", "verify", "done", "final", "error", "cancelled", "heartbeat"];
      source.onopen = () => { status.textContent = "connected"; };
      source.onerror = () => { status.textContent = "reconnecting"; };
      for (const stage of stages) {
        source.addEventListener(stage, event => {
          const payload = JSON.parse(event.data);
          if (payload.stage !== "heartbeat") {
            appendLiveEvent(payload);
            handleConversationEvent(payload);
          }
        });
      }
    }

    function appendLiveEvent(event) {
      liveEvents.unshift(event);
      if (liveEvents.length > 20) liveEvents.pop();
      renderLiveEvents();
    }

    function renderLiveEvents() {
      const root = document.getElementById("liveEvents");
      root.innerHTML = "";
      if (!liveEvents.length) {
        root.innerHTML = `<div class="muted">等待下一条 live event。</div>`;
        return;
      }
      for (const event of liveEvents) {
        const node = document.createElement("div");
        node.className = `live-event ${escapeHtml(event.stage)}`;
        node.innerHTML =
          `<strong>${escapeHtml(event.stage)} · ${escapeHtml(event.message)}</strong>` +
          `<div class="meta">${escapeHtml(event.turn_id || "no turn")} · #${escapeHtml(event.seq)}</div>` +
          (event.detail ? `<div class="meta">${escapeHtml(event.detail)}</div>` : "");
        root.appendChild(node);
      }
    }

    function activateTab(tabName) {
      document.querySelectorAll(".tab").forEach(tab => {
        tab.classList.toggle("active", tab.dataset.tab === tabName);
      });
      document.getElementById("details").textContent =
        JSON.stringify((traceTabs && traceTabs[tabName]) || {}, null, 2);
    }

    function escapeHtml(value) {
      return text(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function text(value) {
      return value === undefined || value === null ? "" : String(value);
    }

    document.getElementById("toggleSessionsBtn").addEventListener("click", () => {
      document.getElementById("sessionRail").classList.toggle("expanded");
    });
    document.getElementById("processBtn").addEventListener("click", () => {
      document.getElementById("processRail").hidden = !document.getElementById("processRail").hidden;
    });
    document.getElementById("brainBtn").addEventListener("click", () => {
      document.getElementById("brainModal").classList.add("open");
      activateTab("timeline");
    });
    document.getElementById("settingsBtn").addEventListener("click", () => {
      document.getElementById("brainModal").classList.add("open");
      activateTab("settings");
    });
    document.getElementById("closeModalBtn").addEventListener("click", () => {
      document.getElementById("brainModal").classList.remove("open");
    });
    document.querySelectorAll(".tab").forEach(tab => {
      tab.addEventListener("click", () => activateTab(tab.dataset.tab));
    });
    document.getElementById("messageInput").addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        document.getElementById("chatForm").requestSubmit();
      }
    });
    document.getElementById("refreshBtn").addEventListener("click", loadState);
    document.getElementById("chatForm").addEventListener("submit", submitChat);
    document.getElementById("stopTurnBtn").addEventListener("click", stopTurn);
    renderConversation();
    renderLiveEvents();
    connectLiveEvents();
    loadState();
    setInterval(loadState, 1800);
  </script>
</body>
</html>
"""
