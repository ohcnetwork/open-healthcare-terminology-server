from __future__ import annotations

import typer

from ots.cli.common.commands import app as common_app
from ots.terminology.icd.scripts.commands import app as icd_app
from ots.terminology.loinc.scripts.commands import app as loinc_app
from ots.terminology.snomed.scripts.commands import app as snomed_app

app = typer.Typer(
    name="ots",
    help="Open Terminology Server management CLI.",
    no_args_is_help=True,
)

app.add_typer(snomed_app, name="snomed", help="SNOMED CT imports and maintenance.")
app.add_typer(loinc_app, name="loinc", help="LOINC imports and maintenance.")
app.add_typer(icd_app, name="icd", help="ICD downloads and imports.")
app.add_typer(common_app, name="common", help="Database, embeddings, and shared maintenance.")
