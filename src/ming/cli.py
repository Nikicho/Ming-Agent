"""Ming CLI - interactive chat interface."""

import asyncio
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from ming import __version__
from ming.config import load_config
from ming.core.agent import Agent

console = Console()


def print_banner():
    """Print Ming startup banner."""
    console.print(
        Panel(
            f"[bold cyan]Ming (明)[/bold cyan] v{__version__}\n"
            "[dim]知常曰明，不知常，妄作凶 —— 《道德经》[/dim]\n\n"
            "[green]Type your message to chat. Commands:[/green]\n"
            "  /quit  - Exit\n"
            "  /clear - Clear conversation\n"
            "  /status - Show session info",
            title="[bold]Ming Agent[/bold]",
            border_style="cyan",
        )
    )


async def interactive_loop():
    """Main interactive chat loop."""
    config = load_config()

    # Validate API key
    if not config.llm.api_key:
        console.print(
            "[red]Error: No API key configured.[/red]\n"
            "Set MING_LLM_API_KEY environment variable, or create config/local.yaml with:\n"
            "  llm:\n"
            '    api_key: "your-key-here"'
        )
        sys.exit(1)

    agent = Agent(config)
    console.print(f"[dim]Model: {config.llm.model}[/dim]\n")

    while True:
        try:
            # Get user input
            user_input = console.input("[bold green]You:[/bold green] ").strip()

            if not user_input:
                continue

            # Handle commands
            if user_input.startswith("/"):
                cmd = user_input.lower()
                if cmd in ("/quit", "/exit", "/q"):
                    console.print("[dim]再见。[/dim]")
                    break
                elif cmd == "/clear":
                    agent = Agent(config)
                    console.print("[dim]Conversation cleared.[/dim]\n")
                    continue
                elif cmd == "/status":
                    msg_count = len(agent.messages) - 1  # exclude system prompt
                    console.print(
                        f"[dim]Messages: {msg_count} | "
                        f"Model: {config.llm.model}[/dim]\n"
                    )
                    continue
                else:
                    console.print(f"[yellow]Unknown command: {user_input}[/yellow]\n")
                    continue

            # Call agent
            console.print("[dim]Ming is thinking...[/dim]")
            response = await agent.chat(user_input)

            # Display response
            console.print()
            console.print(Panel(Markdown(response), title="[bold cyan]Ming[/bold cyan]", border_style="cyan"))
            console.print()

        except KeyboardInterrupt:
            console.print("\n[dim]Use /quit to exit.[/dim]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]\n")


def main():
    """Entry point."""
    # Quick single-message mode: `ming "hello"`
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        user_input = " ".join(sys.argv[1:])
        config = load_config()
        if not config.llm.api_key:
            print("Error: No API key. Set MING_LLM_API_KEY or create config/local.yaml")
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
