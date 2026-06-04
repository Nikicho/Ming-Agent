import logging

import pytest

from ming import cli


def test_main_prints_help_without_entering_interactive(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "Usage:" in output
    assert "/compact" in output
    assert "/rewind" in output
    assert "/resume" in output
    assert "/scope" in output
    assert "/expand" in output
    assert "/cleanup" in output


def test_setup_logging_defaults_file_log_to_info(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    cli._setup_logging("INFO")
    logger = logging.getLogger("ming")
    logger.debug("debug should stay hidden")
    logger.info("info should be recorded")

    for handler in logging.getLogger().handlers:
        handler.flush()

    log_file = next((tmp_path / ".ming" / "logs").glob("ming_*.log"))
    text = log_file.read_text(encoding="utf-8")

    assert "info should be recorded" in text
    assert "debug should stay hidden" not in text


def test_setup_logging_suppresses_noisy_provider_console_logs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    cli._setup_logging("INFO")

    assert logging.getLogger("LiteLLM").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("litellm").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("asyncio").getEffectiveLevel() >= logging.WARNING
    assert cli._should_ignore_asyncio_exception({
        "exception": ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接。")
    })


def test_format_progress_event_defaults_to_summary():
    event = cli.AgentProgressEvent(
        stage="tool",
        message="执行工具 file_write",
        detail='{"path": "scratch/demo.txt", "content": "hello"}',
    )

    assert cli._format_progress_event(event, show_details=False) == "Ming: 执行工具 file_write"
    detailed = cli._format_progress_event(event, show_details=True)
    assert "scratch/demo.txt" in detailed
