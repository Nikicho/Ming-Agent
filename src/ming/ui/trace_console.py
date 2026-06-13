# ruff: noqa: E501
"""Local Trace Console for visualizing recent Ming agent turns."""

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


class TraceConsoleState:
    """Build a UI-friendly snapshot from local Ming trace/checkpoint files."""

    def __init__(self, workspace_root: str | Path | None = None):
        self.workspace_root = Path(workspace_root or Path.cwd())
        self.ming_root = self.workspace_root / ".ming"

    def load(self) -> dict[str, Any]:
        trace_path = self._latest_file(self.ming_root / "traces", "*.json")
        checkpoint_path = self._latest_file(self.ming_root / "checkpoints", "*/checkpoint.json")
        trace = self._read_json(trace_path)
        checkpoint = self._read_json(checkpoint_path)

        turn_id = trace.get("turn_id") or checkpoint.get("turn_id") or ""
        task_text = trace.get("user_input") or checkpoint.get("name") or "暂无任务"
        latest_assessment = self._latest_assessment(trace)
        state = self._agent_state(trace, latest_assessment)
        timeline = self._build_timeline(trace)

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "workspace": str(self.workspace_root),
            "current_task": {
                "turn_id": turn_id,
                "text": task_text,
                "started_at": trace.get("started_at") or checkpoint.get("created_at") or "",
                "status": state,
            },
            "agent": {
                "state": state,
                "mode": self._agent_mode(trace),
                "summary": self._agent_summary(trace, latest_assessment, timeline),
                "thought_summary": self._thought_summary(trace, latest_assessment),
                "last_event": timeline[-1]["title"] if timeline else "暂无事件",
            },
            "todo": checkpoint.get("todo") or {"items": []},
            "timeline": timeline,
            "subagents": self._subagents(trace, state),
            "artifacts": {
                "trace_path": self._path_text(trace_path),
                "checkpoint_path": self._path_text(checkpoint_path),
                "notepad_path": checkpoint.get("notepad_path", ""),
                "changed_files": checkpoint.get("changed_files", []),
                "messages_summary": checkpoint.get("messages_summary", ""),
            },
        }

    def _build_timeline(self, trace: dict[str, Any]) -> list[dict[str, Any]]:
        if not trace:
            return [{
                "id": "empty",
                "kind": "empty",
                "title": "等待首个 trace",
                "status": "idle",
                "summary": "运行一次 Ming 任务后，这里会展示 agent-loop 的步骤。",
                "details": {},
            }]

        cards: list[dict[str, Any]] = [{
            "id": "task",
            "kind": "task",
            "title": "收到用户任务",
            "status": "done",
            "summary": trace.get("user_input", ""),
            "details": {
                "turn_id": trace.get("turn_id"),
                "started_at": trace.get("started_at"),
            },
        }]

        assessments = trace.get("assessments") or []
        for index, event in enumerate(trace.get("tool_events") or []):
            event_id = event.get("event_id") or f"tool-{index + 1}"
            assessment = assessments[index] if index < len(assessments) else {}
            title = f"工具 {event.get('tool_name', 'unknown')}"
            summary = (
                f"{event.get('action', '')} | {event.get('progress', 'unknown')} | "
                f"evidence={event.get('evidence_count', 0)}"
            )
            cards.append({
                "id": event_id,
                "kind": "tool",
                "title": title,
                "status": event.get("status", "unknown"),
                "summary": summary,
                "details": {
                    "event": event,
                    "assessment": assessment,
                },
            })

        for index, observation in enumerate(trace.get("observations") or []):
            cards.append({
                "id": f"obs-{index + 1}",
                "kind": "observation",
                "title": f"观察 {observation.get('kind', 'note')}",
                "status": "noted",
                "summary": observation.get("summary", ""),
                "details": observation,
            })

        for index, assessment in enumerate(assessments):
            cards.append({
                "id": f"assess-{index + 1}",
                "kind": "assessment",
                "title": f"进展判断 {assessment.get('decision', 'unknown')}",
                "status": assessment.get("decision", "unknown"),
                "summary": assessment.get("reason", ""),
                "details": assessment,
            })

        if trace.get("final_output"):
            cards.append({
                "id": "final",
                "kind": "final",
                "title": "最终回复",
                "status": "done",
                "summary": self._shorten(trace.get("final_output", ""), 220),
                "details": {"final_output": trace.get("final_output", "")},
            })

        return cards

    def _subagents(self, trace: dict[str, Any], state: str) -> list[dict[str, Any]]:
        observations = trace.get("observations") or []
        alpha = self._find_observation(observations, {"alpha", "α", "伪"})
        beta = self._find_observation(observations, {"beta", "β", "尾"})
        gamma = self._find_observation(observations, {"gamma", "γ", "纬"})
        main_status = "idle" if state == "idle" else state
        return [
            {
                "name": "Ming Main",
                "role": "主循环",
                "status": main_status,
                "summary": self._main_lane_summary(trace),
            },
            {
                "name": "Alpha",
                "role": "正向方案",
                "status": "observed" if alpha else "idle",
                "summary": alpha or "本轮未触发对抗分支。",
            },
            {
                "name": "Beta",
                "role": "反向审查",
                "status": "observed" if beta else "idle",
                "summary": beta or "本轮未触发对抗分支。",
            },
            {
                "name": "Gamma",
                "role": "收敛裁决",
                "status": "observed" if gamma else "idle",
                "summary": gamma or "本轮未触发对抗分支。",
            },
        ]

    def _agent_state(
        self,
        trace: dict[str, Any],
        latest_assessment: dict[str, Any],
    ) -> str:
        if not trace:
            return "idle"
        if latest_assessment.get("decision") == "stop":
            return "blocked"
        if trace.get("final_output"):
            return "completed"
        return "running"

    def _agent_mode(self, trace: dict[str, Any]) -> str:
        kinds = {str(item.get("kind", "")).lower() for item in trace.get("observations", [])}
        if kinds & {"alpha", "beta", "gamma", "α", "β", "γ", "伪", "尾", "纬"}:
            return "adversarial"
        return "single"

    def _agent_summary(
        self,
        trace: dict[str, Any],
        latest_assessment: dict[str, Any],
        timeline: list[dict[str, Any]],
    ) -> str:
        if not trace:
            return "还没有可展示的 Ming 运行记录。"
        if latest_assessment:
            return self._shorten(latest_assessment.get("reason", ""), 160)
        if trace.get("final_output"):
            return self._shorten(trace.get("final_output", ""), 160)
        return timeline[-1]["summary"] if timeline else "正在等待下一步事件。"

    def _thought_summary(
        self,
        trace: dict[str, Any],
        latest_assessment: dict[str, Any],
    ) -> str:
        if not trace:
            return "暂无可公开思路摘要。"
        if latest_assessment:
            return self._shorten(latest_assessment.get("reason", ""), 180)
        observations = trace.get("observations") or []
        if observations:
            return self._shorten(observations[-1].get("summary", ""), 180)
        return "本轮没有额外观察记录；请打开详情查看结构化事件。"

    def _main_lane_summary(self, trace: dict[str, Any]) -> str:
        if not trace:
            return "等待任务。"
        tool_count = len(trace.get("tool_events") or [])
        obs_count = len(trace.get("observations") or [])
        return f"已记录 {tool_count} 个工具事件，{obs_count} 条观察。"

    def _latest_assessment(self, trace: dict[str, Any]) -> dict[str, Any]:
        assessments = trace.get("assessments") or []
        return assessments[-1] if assessments else {}

    def _find_observation(self, observations: list[dict[str, Any]], names: set[str]) -> str:
        for observation in observations:
            kind = str(observation.get("kind", "")).lower()
            if kind in names:
                return self._shorten(str(observation.get("summary", "")), 160)
        return ""

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
    """Tiny stdlib HTTP app for the Trace Console."""

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
                heartbeat = {
                    "seq": last_seq,
                    "time": datetime.now().isoformat(timespec="seconds"),
                    "turn_id": "",
                    "stage": "heartbeat",
                    "message": "keep-alive",
                    "detail": "",
                    "type": "heartbeat",
                }
                yield self.format_sse(heartbeat)
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
            print(f"Ming Trace Console: http://{host}:{port}")
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nMing Trace Console stopped.")
        finally:
            server.server_close()


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ming 任务工作台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --line: #d9dee7;
      --text: #18202b;
      --muted: #667085;
      --brand: #0f766e;
      --accent: #b45309;
      --bad: #b42318;
      --good: #047857;
      --soft: #ecfdf5;
      --shadow: 0 12px 30px rgba(15, 23, 42, .08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, "Segoe UI", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      height: 72px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 28px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, .92);
      position: sticky;
      top: 0;
      z-index: 10;
    }
    h1 { margin: 0; font-size: 20px; font-weight: 700; }
    h2 { margin: 0 0 12px; font-size: 15px; }
    button {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      border-radius: 6px;
      padding: 8px 10px;
      cursor: pointer;
    }
    .status {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 7px 11px;
      background: #fff;
      font-size: 13px;
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--muted);
    }
    .dot.completed { background: var(--good); }
    .dot.running { background: var(--brand); }
    .dot.blocked { background: var(--bad); }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(320px, 400px);
      gap: 18px;
      padding: 18px;
      max-width: 1500px;
      margin: 0 auto;
    }
    section, aside {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 16px;
      min-width: 0;
    }
    .overview-panel {
      grid-column: 1 / -1;
      display: grid;
      grid-template-columns: minmax(280px, 1.4fr) minmax(220px, .8fr) minmax(260px, 1fr);
      gap: 16px;
    }
    .overview-panel .task,
    .overview-panel .detail {
      margin: 0;
      padding: 0;
      border: 0;
    }
    .workbench {
      min-height: calc(100vh - 190px);
    }
    .diagnostics-panel {
      align-self: start;
      position: sticky;
      top: 90px;
      max-height: calc(100vh - 110px);
      overflow: auto;
    }
    .task {
      display: grid;
      gap: 8px;
      margin-bottom: 16px;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--line);
    }
    .task .text { font-size: 14px; line-height: 1.55; }
    .meta, .muted {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      overflow-wrap: anywhere;
    }
    .todo { display: grid; gap: 8px; }
    .todo-item {
      display: grid;
      grid-template-columns: 18px 1fr;
      gap: 8px;
      align-items: start;
      font-size: 13px;
      line-height: 1.45;
    }
    .check {
      width: 16px;
      height: 16px;
      border-radius: 4px;
      border: 1px solid var(--line);
      display: inline-grid;
      place-items: center;
      font-size: 11px;
      color: #fff;
      background: #fff;
    }
    .check.completed { background: var(--good); border-color: var(--good); }
    .check.in_progress { background: var(--accent); border-color: var(--accent); }
    .conversation {
      display: grid;
      gap: 10px;
      min-height: 220px;
      max-height: 42vh;
      overflow: auto;
      padding-right: 4px;
      margin-bottom: 14px;
    }
    .message {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fff;
      line-height: 1.55;
      font-size: 14px;
      overflow-wrap: anywhere;
    }
    .message.user { border-color: #99f6e4; background: #f0fdfa; }
    .message.assistant { border-color: #bfdbfe; background: #eff6ff; }
    .message.system, .message.event { color: var(--muted); font-size: 12px; }
    .chat-form {
      display: grid;
      gap: 10px;
      margin-bottom: 16px;
    }
    textarea {
      width: 100%;
      min-height: 92px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      font: inherit;
      line-height: 1.5;
    }
    .chat-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      justify-content: flex-end;
    }
    .primary {
      background: var(--brand);
      border-color: var(--brand);
      color: #fff;
    }
    .timeline {
      display: grid;
      gap: 10px;
      max-height: 48vh;
      overflow: auto;
      padding-right: 4px;
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fff;
      cursor: pointer;
    }
    .card:hover { border-color: var(--brand); }
    .card.active { border-color: var(--brand); background: #f0fdfa; }
    .card-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
    }
    .card-title { font-size: 14px; font-weight: 700; }
    .pill {
      border-radius: 999px;
      padding: 3px 8px;
      border: 1px solid var(--line);
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
    }
    .summary {
      color: #344054;
      font-size: 13px;
      line-height: 1.55;
      overflow-wrap: anywhere;
    }
    .lane {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      margin-bottom: 8px;
      background: #fff;
    }
    .lane-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 6px;
      font-size: 13px;
      font-weight: 700;
    }
    .detail {
      margin-top: 14px;
      border-top: 1px solid var(--line);
      padding-top: 14px;
    }
    .live-events {
      display: grid;
      gap: 8px;
      max-height: 240px;
      overflow: auto;
      padding-right: 4px;
    }
    .live-event {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px;
      background: #fff;
      font-size: 12px;
      line-height: 1.45;
    }
    .live-event strong {
      display: block;
      margin-bottom: 4px;
      color: var(--text);
    }
    .live-event.error, .live-event.cancelled { border-color: var(--bad); }
    .live-event.done { border-color: var(--good); }
    pre {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: #101828;
      color: #e5e7eb;
      padding: 12px;
      border-radius: 8px;
      font-size: 12px;
      max-height: 300px;
      overflow: auto;
    }
    @media (max-width: 1060px) {
      header { height: auto; padding: 14px 16px; align-items: flex-start; gap: 12px; }
      main { grid-template-columns: 1fr; padding: 12px; }
      .overview-panel { grid-column: auto; grid-template-columns: 1fr; }
      .diagnostics-panel { position: static; max-height: none; }
      .timeline { max-height: none; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Ming 任务工作台</h1>
      <div class="meta" id="workspace"></div>
    </div>
    <div class="status">
      <span class="dot" id="stateDot"></span>
      <span id="stateText">loading</span>
      <button id="refreshBtn" type="button">刷新</button>
    </div>
  </header>
  <main>
    <aside class="overview-panel">
      <div class="task">
        <h2>当前任务</h2>
        <div class="text" id="taskText"></div>
        <div class="meta" id="taskMeta"></div>
      </div>
      <h2>TODO</h2>
      <div class="todo" id="todoList"></div>
      <div class="detail">
        <h2>运行产物</h2>
        <div class="meta" id="artifacts"></div>
      </div>
    </aside>
    <section class="workbench">
      <h2>对话</h2>
      <div class="conversation" id="conversation"></div>
      <form class="chat-form" id="chatForm">
        <textarea id="messageInput" name="message" placeholder="输入任务，Ming 会在本地运行。"></textarea>
        <div class="chat-actions">
          <span class="meta" id="chatStatus">ready</span>
          <button class="primary" id="sendBtn" type="submit">Send</button>
          <button id="stopTurnBtn" type="button" disabled>Stop</button>
        </div>
      </form>
      <div class="detail">
        <h2>执行过程</h2>
        <div class="timeline" id="runTimeline"></div>
      </div>
    </section>
    <aside class="diagnostics-panel">
      <h2>Agent 状态</h2>
      <div class="summary" id="agentSummary"></div>
      <div class="detail">
        <h2>Live Events</h2>
        <div class="meta" id="liveStatus">connecting</div>
        <div class="live-events" id="liveEvents"></div>
      </div>
      <div class="detail">
        <h2>可公开思路摘要</h2>
        <div class="summary" id="thoughtSummary"></div>
      </div>
      <div class="detail">
        <h2>Subagents</h2>
        <div id="subagents"></div>
      </div>
      <div class="detail">
        <h2>详情</h2>
        <pre id="details">点击左侧步骤查看结构化详情。</pre>
      </div>
    </aside>
  </main>
  <script>
    let selectedId = "";
    const liveEvents = [];
    const conversation = [];
    const liveRunEvents = [];
    let stateTimeline = [];
    function normalizeStaticLabels() {
      const labels = [
        "当前任务",
        "TODO",
        "运行产物",
        "对话",
        "执行过程",
        "Agent 状态",
        "SSE 实时事件",
        "思考摘要",
        "Subagents",
        "诊断详情",
      ];
      document.querySelectorAll("h2").forEach((heading, index) => {
        if (labels[index]) {
          heading.textContent = labels[index];
        }
      });
      document.getElementById("refreshBtn").textContent = "刷新";
      document.getElementById("messageInput").placeholder = "输入任务，Ming 会在本地运行。";
      document.getElementById("details").textContent = "点击执行过程中的步骤，查看结构化详情。";
    }
    async function loadState() {
      const response = await fetch("/api/state", { cache: "no-store" });
      const state = await response.json();
      render(state);
    }
    function text(value) {
      return value === undefined || value === null ? "" : String(value);
    }
    function render(state) {
      document.getElementById("workspace").textContent = state.workspace;
      document.getElementById("taskText").textContent = state.current_task.text;
      document.getElementById("taskMeta").textContent =
        `${state.current_task.turn_id || "no turn"} · ${state.current_task.started_at || "no time"}`;
      document.getElementById("stateText").textContent =
        `${state.agent.state} · ${state.agent.mode}`;
      const dot = document.getElementById("stateDot");
      dot.className = `dot ${state.agent.state}`;
      document.getElementById("agentSummary").textContent = state.agent.summary;
      document.getElementById("thoughtSummary").textContent = state.agent.thought_summary;
      renderTodo(state.todo.items || []);
      stateTimeline = state.timeline || [];
      renderRunTimeline();
      renderSubagents(state.subagents || []);
      renderArtifacts(state.artifacts || {});
    }
    function renderTodo(items) {
      const root = document.getElementById("todoList");
      root.innerHTML = "";
      if (!items.length) {
        root.innerHTML = `<div class="muted">暂无 TODO。</div>`;
        return;
      }
      for (const item of items) {
        const row = document.createElement("div");
        row.className = "todo-item";
        row.innerHTML =
          `<span class="check ${item.status}">${item.status === "completed" ? "✓" : ""}</span>` +
          `<span>${escapeHtml(item.text)}<br><span class="muted">${escapeHtml(item.status)}</span></span>`;
        root.appendChild(row);
      }
    }
    function renderRunTimeline(cards) {
      const root = document.getElementById("runTimeline");
      cards = cards || (liveRunEvents.length ? liveRunEvents : stateTimeline);
      root.innerHTML = "";
      for (const card of cards) {
        const node = document.createElement("article");
        node.className = `card ${selectedId === card.id ? "active" : ""}`;
        node.innerHTML =
          `<div class="card-head"><span class="card-title">${escapeHtml(card.title)}</span>` +
          `<span class="pill">${escapeHtml(card.kind)} · ${escapeHtml(card.status)}</span></div>` +
          `<div class="summary">${escapeHtml(card.summary)}</div>`;
        node.addEventListener("click", () => {
          selectedId = card.id;
          document.getElementById("details").textContent =
            JSON.stringify(card.details || {}, null, 2);
          renderRunTimeline(cards);
        });
        root.appendChild(node);
      }
    }
    function renderSubagents(agents) {
      const root = document.getElementById("subagents");
      root.innerHTML = "";
      for (const agent of agents) {
        const lane = document.createElement("div");
        lane.className = "lane";
        lane.innerHTML =
          `<div class="lane-title"><span>${escapeHtml(agent.name)}</span>` +
          `<span class="pill">${escapeHtml(agent.status)}</span></div>` +
          `<div class="meta">${escapeHtml(agent.role)}</div>` +
          `<div class="summary">${escapeHtml(agent.summary)}</div>`;
        root.appendChild(lane);
      }
    }
    function renderArtifacts(artifacts) {
      document.getElementById("artifacts").textContent =
        [
          `trace: ${text(artifacts.trace_path)}`,
          `checkpoint: ${text(artifacts.checkpoint_path)}`,
          `notepad: ${text(artifacts.notepad_path)}`,
          `changed: ${(artifacts.changed_files || []).join(", ") || "none"}`,
        ].join("\\n");
    }
    async function submitChat(event) {
      event.preventDefault();
      const input = document.getElementById("messageInput");
      const message = input.value.trim();
      if (!message) {
        return;
      }
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
      if (conversation.length > 80) {
        conversation.shift();
      }
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
        verify: "核验结果",
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
        details: event,
      };
    }
    function appendRunEvent(event) {
      const card = formatRunEvent(event);
      if (!liveRunEvents.some(item => item.id === card.id)) {
        liveRunEvents.push(card);
      }
      if (liveRunEvents.length > 80) {
        liveRunEvents.shift();
      }
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
        if (event.stage === "done") {
          loadState();
        }
      }
    }
    function connectLiveEvents() {
      const status = document.getElementById("liveStatus");
      const source = new EventSource("/api/events");
      const stages = ["submitted", "context", "route", "llm", "tool", "verify", "done", "final", "error", "cancelled", "heartbeat"];
      source.onopen = () => {
        status.textContent = "connected";
      };
      source.onerror = () => {
        status.textContent = "reconnecting";
      };
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
      if (liveEvents.length > 20) {
        liveEvents.pop();
      }
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
        const detail = event.detail ? `<div class="meta">${escapeHtml(event.detail)}</div>` : "";
        node.innerHTML =
          `<strong>${escapeHtml(event.stage)} · ${escapeHtml(event.message)}</strong>` +
          `<div class="meta">${escapeHtml(event.turn_id || "no turn")} · #${escapeHtml(event.seq)}</div>` +
          detail;
        root.appendChild(node);
      }
    }
    function escapeHtml(value) {
      return text(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }
    document.getElementById("refreshBtn").addEventListener("click", loadState);
    document.getElementById("chatForm").addEventListener("submit", submitChat);
    document.getElementById("stopTurnBtn").addEventListener("click", stopTurn);
    normalizeStaticLabels();
    renderConversation();
    renderLiveEvents();
    connectLiveEvents();
    loadState();
    setInterval(loadState, 1800);
  </script>
</body>
</html>
"""
