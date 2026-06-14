"""Ming CLI - interactive chat interface."""

import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import Sequence

# Fix Windows console encoding for CJK characters
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

from rich.console import Console
from rich.logging import RichHandler
from rich.markdown import Markdown
from rich.panel import Panel

from ming import __version__
from ming.config import load_config

console = Console()


@dataclass(frozen=True)
class AgentProgressEvent:
    stage: str
    message: str
    detail: str = ""
    turn_id: str = ""


def _setup_logging(level: str = "INFO") -> None:
    from pathlib import Path

    log_dir = Path(".ming") / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # File handler — follows normal level by default; /debug can raise it later.
    from datetime import datetime
    log_file = log_dir / f"ming_{datetime.now():%Y%m%d_%H%M%S}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"
    ))

    # Console handler is intentionally quiet by default; user-facing progress
    # is emitted separately from raw provider logs.
    console_handler = RichHandler(console=console, show_path=False, show_time=False)
    console_handler.setLevel(logging.WARNING if numeric_level < logging.WARNING else numeric_level)

    logging.basicConfig(
        level=min(numeric_level, logging.WARNING),
        handlers=[console_handler, file_handler],
        force=True,
    )
    logging.getLogger("ming").setLevel(numeric_level)

    # Suppress noisy third-party loggers
    for name in ("httpx", "litellm", "LiteLLM", "httpcore", "openai", "aiohttp", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger("ming").info(f"Session log: {log_file}")


def _set_ming_log_level(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.getLogger("ming").setLevel(numeric_level)
    for handler in logging.getLogger().handlers:
        handler.setLevel(numeric_level)
    for name in ("litellm", "LiteLLM", "httpx", "httpcore", "openai", "aiohttp"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _should_ignore_asyncio_exception(context: dict) -> bool:
    exc = context.get("exception")
    if not isinstance(exc, ConnectionResetError):
        return False
    values = {getattr(exc, "errno", None), getattr(exc, "winerror", None), *exc.args}
    return 10054 in values


def _install_asyncio_exception_filter(loop: asyncio.AbstractEventLoop) -> None:
    default_handler = loop.get_exception_handler()

    def handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        if _should_ignore_asyncio_exception(context):
            logging.getLogger("ming").debug("Suppressed benign asyncio connection reset")
            return
        if default_handler:
            default_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handler)


def _format_progress_event(event: AgentProgressEvent, show_details: bool = False) -> str:
    text = f"Ming: {event.message}"
    if show_details and event.detail:
        return f"{text} | {event.detail}"
    return text


def _record_live_progress(store, event: AgentProgressEvent) -> None:
    event_type = {
        "done": "final",
        "error": "error",
        "cancelled": "cancelled",
    }.get(event.stage, "progress")
    store.append(
        stage=event.stage,
        message=event.message,
        turn_id=event.turn_id,
        detail=event.detail,
        event_type=event_type,
    )


def print_banner():
    console.print(
        Panel(
            f"[bold cyan]Ming (明)[/bold cyan] v{__version__}\n"
            "[dim]知常曰明，不知常，妄作凶 —— 《道德经》[/dim]\n\n"
            "[green]Commands:[/green]\n"
            "  /quit    Exit\n"
            "  /clear   Clear conversation\n"
            "  /status  Show session info\n"
            "  /debug   Toggle debug logging\n"
            "  /compact Compact old conversation context\n"
            "  /resume  Resume latest checkpoint context\n"
            "  /rewind  Remove the last turn from context\n"
            "  /rollback Roll back the latest file tool change\n"
            "  /forget <session|memory|project> Scoped forget\n"
            "  /scope <user,project,global> Switch active memory scopes\n"
            "  /cleanup Cleanup old checkpoints\n"
            "  /dream  Run a light Dream review report\n"
            "  /checkpoint Show the latest checkpoint file\n"
            "  /details Toggle detailed progress\n\n"
            "[dim]Press Ctrl+C while Ming is running to stop the current turn.[/dim]",
            title="[bold]Ming Agent[/bold]",
            border_style="cyan",
        )
    )


async def interactive_loop():
    config = load_config()
    _setup_logging(config.logging.level)
    loop = asyncio.get_running_loop()
    _install_asyncio_exception_filter(loop)

    from ming.core.agent import Agent

    if not config.llm.api_key:
        console.print(
            "[red]Error: No API key configured.[/red]\n"
            "Set MING_LLM_API_KEY env var, or create config/local.yaml:\n"
            "  llm:\n"
            '    api_key: "your-key"'
        )
        sys.exit(1)

    detail_mode = False
    from ming.core.live_events import LiveEventStore

    live_store = LiveEventStore()

    def show_progress(event: AgentProgressEvent) -> None:
        try:
            _record_live_progress(live_store, event)
        except Exception as exc:
            logging.getLogger("ming").debug("Live progress write failed: %s", exc)
        console.print(f"[dim]{_format_progress_event(event, detail_mode)}[/dim]")

    agent = Agent(config, progress_callback=show_progress)
    console.print(f"[dim]Model: {config.llm.model}[/dim]\n")
    debug_mode = False

    while True:
        try:
            user_input = console.input("[bold green]You:[/bold green] ").strip()
            if not user_input:
                continue

            # Commands
            if user_input.startswith("/"):
                cmd = user_input.lower().split()[0]
                if cmd in ("/quit", "/exit", "/q"):
                    if agent.session_trace.turns:
                        trace_path = agent.save_session_trace()
                        console.print(f"[dim]Session trace saved: {trace_path}[/dim]")
                    console.print("[dim]再见。[/dim]")
                    break
                elif cmd == "/clear":
                    removed = agent.clear_dialog()
                    console.print(f"[dim]Conversation cleared ({removed} messages).[/dim]\n")
                    continue
                elif cmd == "/status":
                    tokens = agent.context.current_tokens()
                    max_tok = agent.config.context.max_context_tokens
                    msg_count = len(agent.context.dialog_history)
                    mem_count = len(agent.memory.get_all())
                    pat_count = len(agent.automaticity.patterns)
                    console.print(
                        f"[dim]Messages: {msg_count} | "
                        f"Tokens: ~{tokens:,}/{max_tok:,} ({tokens*100//max_tok}%) | "
                        f"Memories: {mem_count} | Patterns: {pat_count} | "
                        f"Model: {config.llm.model}[/dim]\n"
                    )
                    continue
                elif cmd == "/debug":
                    debug_mode = not debug_mode
                    level = "DEBUG" if debug_mode else config.logging.level
                    _set_ming_log_level(level)
                    console.print(f"[dim]Debug {'ON' if debug_mode else 'OFF'}[/dim]\n")
                    continue
                elif cmd == "/compact":
                    await agent.compact_now()
                    console.print("[dim]Compaction requested.[/dim]\n")
                    continue
                elif cmd == "/resume":
                    parts = user_input.split(maxsplit=1)
                    checkpoint_id = parts[1].strip() if len(parts) > 1 else "latest"
                    checkpoint = agent.resume_latest_checkpoint(checkpoint_id)
                    if checkpoint is None:
                        console.print("[dim]No checkpoint found.[/dim]\n")
                    else:
                        console.print(
                            f"[dim]Resumed checkpoint: {checkpoint.get('turn_id')}[/dim]\n"
                        )
                    continue
                elif cmd == "/rewind":
                    removed = agent.rewind_last_turn()
                    console.print(f"[dim]Removed {removed} messages from the last turn.[/dim]\n")
                    continue
                elif cmd == "/rollback":
                    result = agent.rollback_last_change()
                    console.print(f"[dim]Rollback: {result}[/dim]\n")
                    continue
                elif cmd == "/forget":
                    parts = user_input.split(maxsplit=1)
                    if len(parts) < 2:
                        console.print("[yellow]Usage: /forget session|memory|project[/yellow]\n")
                        continue
                    try:
                        result = agent.forget_scope(parts[1])
                    except ValueError as exc:
                        console.print(f"[yellow]{exc}[/yellow]\n")
                        continue
                    if parts[1].strip().lower() in {"memory", "memories", "user", "project"}:
                        agent.set_context_scopes(agent.active_context_scopes)
                    console.print(f"[dim]Forgot: {result}[/dim]\n")
                    continue
                elif cmd == "/scope":
                    parts = user_input.split(maxsplit=1)
                    scopes = (
                        [part.strip() for part in parts[1].split(",")]
                        if len(parts) > 1
                        else ["user", "project", "global"]
                    )
                    try:
                        result = agent.set_context_scopes(scopes)
                    except ValueError as exc:
                        console.print(f"[yellow]{exc}[/yellow]\n")
                        continue
                    console.print(f"[dim]Context scopes: {result['active_scopes']}[/dim]\n")
                    continue
                elif cmd == "/cleanup":
                    result = agent.cleanup_runtime()
                    console.print(f"[dim]Cleanup: {result}[/dim]\n")
                    continue
                elif cmd == "/dream":
                    from ming.core.dream import DreamEngine

                    path = DreamEngine(agent.workspace_root).run()
                    console.print(f"[dim]Dream report: {path}[/dim]\n")
                    continue
                elif cmd == "/checkpoint":
                    path = agent.last_checkpoint_path or agent.checkpoints.latest()
                    console.print(f"[dim]Latest checkpoint: {path or 'none'}[/dim]\n")
                    continue
                elif cmd == "/details":
                    detail_mode = not detail_mode
                    console.print(f"[dim]Details {'ON' if detail_mode else 'OFF'}[/dim]\n")
                    continue
                else:
                    console.print(f"[yellow]Unknown command: {cmd}[/yellow]\n")
                    continue

            # Run agent
            console.print("[dim]Ming: 开始处理[/dim]")
            response = await agent.chat(user_input)

            console.print()
            console.print(Panel(
                Markdown(response),
                title="[bold cyan]Ming[/bold cyan]",
                border_style="cyan",
            ))
            console.print()

        except KeyboardInterrupt:
            console.print("\n[dim]Stopped current turn. Use /quit to exit.[/dim]")
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task and hasattr(task, "uncancel"):
                task.uncancel()
            console.print("\n[dim]Stopped current turn.[/dim]\n")
            continue
        except EOFError:
            if agent.session_trace.turns:
                trace_path = agent.save_session_trace()
                console.print(f"[dim]Session trace saved: {trace_path}[/dim]")
            console.print("\n[dim]Input closed.[/dim]")
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]\n")
            if debug_mode:
                import traceback
                console.print(f"[dim]{traceback.format_exc()}[/dim]")


def _help_text() -> str:
    return """Usage:
  python -m ming "your request"
  python -m ming
  python -m ming ui [--host 127.0.0.1] [--port 8765]
  python -m ming dream [--mode light] [--limit 10]

Interactive commands:
  /quit     Exit
  /clear    Clear conversation
  /status   Show token usage, memory count, and pattern count
  /debug    Toggle debug logging
  /compact  Compact old conversation context
  /resume   Resume latest checkpoint context
  /rewind   Remove the last turn from context
  /rollback Roll back the latest file_write/file_edit change
  /forget   Scoped forget: /forget session|memory|project
  /scope    Switch active memory scopes: /scope user,project,global
  /cleanup  Cleanup old checkpoints
  /dream    Run a non-mutating Dream review report
  /checkpoint Show the latest checkpoint file
  /details  Toggle detailed progress

Stop:
  Press Ctrl+C while Ming is running to stop the current turn
"""


def _run_trace_console(argv: Sequence[str]) -> None:
    host = "127.0.0.1"
    port = 8765
    index = 0
    while index < len(argv):
        option = argv[index]
        if option == "--host" and index + 1 < len(argv):
            host = argv[index + 1]
            index += 2
            continue
        if option == "--port" and index + 1 < len(argv):
            try:
                port = int(argv[index + 1])
            except ValueError:
                print(f"Invalid --port: {argv[index + 1]}")
                raise SystemExit(2) from None
            index += 2
            continue
        print(f"Unknown ui option: {option}")
        raise SystemExit(2)

    from ming.ui.trace_console import TraceConsoleApp

    TraceConsoleApp().serve(host=host, port=port)


def _run_dream(argv: Sequence[str]) -> None:
    mode = "light"
    limit = 10
    index = 0
    while index < len(argv):
        option = argv[index]
        if option == "--mode" and index + 1 < len(argv):
            mode = argv[index + 1]
            index += 2
            continue
        if option == "--limit" and index + 1 < len(argv):
            try:
                limit = int(argv[index + 1])
            except ValueError:
                print(f"Invalid --limit: {argv[index + 1]}")
                raise SystemExit(2) from None
            index += 2
            continue
        print(f"Unknown dream option: {option}")
        raise SystemExit(2)

    from ming.core.dream import DreamEngine

    path = DreamEngine().run(mode=mode, limit=limit)
    print(f"Dream report: {path}")


def main(argv: Sequence[str] | None = None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help", "help"):
        print(_help_text())
        raise SystemExit(0)

    if argv and argv[0] == "ui":
        _run_trace_console(argv[1:])
        return

    if argv and argv[0] == "dream":
        _run_dream(argv[1:])
        return

    # Single-message mode
    if argv and not argv[0].startswith("-"):
        user_input = " ".join(argv)
        config = load_config()
        _setup_logging("WARNING")
        if not config.llm.api_key:
            print("Error: No API key. Set MING_LLM_API_KEY or config/local.yaml")
            sys.exit(1)
        from ming.core.agent import Agent

        agent = Agent(config)
        response = agent.chat_sync(user_input)
        print(response)
        return

    # Interactive mode
    print_banner()
    try:
        asyncio.run(interactive_loop())
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


if __name__ == "__main__":
    main()
