#!/usr/bin/env python3
"""Download the official ICD-10-CM and ICD-11 release files used by the loaders."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.request import urlretrieve

ICD10CM_APRIL_2026_URL = (
    "https://www.cms.gov/files/zip/april-1-2026-code-descriptions-tabular-order.zip"
)
ICD11_MMS_2026_01_URL = (
    "https://icdcdn.who.int/static/releasefiles/2026-01/SimpleTabulation-ICD-11-MMS-en.zip"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--icd10cm-output", type=Path, default=Path(
        "data/raw/icd10cm/april-1-2026-code-descriptions-tabular-order.zip"
    ))
    parser.add_argument("--icd11-output", type=Path, default=Path(
        "data/raw/icd11/SimpleTabulation-ICD-11-MMS-en-2026-01.zip"
    ))
    return parser.parse_args()


def download(url: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url}")
    urlretrieve(url, output)
    print(f"Wrote {output}")


def main() -> None:
    args = parse_args()
    download(ICD10CM_APRIL_2026_URL, args.icd10cm_output)
    download(ICD11_MMS_2026_01_URL, args.icd11_output)


if __name__ == "__main__":
    main()
