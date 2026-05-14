"""`clerk mcp` subcommands."""

from __future__ import annotations

import typer

app = typer.Typer(help="MCP server for agent integration.", no_args_is_help=True)


@app.command("serve")
def serve() -> None:
    """Run the MCP server over stdio. Token is read from CLERK_TOKEN at startup."""
    from clerk.mcp_server import run

    run()
