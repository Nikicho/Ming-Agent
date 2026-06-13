from ming.core.live_events import LiveEventStore


def test_live_event_store_appends_sequence_and_reads_since(tmp_path):
    store = LiveEventStore(tmp_path / ".ming" / "live")

    first = store.append(stage="context", message="准备上下文", turn_id="turn-1")
    second = store.append(stage="tool", message="执行工具 file_write", turn_id="turn-1")

    assert first["seq"] == 1
    assert second["seq"] == 2
    assert [event["stage"] for event in store.since(0)] == ["context", "tool"]
    assert [event["stage"] for event in store.since(1)] == ["tool"]


def test_live_event_store_tolerates_bad_lines(tmp_path):
    store = LiveEventStore(tmp_path / ".ming" / "live")
    store.path.parent.mkdir(parents=True)
    store.path.write_text("{bad json}\n", encoding="utf-8")

    event = store.append(stage="done", message="完成", turn_id="turn-2")

    assert event["seq"] == 1
    assert store.since(0)[0]["stage"] == "done"
