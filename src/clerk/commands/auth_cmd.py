"""`clerk auth` subcommands."""

from __future__ import annotations

import typer

from clerk.auth import google, keychain, microsoft
from clerk.config import Provider, load_accounts, load_config, save_accounts

app = typer.Typer(help="Add, list, and remove authenticated accounts.", no_args_is_help=True)


@app.command("add")
def add(
    provider: Provider = typer.Option(..., "--provider", help="microsoft or google"),
    device_code: bool = typer.Option(
        False,
        "--device-code",
        help="Use device-code flow instead of opening a browser. Useful over SSH.",
    ),
) -> None:
    """Authenticate a new account and persist its refresh token to the Keychain."""
    cfg = load_config()
    reg = load_accounts()

    try:
        if provider == "microsoft":
            result = microsoft.login(cfg.oauth.microsoft, device_code=device_code)
        elif provider == "google":
            if device_code:
                typer.secho(
                    "Google deprecated the OOB / device-code flow in 2022. "
                    "Run without --device-code to use the interactive browser flow.",
                    fg=typer.colors.RED,
                    err=True,
                )
                raise typer.Exit(2)
            result = google.login(cfg.oauth.google)
        else:
            typer.secho(f"Unknown provider {provider!r}.", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)
    except RuntimeError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None

    existing_ids = {a.id for a in reg.accounts}
    if result.account.id in existing_ids:
        reg.accounts = [a for a in reg.accounts if a.id != result.account.id]
    reg.accounts.append(result.account)
    save_accounts(reg)

    typer.secho(
        f"Added {result.account.provider} account: {result.account.email}",
        fg=typer.colors.GREEN,
    )


@app.command("list")
def list_accounts() -> None:
    """List authenticated accounts."""
    reg = load_accounts()
    if not reg.accounts:
        typer.echo("No accounts configured. Add one with: clerk auth add --provider microsoft")
        return
    width = max(len(a.id) for a in reg.accounts)
    for a in reg.accounts:
        name = f" — {a.display_name}" if a.display_name else ""
        typer.echo(f"{a.id:<{width}}  {a.email}{name}")


@app.command("remove")
def remove(account_id: str) -> None:
    """Remove an account from the registry and delete its Keychain entry."""
    reg = load_accounts()
    before = len(reg.accounts)
    reg.accounts = [a for a in reg.accounts if a.id != account_id]
    if len(reg.accounts) == before:
        typer.secho(f"No account with id {account_id!r}.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)
    save_accounts(reg)
    keychain.delete(account_id)
    typer.echo(f"Removed {account_id}")
