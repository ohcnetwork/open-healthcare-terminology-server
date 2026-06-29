from __future__ import annotations

import typer

from ots.cli.runner import run_module_main

CONTEXT_SETTINGS = {"allow_extra_args": True, "ignore_unknown_options": True}

app = typer.Typer(help="LOINC commands.", no_args_is_help=True)


@app.command("load", context_settings=CONTEXT_SETTINGS)
def load(ctx: typer.Context) -> None:
    """Load a LOINC release."""
    raise typer.Exit(
        run_module_main("ots.terminology.loinc.scripts.load_loinc_postgres", ctx.args)
    )


@app.command("cleanup-codes", context_settings=CONTEXT_SETTINGS)
def cleanup_codes(ctx: typer.Context) -> None:
    """Backfill display LOINC codes."""
    raise typer.Exit(
        run_module_main("ots.terminology.loinc.scripts.cleanup_codes", ctx.args)
    )


@app.command("cleanup-status", context_settings=CONTEXT_SETTINGS)
def cleanup_status(ctx: typer.Context) -> None:
    """Mark discouraged/deprecated LOINC rows inactive."""
    raise typer.Exit(
        run_module_main("ots.terminology.loinc.scripts.cleanup_status", ctx.args)
    )


@app.command("rebuild-search-text", context_settings=CONTEXT_SETTINGS)
def rebuild_search_text(ctx: typer.Context) -> None:
    """Rebuild LOINC search_text."""
    raise typer.Exit(
        run_module_main("ots.terminology.loinc.scripts.rebuild_search_text", ctx.args)
    )
