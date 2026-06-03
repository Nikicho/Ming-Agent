"""Ming CLI - interactive chat interface."""

import asyncio
import logging
import sys

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
from ming.core.agent import Agent

console = Console()


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False, show_time=False)],
    )
    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("litellm").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def print_banner():
    console.print(
        Panel(
            f"[bold cyan]Ming (明)[/bold cyan] v{__version__}\n"
            "[dim]知常曰明，不知常，妄作凶 —— 《道德经》[/dim]\n\n"
            "[green]Commands:[/green]\n"
            "  /quit    Exit\n"
            "  /clear   Clear conversation\n"
            "  /status  Show session info\n"
            "  /debug   Toggle debug logging",
            title="[bold]Ming Agent[/bold]",
            border_style="cyan",
        )
    )


async def interactive_loop():
    config = load_config()
    _setup_logging(config.logging.level)

    if not config.llm.api_key:
        console.print(
            "[red]Error: No API key configured.[/red]\n"
            "Set MING_LLM_API_KEY env var, or create config/local.yaml:\n"
            "  llm:\n"
            '    api_key: "your-key"'
        )
        sys.exit(1)

    agent = Agent(config)
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
                    agent = Agent(config)
                    console.print("[dim]Conversation cleared.[/dim]\n")
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
                    logging.getLogger("ming").setLevel(getattr(logging, level))
                    console.print(f"[dim]Debug {'ON' if debug_mode else 'OFF'}[/dim]\n")
                    continue
                else:
                    console.print(f"[yellow]Unknown command: {cmd}[/yellow]\n")
                    continue

            # Run agent
            console.print("[dim]Ming is thinking...[/dim]")
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
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]\n")
            if debug_mode:
                import traceback
                console.print(f"[dim]{traceback.format_exc()}[/dim]")


def main():
    # Single-message mode
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        user_input = " ".join(sys.argv[1:])
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
