"""`clerk token` subcommands."""

from __future__ import annotations

import typer

from clerk import tokens
from clerk.config import ALL_SCOPES, load_config

app = typer.Typer(help="Create, list, and revoke capability tokens.", no_args_is_help=True)


@app.command("create")
def create(
    scopes: str = typer.Option(
        ...,
        "--scopes",
        help=f"Comma-separated scopes. Valid: {', '.join(ALL_SCOPES)}",
    ),
    note: str = typer.Option("", "--note", help="Free-form label for this token."),
) -> None:
    """Create a new capability token. The raw secret is printed ONCE — save it now."""
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
    try:
        created = tokens.create(scope_list, note=note)  # type: ignore[arg-type]
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from None

    typer.echo(f"Created token {created.id}")
    typer.echo(f"  scopes: {', '.join(created.record.scopes)}")
    if created.record.note:
        typer.echo(f"  note:   {created.record.note}")
    typer.echo("")
    typer.secho(
        "Save this secret now — it will not be shown again:",
        fg=typer.colors.YELLOW,
        bold=True,
    )
    typer.echo(created.raw)


@app.command("list")
def list_tokens() -> None:
    """List capability tokens (hashes never shown)."""
    cfg = load_config()
    if not cfg.tokens:
        typer.echo("No tokens. Create one with: clerk token create --scopes mail:read")
        return
    width = max(len(t.id) for t in cfg.tokens)
    for t in cfg.tokens:
        note = f"  ({t.note})" if t.note else ""
        typer.echo(f"{t.id:<{width}}  {t.created_at}  {', '.join(t.scopes)}{note}")


@app.command("revoke")
def revoke(token_id: str) -> None:
    """Revoke a token by id. The raw secret immediately stops working."""
    if not tokens.revoke(token_id):
        typer.secho(f"No token with id {token_id!r}.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)
    typer.echo(f"Revoked {token_id}")
