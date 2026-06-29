from __future__ import annotations

import typer

from ots.cli.runner import run_module_main

CONTEXT_SETTINGS = {"allow_extra_args": True, "ignore_unknown_options": True}

app = typer.Typer(help="Shared maintenance commands.", no_args_is_help=True)


@app.command("embed", context_settings=CONTEXT_SETTINGS)
def embed(ctx: typer.Context) -> None:
    """Populate embeddings."""
    raise typer.Exit(run_module_main("ots.cli.common.update_concept_embeddings", ctx.args))


@app.command("dump-db", context_settings=CONTEXT_SETTINGS)
def dump_db(ctx: typer.Context) -> None:
    """Dump the Postgres database."""
    raise typer.Exit(run_module_main("ots.cli.common.dump_database", ctx.args))


@app.command("load-db", context_settings=CONTEXT_SETTINGS)
def load_db(ctx: typer.Context) -> None:
    """Restore a database dump."""
    raise typer.Exit(run_module_main("ots.cli.common.load_database_dump", ctx.args))


@app.command("lexical-indexes", context_settings=CONTEXT_SETTINGS)
def lexical_indexes(ctx: typer.Context) -> None:
    """Ensure lexical search indexes."""
    raise typer.Exit(run_module_main("ots.cli.common.ensure_lexical_indexes", ctx.args))


@app.command("resync-edition", context_settings=CONTEXT_SETTINGS)
def resync_edition(ctx: typer.Context) -> None:
    """Refresh a composed edition from a base edition."""
    raise typer.Exit(run_module_main("ots.cli.common.resync_terminology_edition", ctx.args))
