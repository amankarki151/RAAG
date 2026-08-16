"""The `raag` command-line interface.

Three engines, one command surface. This module does nothing but compose the
sub-applications and load environment configuration; every command's actual
work lives in its own module.

    raag sample run <path>
    raag tune run <snapshot>
    raag master index <snapshot>
    raag master refactor <file> "<request>"
"""

from __future__ import annotations

import typer
from dotenv import load_dotenv

from raag_cli import master, sample, tune

__all__ = ["app", "main"]

app = typer.Typer(
    name="raag",
    help="Repository-scale architectural analysis.\n\n"
    "Parse a codebase, quantify its coupling, and scope AI-assisted "
    "refactoring to the files a change can actually reach.",
    add_completion=False,
)

app.add_typer(sample.app, name="sample")
app.add_typer(tune.app, name="tune")
app.add_typer(master.app, name="master")


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    """Show help when invoked with no subcommand.

    Handled explicitly rather than via Typer's no_args_is_help, whose exit
    code on a bare invocation varies across Click versions when the app has
    only sub-Typer groups and no top-level command logic of its own. Exiting
    0 here is guaranteed regardless of that underlying behaviour, and it is
    the contract the test suite pins: a user who types `raag` with nothing
    else gets guidance, not an implicit error.
    """
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=0)


@app.command("version")
def version() -> None:
    """Print component versions."""
    from raag_master import __version__ as master_version
    from raag_tune import __version__ as tune_version

    typer.echo(f"raag        {master_version}")
    typer.echo(f"tune engine {tune_version}")
    typer.echo(f"master engine {master_version}")


def main() -> None:
    """Entry point.

    Environment is loaded once here rather than in each command, so a command
    can be imported and called directly in a test without side effects.
    """
    load_dotenv()
    app()


if __name__ == "__main__":
    main()
