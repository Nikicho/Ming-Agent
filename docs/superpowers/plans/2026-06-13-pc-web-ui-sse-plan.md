# Ming PC Web UI + SSE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Ming stage 2a PC interaction surface: a local browser Web UI with HTTP POST input, SSE live agent-loop events, collapsible tool cards, stop control, and an enhanced dialogue flow.

**Architecture:** Keep Ming Core in Python. First add a durable live event bus and SSE endpoint to remove the “卡住感”; then add a local chat API that runs `Agent.chat()` in a cancellable background task; finally replace the current Trace Console dashboard with a conversation-first UI. React is the target frontend, but Phase 1 can use the existing stdlib HTML page to reduce risk.

**Tech Stack:** Python 3.12 stdlib HTTP server, Server-Sent Events, existing `Agent.progress_callback`, JSONL live event store, pytest, ruff. React/Vite is introduced only after the Python event/API contract is stable.

---

## Scope From Architecture Design

Source design: `C:\Users\Hebe1\Documents\Obsidian Vault\自主进化Agent研究\Ming 架构设计文档.md`, sections 7.3-7.4.

In scope for this plan:
- PC first. Browser at `localhost`.
- SSE + HTTP POST in 2a.
- Enhanced dialogue flow as primary UI.
- Tool calls folded by default.
- Memory reachable from side panel, not always visible.
- Stop current turn.
- Trace/current task visibility without exposing raw chain-of-thought.

Out of scope for this plan:
- Tauri v2 shell.
- Phone remote-control session.
- Voice, image paste, file drag-and-drop.
- Full token streaming from provider. The first SSE implementation streams progress events and final output; token streaming is a later LLM-provider task.
- WebSocket.
- γ裁决卡片 backend semantics beyond rendering a message type placeholder.

---

## File Structure

### Python Core

- Create `src/ming/core/live_events.py`
  - Durable append-only JSONL event store under `.ming/live/events.jsonl`.
  - Event schema and helpers: `append()`, `tail()`, `since(seq)`, `clear()`.
  - Keeps UI and CLI decoupled.

- Modify `src/ming/core/agent.py`
  - Add optional `turn_id` to progress events, or emit enough metadata for live UI.
  - Emit structured live events for `context`, `route`, `llm`, `tool`, `verify`, `done`, `error`, `cancelled`.
  - Keep existing `progress_callback` behavior for CLI.

- Modify `src/ming/cli.py`
  - When interactive CLI runs, attach a live event writer in addition to console progress output.
  - Keep CLI usable without Web UI.

### Local Web Server

- Modify `src/ming/ui/trace_console.py`
  - Phase 1: add `/api/events` SSE endpoint that tails `.ming/live/events.jsonl`.
  - Phase 1: add UI EventSource subscription to display live events.
  - Phase 2: add `/api/chat` POST endpoint and `/api/turns/current/stop` POST endpoint.
  - Phase 2: maintain a single active background task per server process.

- Create `src/ming/ui/chat_runtime.py`
  - Owns the local Web UI runtime state.
  - Starts `Agent.chat()` in a cancellable task.
  - Bridges `AgentProgressEvent` into `LiveEventStore`.
  - Tracks active turn status and final output.

### Frontend

Phase 1:
- Modify inline HTML in `src/ming/ui/trace_console.py`
  - Keep current stdlib page.
  - Add live event strip / status rail.
  - Add Stop button placeholder only if server-managed turn exists.

Phase 2:
- Create `web/package.json`
- Create `web/vite.config.ts`
- Create `web/src/App.tsx`
- Create `web/src/api.ts`
- Create `web/src/components/Conversation.tsx`
- Create `web/src/components/ToolCard.tsx`
- Create `web/src/components/DecisionCard.tsx`
- Create `web/src/components/Sidebar.tsx`
- Create `web/src/styles.css`
- Add built asset serving from Python after `npm run build`.

Keep React introduction separate from Python SSE work. Do not mix both in one commit.

### Tests

- Create `tests/test_live_events.py`
- Extend `tests/test_trace_console.py`
- Create `tests/test_chat_runtime.py`
- Extend `tests/test_agent_pipeline.py`
- Keep browser smoke verification manual or headless Chrome based until frontend stack is formalized.

### Docs

- Update `README.md`
  - `python -m ming ui --port 8765`
  - `/api/events` SSE behavior
  - Stop current turn
  - Known limitations: no token streaming yet, no Tauri yet.

- Update `docs/experience-scenarios.md`
  - Add Web UI SSE live run scenario.
  - Add Stop button scenario.

---

## Milestone 1: Durable Live Event Bus

### Task 1: Add Live Event Store

**Files:**
- Create: `src/ming/core/live_events.py`
- Test: `tests/test_live_events.py`

- [ ] **Step 1: Write failing tests**

Add:

```python
from ming.core.live_events import LiveEventStore


def test_live_event_store_appends_sequence_and_reads_since(tmp_path):
    store = LiveEventStore(tmp_path / ".ming" / "live")

    first = store.append(stage="context", message="准备上下文", turn_id="turn-1")
    second = store.append(stage="tool", message="执行工具 file_write", turn_id="turn-1")

    assert first["seq"] == 1
    assert second["seq"] == 2
    assert [event["stage"] for event in store.since(0)] == ["context", "tool"]
    assert [event["stage"] for event in store.since(1)] == ["tool"]
```

- [ ] **Step 2: Run test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_live_events.py -q
```

Expected: fail because `ming.core.live_events` does not exist.

- [ ] **Step 3: Implement `LiveEventStore`**

Implementation requirements:
- File path: `.ming/live/events.jsonl`.
- Event fields:
  - `seq: int`
  - `time: ISO seconds`
  - `turn_id: str`
  - `stage: str`
  - `message: str`
  - `detail: str`
  - `type: "progress" | "final" | "error" | "cancelled" | "heartbeat"`
- Use UTF-8 JSON lines.
- Do not store API keys or raw provider request payloads.

- [ ] **Step 4: Run test to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_live_events.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add src/ming/core/live_events.py tests/test_live_events.py
git commit -m "feat: add live event store"
```

---

### Task 2: Bridge Agent Progress Into Live Events

**Files:**
- Modify: `src/ming/core/agent.py`
- Modify: `src/ming/cli.py`
- Test: `tests/test_agent_pipeline.py`

- [ ] **Step 1: Write failing test**

Add a test that creates an `Agent` with a progress callback writing to `LiveEventStore`, runs a simple fake LLM/tool sequence, and asserts `context`, `llm`, `tool`, `verify`, `done` are in JSONL.

Use the existing fake LLM pattern from `tests/test_agent_pipeline.py::test_agent_emits_summary_progress_events`.

- [ ] **Step 2: Run test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_pipeline.py::test_agent_writes_live_progress_events -q
```

Expected: fail because no live writer is attached.

- [ ] **Step 3: Implement bridge**

Implementation approach:
- Keep `AgentProgressEvent` unchanged if possible.
- In `cli.interactive_loop()`, create `LiveEventStore(Path.cwd() / ".ming" / "live")`.
- In `show_progress(event)`, both print to console and append to live store.
- In `Agent._emit_progress()`, include high-signal message only; do not log raw chain-of-thought.
- On graceful LLM error / cancellation, emit `error` / `cancelled` before `_finish_turn()`.

- [ ] **Step 4: Run tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_pipeline.py tests\test_cli.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add src/ming/core/agent.py src/ming/cli.py tests/test_agent_pipeline.py
git commit -m "feat: stream agent progress to live events"
```

---

## Milestone 2: SSE Endpoint In Existing Trace Console

### Task 3: Add `/api/events` SSE Endpoint

**Files:**
- Modify: `src/ming/ui/trace_console.py`
- Test: `tests/test_trace_console.py`

- [ ] **Step 1: Write failing tests**

Add tests:

```python
def test_trace_console_formats_sse_event(tmp_path):
    app = TraceConsoleApp(tmp_path)
    event = {"seq": 1, "stage": "llm", "message": "调用模型，第 1 轮"}

    payload = app.format_sse(event)

    assert payload.startswith("id: 1\n")
    assert "event: llm\n" in payload
    assert "data: " in payload
```

- [ ] **Step 2: Run test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_trace_console.py::test_trace_console_formats_sse_event -q
```

Expected: fail because `format_sse` does not exist.

- [ ] **Step 3: Implement SSE support**

Add:
- `TraceConsoleApp.format_sse(event: dict) -> str`
- `TraceConsoleApp.event_stream(last_seq: int = 0) -> Iterator[str]`
- HTTP handler branch:
  - path `/api/events`
  - `Content-Type: text/event-stream`
  - `Cache-Control: no-cache`
  - `Connection: keep-alive`
  - loop tailing `LiveEventStore.since(last_seq)`
  - heartbeat every 10 seconds

Do not block `/api/state`; `ThreadingHTTPServer` can serve concurrent requests.

- [ ] **Step 4: Run tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_trace_console.py -q
```

Expected: pass.

- [ ] **Step 5: Manual smoke**

Start:

```powershell
.\.venv\Scripts\python.exe -m ming ui --port 8765
```

In another terminal:

```powershell
curl.exe -N http://127.0.0.1:8765/api/events
```

Expected: receives `heartbeat` and progress events after running Ming CLI.

- [ ] **Step 6: Commit**

```powershell
git add src/ming/ui/trace_console.py tests/test_trace_console.py
git commit -m "feat: add sse event stream"
```

---

### Task 4: Show Live Events In Current HTML

**Files:**
- Modify: `src/ming/ui/trace_console.py`
- Test: `tests/test_trace_console.py`

- [ ] **Step 1: Write failing HTML assertion**

Extend existing render test:

```python
assert "EventSource" in html
assert "/api/events" in html
assert "liveEvents" in html
```

- [ ] **Step 2: Run test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_trace_console.py::test_trace_console_app_renders_index_and_json -q
```

Expected: fail because HTML has no EventSource.

- [ ] **Step 3: Implement UI live strip**

Add a compact “Live” section:
- Shows connection state.
- Shows latest 20 events.
- Highlights `llm`, `tool`, `verify`, `error`, `cancelled`, `done`.
- Keeps existing `/api/state` polling for completed trace details.

Frontend JS:

```js
const source = new EventSource("/api/events");
source.addEventListener("llm", appendLiveEvent);
source.addEventListener("tool", appendLiveEvent);
source.addEventListener("verify", appendLiveEvent);
source.addEventListener("done", appendLiveEvent);
source.addEventListener("error", appendLiveEvent);
source.addEventListener("cancelled", appendLiveEvent);
source.addEventListener("heartbeat", updateHeartbeat);
```

- [ ] **Step 4: Run tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_trace_console.py -q
```

Expected: pass.

- [ ] **Step 5: Browser smoke**

Use headless Chrome:

```powershell
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$shot = (Resolve-Path .).Path + '\.ming\sse-ui-smoke.png'
& $chrome --headless=new --disable-gpu --window-size=1440,1000 --screenshot=$shot http://127.0.0.1:8765/
```

Expected: screenshot shows Live section and no layout overlap.

- [ ] **Step 6: Commit**

```powershell
git add src/ming/ui/trace_console.py tests/test_trace_console.py
git commit -m "feat: show live agent events"
```

---

## Milestone 3: Local Chat Runtime Over HTTP POST

### Task 5: Add Chat Runtime With Cancellable Active Turn

Status: completed in `src/ming/ui/chat_runtime.py`.

**Files:**
- Create: `src/ming/ui/chat_runtime.py`
- Test: `tests/test_chat_runtime.py`

- [x] **Step 1: Write failing tests**

Test behaviors:
- `submit("hello")` starts a background turn and returns `turn_id`.
- second submit while running returns a busy result.
- `stop()` cancels active task and emits cancelled event.

Use a fake async agent callable rather than real LLM.

- [x] **Step 2: Run test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_chat_runtime.py -q
```

Expected: fail because module does not exist.

- [x] **Step 3: Implement runtime**

`ChatRuntime` responsibilities:
- Own one `Agent` instance.
- Own one `LiveEventStore`.
- `submit(message: str) -> dict`
- `stop() -> dict`
- `status() -> dict`
- Store final output as a live event.

Avoid global mutable state outside the server instance.

- [x] **Step 4: Run tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_chat_runtime.py -q
```

Expected: pass.

- [x] **Step 5: Commit**

```powershell
git add src/ming/ui/chat_runtime.py tests/test_chat_runtime.py
git commit -m "feat: add local chat runtime"
```

---

### Task 6: Add `/api/chat` and `/api/turns/current/stop`

Status: completed in `src/ming/ui/trace_console.py`.

**Files:**
- Modify: `src/ming/ui/trace_console.py`
- Test: `tests/test_trace_console.py`

- [x] **Step 1: Write failing handler tests at app method level**

Avoid brittle socket tests first. Add app-level methods:
- `TraceConsoleApp.submit_chat(payload: dict) -> tuple[int, dict]`
- `TraceConsoleApp.stop_current_turn() -> tuple[int, dict]`

Tests:
- empty message returns 400.
- valid message returns 202 with `turn_id`.
- stop returns 200.

- [x] **Step 2: Run test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_trace_console.py -q
```

Expected: fail because methods do not exist.

- [x] **Step 3: Implement POST handling**

Handler routes:
- `POST /api/chat`
  - body: `{"message": "..."}`
  - response: `202 {"turn_id": "...", "status": "running"}`
- `POST /api/turns/current/stop`
  - response: `200 {"status": "cancelled"}` or `409 {"status": "idle"}`

For now, one active turn per server.

- [x] **Step 4: Run tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_trace_console.py tests\test_chat_runtime.py -q
```

Expected: pass.

- [x] **Step 5: Manual smoke**

```powershell
curl.exe -X POST http://127.0.0.1:8765/api/chat -H "Content-Type: application/json" -d "{\"message\":\"创建 scratch/sse_demo.txt，内容 hello\"}"
curl.exe -N http://127.0.0.1:8765/api/events
```

Expected: POST returns quickly; SSE shows progress.

- [x] **Step 6: Commit**

```powershell
git add src/ming/ui/trace_console.py tests/test_trace_console.py
git commit -m "feat: add web chat api"
```

---

## Milestone 4: Conversation-First UI MVP

### Task 7: Add Minimal Conversation UI To Current Page

Status: completed in current stdlib HTML page. Smoke covered by HTTP checks; browser plugin screenshot verification was unavailable in this session.

**Files:**
- Modify: `src/ming/ui/trace_console.py`
- Test: `tests/test_trace_console.py`

- [x] **Step 1: Write failing HTML assertions**

Add:

```python
assert "chatForm" in html
assert "messageInput" in html
assert "stopTurnBtn" in html
assert "conversation" in html
```

- [x] **Step 2: Run test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_trace_console.py::test_trace_console_app_renders_index_and_json -q
```

Expected: fail.

- [x] **Step 3: Implement UI**

Layout discipline from architecture doc:
- Primary center: conversation flow.
- Tool calls folded by default.
- Live status inline, not a dashboard.
- Side panel collapsed by default for trace/memory/settings.

Controls:
- Input textarea.
- Send button.
- Stop button while running.
- Live event list folded below assistant response.

- [x] **Step 4: Run tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_trace_console.py -q
```

Expected: pass.

- [x] **Step 5: Browser smoke**

Open:

```text
http://127.0.0.1:8765
```

Scenario:
1. Type `创建 scratch/webui_demo.txt，内容 hello，然后读取确认`.
2. Click Send.
3. Confirm live events appear.
4. Confirm final response appears.
5. Run a second longer request and click Stop.
6. Confirm cancelled event appears and UI becomes ready again.

- [x] **Step 6: Commit**

```powershell
git add src/ming/ui/trace_console.py tests/test_trace_console.py
git commit -m "feat: add conversation web ui"
```

---

## Milestone 5: React Frontend Split

Do this only after Milestones 1-4 are stable.

### Task 8: Introduce Vite React App

**Files:**
- Create: `web/package.json`
- Create: `web/vite.config.ts`
- Create: `web/tsconfig.json`
- Create: `web/src/App.tsx`
- Create: `web/src/api.ts`
- Create: `web/src/styles.css`
- Modify: `src/ming/ui/trace_console.py`
- Test: `tests/test_trace_console.py`

- [ ] **Step 1: Decide dependency boundary**

Add frontend tooling only under `web/`. Do not make Python tests require Node unless explicitly running frontend checks.

- [ ] **Step 2: Scaffold React app**

Use Vite React TypeScript.

Required npm scripts:

```json
{
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "typecheck": "tsc --noEmit"
  }
}
```

- [ ] **Step 3: Implement components**

Components:
- `Conversation`
- `MessageBubble`
- `ToolCard`
- `DecisionCard`
- `Sidebar`
- `Composer`

- [ ] **Step 4: Serve built assets**

Python server should serve:
- `/assets/*`
- `/`
- fallback to current inline HTML if build does not exist.

- [ ] **Step 5: Verify**

Run:

```powershell
cd web
npm install
npm run build
cd ..
.\.venv\Scripts\python.exe -m pytest tests\test_trace_console.py -q
```

Expected: React build succeeds; Python asset tests pass.

- [ ] **Step 6: Commit**

```powershell
git add web src/ming/ui/trace_console.py tests/test_trace_console.py
git commit -m "feat: introduce react web ui"
```

---

## Milestone 6: UX Polish And Reliability

### Task 9: Event Contract Hardening

**Files:**
- Modify: `src/ming/core/live_events.py`
- Modify: `src/ming/ui/chat_runtime.py`
- Test: `tests/test_live_events.py`

- [ ] Add schema version field.
- [ ] Add max event log size or rotation policy.
- [ ] Add sanitization test: API key-looking strings are redacted from details.
- [ ] Add reconnection behavior using `Last-Event-ID`.
- [ ] Commit: `fix: harden live event contract`.

### Task 10: Stop UX

**Files:**
- Modify: `src/ming/ui/chat_runtime.py`
- Modify: `web/src/App.tsx` or inline HTML depending on phase
- Test: `tests/test_chat_runtime.py`

- [ ] Stop button sends `/api/turns/current/stop`.
- [ ] Server cancels task.
- [ ] UI shows “已停止本轮思考”.
- [ ] New message can be sent after stop.
- [ ] Commit: `feat: add stop control to web ui`.

### Task 11: Documentation And Experience Scenarios

**Files:**
- Modify: `README.md`
- Modify: `docs/experience-scenarios.md`

- [ ] Add Web UI usage.
- [ ] Add SSE troubleshooting.
- [ ] Add known limitations.
- [ ] Add validation scenarios:
  - simple file creation
  - local HTML page generation
  - long request + stop
  - tool error
  - model error
- [ ] Commit: `docs: document web ui sse workflow`.

---

## Verification Matrix

Run before final handoff:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ming --help
.\.venv\Scripts\python.exe -m ming ui --port 8765
```

Manual:
- `curl.exe -N http://127.0.0.1:8765/api/events`
- Browser smoke on `http://127.0.0.1:8765`
- Headless Chrome screenshot check for no blank page/no overlap.

Expected:
- Tests pass.
- SSE connects and receives heartbeat.
- Sending a message returns immediately.
- Live events show progress before final answer.
- Stop cancels current turn without traceback.
- Trace/checkpoint still saved.

---

## Risks And Design Notes

- Current `TraceConsoleState` contains mojibake strings from older edits. Do not broaden this plan into a full text cleanup unless tests expose user-visible breakage.
- Token streaming is not included until the LLM provider layer supports streaming cleanly. Progress streaming solves the current “卡住感” first.
- React introduction should be delayed until the SSE/chat API contract works with the current page.
- Keep CLI first-class. Web UI should reuse Agent Core, not fork behavior.
- Do not commit generated files such as `.ming/`, `scratch/`, `pomodoro.html`, `web/node_modules/`, or built assets unless explicitly chosen for distribution.

---

## Suggested Execution Order

1. Milestone 1: Live event bus.
2. Milestone 2: SSE endpoint and current-page live event display.
3. Milestone 3: Local chat API and cancellable runtime.
4. Milestone 4: Conversation-first UI MVP in current page.
5. Milestone 5: React split.
6. Milestone 6: polish and hardening.

This order gives useful UX improvements after each milestone and avoids a high-risk rewrite.
