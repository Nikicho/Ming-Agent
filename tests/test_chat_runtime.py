import asyncio
import time
from threading import Event

from ming.ui.chat_runtime import ChatRuntime


def _wait_until(predicate, timeout=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_chat_runtime_starts_turn_and_rejects_second_submit(tmp_path):
    started = Event()
    release = Event()

    async def runner(message: str, turn_id: str) -> str:
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return f"ok:{message}:{turn_id}"

    runtime = ChatRuntime(tmp_path, agent_runner=runner)
    try:
        accepted = runtime.submit("hello")
        assert accepted["status"] == "running"
        assert accepted["turn_id"]
        assert started.wait(1)

        busy = runtime.submit("again")
        assert busy["status"] == "busy"
        assert busy["turn_id"] == accepted["turn_id"]

        release.set()
        assert _wait_until(lambda: runtime.status()["status"] == "idle")
        assert runtime.status()["final_output"].startswith("ok:hello:")
    finally:
        runtime.shutdown()


def test_chat_runtime_stop_cancels_active_turn_and_emits_event(tmp_path):
    started = Event()
    cancelled = Event()

    async def runner(message: str, turn_id: str) -> str:
        started.set()
        try:
            while True:
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    runtime = ChatRuntime(tmp_path, agent_runner=runner)
    try:
        accepted = runtime.submit("hello")
        assert started.wait(1)

        stopped = runtime.stop()
        assert stopped["status"] == "cancelled"
        assert stopped["turn_id"] == accepted["turn_id"]
        assert cancelled.wait(1)
        assert _wait_until(lambda: runtime.status()["status"] == "idle")
        cancelled_events = [
            event for event in runtime.live_events.since(0) if event["stage"] == "cancelled"
        ]
        assert len(cancelled_events) == 1
    finally:
        runtime.shutdown()
