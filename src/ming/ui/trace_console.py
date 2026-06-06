# ruff: noqa: E501
"""Local Trace Console for visualizing recent Ming agent turns."""

from __future__ import annotations

import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


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

    def __init__(self, workspace_root: str | Path | None = None):
        self.workspace_root = Path(workspace_root or Path.cwd())
        self.state_builder = TraceConsoleState(self.workspace_root)

    def state(self) -> dict[str, Any]:
        return self.state_builder.load()

    def state_json(self) -> str:
        return json.dumps(self.state(), ensure_ascii=False, indent=2)

    def render_index(self) -> str:
        return INDEX_HTML

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
  <title>Ming Trace Console</title>
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
      grid-template-columns: minmax(220px, 300px) minmax(360px, 1fr) minmax(260px, 340px);
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
    .timeline {
      display: grid;
      gap: 10px;
      max-height: calc(100vh - 128px);
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
      .timeline { max-height: none; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Ming Trace Console</h1>
      <div class="meta" id="workspace"></div>
    </div>
    <div class="status">
      <span class="dot" id="stateDot"></span>
      <span id="stateText">loading</span>
      <button id="refreshBtn" type="button">刷新</button>
    </div>
  </header>
  <main>
    <aside>
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
    <section>
      <h2>Agent Loop</h2>
      <div class="timeline" id="timeline"></div>
    </section>
    <aside>
      <h2>Agent 状态</h2>
      <div class="summary" id="agentSummary"></div>
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
      renderTimeline(state.timeline || []);
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
    function renderTimeline(cards) {
      const root = document.getElementById("timeline");
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
          renderTimeline(cards);
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
    function escapeHtml(value) {
      return text(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }
    document.getElementById("refreshBtn").addEventListener("click", loadState);
    loadState();
    setInterval(loadState, 1800);
  </script>
</body>
</html>
"""
