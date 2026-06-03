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
