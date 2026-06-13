"""Local Web UI chat runtime for running one cancellable Ming turn."""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Awaitable, Callable

from ming.config import MingConfig, load_config
from ming.core.agent import Agent, AgentProgressEvent
from ming.core.live_events import LiveEventStore
from ming.core.trace import new_turn_id

logger = logging.getLogger("ming")

AgentRunner = Callable[[str, str], Awaitable[str]]


class ChatRuntime:
    """Own a single active Web UI turn and bridge progress to live events."""

    def __init__(
        self,
        workspace_root: str | Path | None = None,
        config: MingConfig | None = None,
        agent_runner: AgentRunner | None = None,
        live_events: LiveEventStore | None = None,
    ):
        self.workspace_root = Path(workspace_root or Path.cwd())
        self.config = config or load_config()
        self.live_events = live_events or LiveEventStore(self.workspace_root / ".ming" / "live")
        self._lock = threading.Lock()
        self._active_future: Future | None = None
        self._active_turn_id = ""
        self._final_output = ""
        self._last_error = ""
        self._cancel_event_turns: set[str] = set()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="ming-web-chat-runtime",
            daemon=True,
        )
        self._thread.start()

        if agent_runner is None:
            agent = Agent(
                self.config,
                working_dir=str(self.workspace_root),
                progress_callback=self._record_progress,
            )

            async def run_agent(message: str, turn_id: str) -> str:
                return await agent.chat(message, turn_id=turn_id)

            self._agent_runner = run_agent
        else:
            self._agent_runner = agent_runner

    def submit(self, message: str) -> dict[str, str]:
        clean = message.strip()
        if not clean:
            return {"status": "invalid", "error": "message is required"}
        with self._lock:
            if self._is_running_locked():
                return {"status": "busy", "turn_id": self._active_turn_id}
            turn_id = new_turn_id()
            self._active_turn_id = turn_id
            self._final_output = ""
            self._last_error = ""
            self._cancel_event_turns.discard(turn_id)
            self.live_events.append(
                stage="submitted",
                message="Web chat submitted",
                turn_id=turn_id,
            )
            future = asyncio.run_coroutine_threadsafe(self._run_turn(clean, turn_id), self._loop)
            self._active_future = future
            return {"status": "running", "turn_id": turn_id}

    def stop(self) -> dict[str, str]:
        with self._lock:
            if not self._is_running_locked() or self._active_future is None:
                return {"status": "idle"}
            turn_id = self._active_turn_id
            self._active_future.cancel()
            self._cancel_event_turns.add(turn_id)
            self.live_events.append(
                stage="cancelled",
                message="已停止本轮思考",
                turn_id=turn_id,
                event_type="cancelled",
            )
            return {"status": "cancelled", "turn_id": turn_id}

    def status(self) -> dict[str, str]:
        with self._lock:
            status = "running" if self._is_running_locked() else "idle"
            return {
                "status": status,
                "turn_id": self._active_turn_id,
                "final_output": self._final_output,
                "error": self._last_error,
            }

    def shutdown(self) -> None:
        with self._lock:
            future = self._active_future
        if future is not None and not future.done():
            future.cancel()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=1)
        self._loop.close()

    async def _run_turn(self, message: str, turn_id: str) -> None:
        try:
            output = await self._agent_runner(message, turn_id)
            with self._lock:
                self._final_output = output
            self.live_events.append(
                stage="final",
                message="Final response",
                turn_id=turn_id,
                detail=output,
                event_type="final",
            )
        except asyncio.CancelledError:
            with self._lock:
                should_emit = turn_id not in self._cancel_event_turns
                self._cancel_event_turns.add(turn_id)
            if should_emit:
                self.live_events.append(
                    stage="cancelled",
                    message="已停止本轮思考",
                    turn_id=turn_id,
                    event_type="cancelled",
                )
            raise
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            logger.exception("Web chat turn failed")
            with self._lock:
                self._last_error = detail
            self.live_events.append(
                stage="error",
                message="Web chat turn failed",
                turn_id=turn_id,
                detail=detail,
                event_type="error",
            )
        finally:
            with self._lock:
                if self._active_turn_id == turn_id:
                    self._active_future = None

    def _record_progress(self, event: AgentProgressEvent) -> None:
        event_type = {
            "done": "final",
            "error": "error",
            "cancelled": "cancelled",
        }.get(event.stage, "progress")
        self.live_events.append(
            stage=event.stage,
            message=event.message,
            turn_id=event.turn_id,
            detail=event.detail,
            event_type=event_type,
        )

    def _is_running_locked(self) -> bool:
        return self._active_future is not None and not self._active_future.done()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
