"""Discovery and extraction helpers for SNOMED CT RF2 release packages."""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SnomedRf2Package:
    source_path: Path
    package_key: str
    package_version: str
    package_type: str
    group: str
    release_date: str | None
    is_archive: bool
    metadata: dict[str, Any]

    @property
    def source_uri(self) -> str:
        return str(self.source_path)

    @property
    def label(self) -> str:
        return self.metadata.get("label") or self.package_key


def package_key_from_name(name: str) -> str:
    stem = Path(name).stem
    label = re.sub(r"^SnomedCT[_-]?", "", stem, flags=re.IGNORECASE)
    label = re.split(r"RF2", label, flags=re.IGNORECASE)[0]
    label = re.sub(r"([a-z])([A-Z])", r"\1-\2", label)
    label = re.sub(r"([0-9])([A-Z])", r"\1-\2", label)
    label = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", label)
    label = re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-").lower()
    return f"snomed-{label}" if label else "snomed-rf2-package"


def release_date_from_name(name: str) -> str | None:
    match = re.search(r"(20\d{6})", name)
    return match.group(1) if match else None


def package_group(package_key: str) -> str:
    key = package_key.lower()
    if "international" in key:
        return "core"
    if "reference-set" in key or "reference-sets" in key:
        return "india-reference-sets"
    if "india" in key:
        return "india-extensions"
    return "extensions"


def package_type(group: str) -> str:
    if group == "core":
        return "release"
    if group.endswith("reference-sets"):
        return "reference_set"
    return "extension"


def package_from_path(path: Path) -> SnomedRf2Package:
    key = package_key_from_name(path.name)
    release_date = release_date_from_name(path.name)
    group = package_group(key)
    return SnomedRf2Package(
        source_path=path,
        package_key=key,
        package_version=release_date or "unknown",
        package_type=package_type(group),
        group=group,
        release_date=release_date,
        is_archive=path.is_file(),
        metadata={
            "label": path.stem,
            "sourceFile": path.name,
            "group": group,
            "releaseDate": release_date,
            "archive": path.is_file(),
        },
    )


def is_snomed_rf2_package_path(path: Path) -> bool:
    name = path.name.casefold()
    return "snomedct" in name or "rf2" in name


def discover_snomed_rf2_packages(source_dir: Path) -> list[SnomedRf2Package]:
    packages_by_identity: dict[tuple[str, str], SnomedRf2Package] = {}
    candidates = [source_dir, *source_dir.rglob("*")] if source_dir.exists() else []
    for path in sorted(candidates):
        if any(part.startswith(".") for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() != ".zip":
            continue
        if path.is_dir() and not has_snapshot_dir(path):
            continue
        if not is_snomed_rf2_package_path(path):
            continue

        package = package_from_path(path)
        identity = (package.package_key, package.package_version)
        existing = packages_by_identity.get(identity)
        if existing is None or (package.is_archive and not existing.is_archive):
            packages_by_identity[identity] = package
    return sorted(packages_by_identity.values(), key=package_sort_key)


def package_sort_key(package: SnomedRf2Package) -> tuple[int, str, str]:
    group_order = {
        "core": 10,
        "india-extensions": 100,
        "india-reference-sets": 200,
        "extensions": 300,
    }
    return (
        group_order.get(package.group, 999),
        package.package_version,
        package.package_key,
    )


def has_snapshot_dir(path: Path) -> bool:
    return find_snapshot_dir(path) is not None


def find_snapshot_dir(path: Path) -> Path | None:
    direct = path / "Snapshot"
    if direct.is_dir():
        return direct
    for candidate in sorted(path.glob("*/Snapshot")):
        if candidate.is_dir():
            return candidate
    return None


def package_root(path: Path) -> Path:
    if (path / "Snapshot").is_dir():
        return path
    children = [child for child in path.iterdir() if child.is_dir()]
    if len(children) == 1 and (children[0] / "Snapshot").is_dir():
        return children[0]
    snapshot = find_snapshot_dir(path)
    if snapshot is None:
        raise FileNotFoundError(f"No Snapshot directory found under {path}")
    return snapshot.parent


def ensure_extracted_package(
    package: SnomedRf2Package,
    *,
    extract_dir: Path,
    force: bool = False,
) -> Path:
    if not package.is_archive:
        return package_root(package.source_path)

    target = extract_dir / package.package_key / package.package_version
    if force and target.exists():
        import shutil

        shutil.rmtree(target)
    if not has_snapshot_dir(target):
        target.mkdir(parents=True, exist_ok=True)
        extract_zip_safely(package.source_path, target)
    return package_root(target)


def extract_zip_safely(zip_path: Path, target: Path) -> None:
    target_root = target.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            destination = (target / member.filename).resolve()
            if not destination.is_relative_to(target_root):
                raise ValueError(
                    f"Refusing to extract unsafe zip member: {member.filename}"
                )
        archive.extractall(target)


def selected_packages(
    packages: Iterable[SnomedRf2Package],
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[SnomedRf2Package]:
    include_terms = [term.casefold() for term in include or [] if term.strip()]
    exclude_terms = [term.casefold() for term in exclude or [] if term.strip()]
    selected: list[SnomedRf2Package] = []
    for package in packages:
        haystack = " ".join(
            [
                package.package_key,
                package.group,
                package.source_path.name,
                package.label,
            ]
        ).casefold()
        if include_terms and not any(term in haystack for term in include_terms):
            continue
        if exclude_terms and any(term in haystack for term in exclude_terms):
            continue
        selected.append(package)
    return selected


def format_package_plan(packages: Iterable[SnomedRf2Package]) -> str:
    lines = ["SNOMED RF2 package plan:"]
    for index, package in enumerate(packages, start=1):
        lines.append(
            f"{index:>2}. {package.package_key} "
            f"version={package.package_version} "
            f"type={package.package_type} "
            f"group={package.group} "
            f"source={package.source_path}"
        )
    return "\n".join(lines)
