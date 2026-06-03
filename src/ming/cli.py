"""Ming CLI - interactive chat interface."""

import asyncio
import logging
import sys
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
from ming.core.agent import Agent, AgentProgressEvent

console = Console()


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
            "  /rewind  Remove the last turn from context\n"
            "  /rollback Roll back the latest file tool change\n"
            "  /forget <session|memory|project> Scoped forget\n"
            "  /trace   Show the latest run trace file\n"
            "  /checkpoint Show the latest checkpoint file\n"
            "  /details Toggle detailed progress",
            title="[bold]Ming Agent[/bold]",
            border_style="cyan",
        )
    )


async def interactive_loop():
    config = load_config()
    _setup_logging(config.logging.level)
    loop = asyncio.get_running_loop()
    _install_asyncio_exception_filter(loop)

    if not config.llm.api_key:
        console.print(
            "[red]Error: No API key configured.[/red]\n"
            "Set MING_LLM_API_KEY env var, or create config/local.yaml:\n"
            "  llm:\n"
            '    api_key: "your-key"'
        )
        sys.exit(1)

    detail_mode = False

    def show_progress(event: AgentProgressEvent) -> None:
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
                    console.print(f"[dim]Forgot: {result}[/dim]\n")
                    continue
                elif cmd == "/trace":
                    path = agent.last_trace_path
                    console.print(f"[dim]Latest trace: {path or 'none'}[/dim]\n")
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
            console.print("\n[dim]Use /quit to exit.[/dim]")
        except EOFError:
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

Interactive commands:
  /quit     Exit
  /clear    Clear conversation
  /status   Show token usage, memory count, and pattern count
  /debug    Toggle debug logging
  /compact  Compact old conversation context
  /rewind   Remove the last turn from context
  /rollback Roll back the latest file_write/file_edit change
  /forget   Scoped forget: /forget session|memory|project
  /trace    Show the latest run trace file
  /checkpoint Show the latest checkpoint file
  /details  Toggle detailed progress
"""


def main(argv: Sequence[str] | None = None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help", "help"):
        print(_help_text())
        raise SystemExit(0)

    # Single-message mode
    if argv and not argv[0].startswith("-"):
        user_input = " ".join(argv)
        config = load_config()
        _setup_logging("WARNING")
        if not config.llm.api_key:
            print("Error: No API key. Set MING_LLM_API_KEY or config/local.yaml")
            sys.exit(1)
        agent = Agent(config)
        response = agent.chat_sync(user_input)
        print(response)
        return

    # Interactive mode
    print_banner()
    asyncio.run(interactive_loop())


if __name__ == "__main__":
    main()
