"""`clerk config` subcommands."""

from __future__ import annotations

import typer

from clerk.config import config_path, load_config, save_config

app = typer.Typer(help="Read and modify clerk configuration.", no_args_is_help=True)


@app.command("path")
def show_path() -> None:
    """Print the path to the config file."""
    typer.echo(str(config_path()))


@app.command("show")
def show() -> None:
    """Print the current config (sensitive token hashes omitted)."""
    cfg = load_config()
    typer.echo(f"global_write_enabled = {cfg.security.global_write_enabled}")
    typer.echo(f"require_token        = {cfg.security.require_token}")
    typer.echo(f"oauth.microsoft.client_id = {cfg.oauth.microsoft.client_id or '<unset>'}")
    typer.echo(f"oauth.google.client_id    = {cfg.oauth.google.client_id or '<unset>'}")
    typer.echo(f"tokens: {len(cfg.tokens)} configured")


@app.command("set")
def set_value(key: str, value: str) -> None:
    """Set a config value. Keys: security.global_write_enabled, security.require_token,
    oauth.microsoft.client_id, oauth.microsoft.authority,
    oauth.google.client_id, oauth.google.client_secret."""
    cfg = load_config()
    match key:
        case "security.global_write_enabled":
            cfg.security.global_write_enabled = _parse_bool(value)
        case "security.require_token":
            new_val = _parse_bool(value)
            if not new_val:
                typer.secho(
                    "WARNING: disabling require_token means ANY shell user on this machine "
                    "can read and write your mail+calendar. Continuing.",
                    fg=typer.colors.YELLOW,
                    err=True,
                )
            cfg.security.require_token = new_val
        case "oauth.microsoft.client_id":
            cfg.oauth.microsoft.client_id = value
        case "oauth.microsoft.authority":
            cfg.oauth.microsoft.authority = value
        case "oauth.google.client_id":
            cfg.oauth.google.client_id = value
        case "oauth.google.client_secret":
            cfg.oauth.google.client_secret = value
        case _:
            typer.secho(f"Unknown config key: {key}", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)
    save_config(cfg)
    typer.echo(f"set {key} = {value}")


def _parse_bool(s: str) -> bool:
    lowered = s.strip().lower()
    if lowered in ("true", "1", "yes", "on"):
        return True
    if lowered in ("false", "0", "no", "off"):
        return False
    raise typer.BadParameter(f"expected boolean, got {s!r}")
