import typer

from clerk import __version__
from clerk.commands import auth_cmd, calendar, config_cmd, mail, mcp_cmd, token_cmd

app = typer.Typer(
    name="clerk",
    help="Unified mail + calendar CLI for Microsoft and Google accounts.",
    no_args_is_help=True,
)
app.add_typer(config_cmd.app, name="config")
app.add_typer(auth_cmd.app, name="auth")
app.add_typer(token_cmd.app, name="token")
app.add_typer(mail.app, name="mail")
app.add_typer(calendar.app, name="calendar")
app.add_typer(mcp_cmd.app, name="mcp")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"clerk {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Print version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    pass


if __name__ == "__main__":
    app()
