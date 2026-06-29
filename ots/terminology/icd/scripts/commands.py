from __future__ import annotations

import typer

from ots.cli.runner import run_module_main

CONTEXT_SETTINGS = {"allow_extra_args": True, "ignore_unknown_options": True}

app = typer.Typer(help="ICD commands.", no_args_is_help=True)


@app.command("download", context_settings=CONTEXT_SETTINGS)
def download(ctx: typer.Context) -> None:
    """Download official ICD release files."""
    raise typer.Exit(
        run_module_main("ots.terminology.icd.scripts.download_releases", ctx.args)
    )


@app.command("load-10cm", context_settings=CONTEXT_SETTINGS)
def load_10cm(ctx: typer.Context) -> None:
    """Load ICD-10-CM data."""
    raise typer.Exit(
        run_module_main("ots.terminology.icd.scripts.load_icd10cm_postgres", ctx.args)
    )


@app.command("load-11", context_settings=CONTEXT_SETTINGS)
def load_11(ctx: typer.Context) -> None:
    """Load ICD-11 MMS data."""
    raise typer.Exit(
        run_module_main("ots.terminology.icd.scripts.load_icd11_postgres", ctx.args)
    )
