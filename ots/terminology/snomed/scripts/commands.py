from __future__ import annotations

import typer

from ots.cli.runner import run_module_main

CONTEXT_SETTINGS = {"allow_extra_args": True, "ignore_unknown_options": True}

app = typer.Typer(help="SNOMED CT commands.", no_args_is_help=True)


@app.command("load", context_settings=CONTEXT_SETTINGS)
def load(ctx: typer.Context) -> None:
    """Load one SNOMED RF2 Snapshot package."""
    raise typer.Exit(
        run_module_main("ots.terminology.snomed.scripts.load_snomed_postgres", ctx.args)
    )


@app.command("load-packages", context_settings=CONTEXT_SETTINGS)
def load_packages(ctx: typer.Context) -> None:
    """Discover and load grouped SNOMED RF2 zip packages."""
    raise typer.Exit(
        run_module_main(
            "ots.terminology.snomed.scripts.load_snomed_rf2_packages", ctx.args
        )
    )


@app.command("rebuild-search-text", context_settings=CONTEXT_SETTINGS)
def rebuild_search_text(ctx: typer.Context) -> None:
    """Rebuild SNOMED search_text."""
    raise typer.Exit(
        run_module_main(
            "ots.terminology.snomed.scripts.rebuild_snomed_search_text", ctx.args
        )
    )
