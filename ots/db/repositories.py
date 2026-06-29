from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select

from ots.db.models import (
    TerminologyEditionPackage,
    TerminologyReleasePackage,
    TerminologySystem,
    TerminologyVersion,
)
from ots.db.session import session_scope


def terminology_to_dict(row: TerminologySystem) -> dict[str, Any]:
    return {
        "terminology_key": row.terminology_key,
        "name": row.name,
        "concept_table": row.concept_table,
        "kind": row.kind,
        "description": row.description,
        "metadata": row.metadata_json,
        "keywords": row.keywords,
        "connections": row.connections,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def terminology_version_to_dict(row: TerminologyVersion) -> dict[str, Any]:
    return {
        "terminology_key": row.terminology_key,
        "version_key": row.version_key,
        "version_label": row.version_label,
        "edition_type": row.edition_type,
        "base_version_key": row.base_version_key,
        "concept_table": row.concept_table,
        "is_default": row.is_default,
        "metadata": row.metadata_json,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def release_package_to_dict(row: TerminologyReleasePackage) -> dict[str, Any]:
    return {
        "terminology_key": row.terminology_key,
        "package_key": row.package_key,
        "package_version": row.package_version,
        "package_type": row.package_type,
        "description": row.description,
        "source_uri": row.source_uri,
        "metadata": row.metadata_json,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def edition_package_to_dict(row: TerminologyEditionPackage) -> dict[str, Any]:
    return {
        "terminology_key": row.terminology_key,
        "version_key": row.version_key,
        "package_key": row.package_key,
        "package_version": row.package_version,
        "role": row.role,
        "include_order": row.include_order,
        "metadata": row.metadata_json,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def list_terminology_systems() -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.scalars(
            select(TerminologySystem).order_by(TerminologySystem.terminology_key)
        ).all()
        return [terminology_to_dict(row) for row in rows]


def list_terminology_versions(
    terminology_key: str | None = None,
) -> list[dict[str, Any]]:
    with session_scope() as session:
        statement = select(TerminologyVersion)
        if terminology_key:
            statement = statement.where(
                TerminologyVersion.terminology_key == terminology_key
            )
        rows = session.scalars(
            statement.order_by(
                TerminologyVersion.terminology_key,
                TerminologyVersion.is_default.desc(),
                TerminologyVersion.version_key,
            )
        ).all()
        return [terminology_version_to_dict(row) for row in rows]


def list_release_packages(terminology_key: str | None = None) -> list[dict[str, Any]]:
    with session_scope() as session:
        statement = select(TerminologyReleasePackage)
        if terminology_key:
            statement = statement.where(
                TerminologyReleasePackage.terminology_key == terminology_key
            )
        rows = session.scalars(
            statement.order_by(
                TerminologyReleasePackage.terminology_key,
                TerminologyReleasePackage.package_key,
                TerminologyReleasePackage.package_version,
            )
        ).all()
        return [release_package_to_dict(row) for row in rows]


def list_edition_packages(
    *,
    terminology_key: str | None = None,
    version_key: str | None = None,
) -> list[dict[str, Any]]:
    with session_scope() as session:
        statement = select(TerminologyEditionPackage)
        if terminology_key:
            statement = statement.where(
                TerminologyEditionPackage.terminology_key == terminology_key
            )
        if version_key:
            statement = statement.where(
                TerminologyEditionPackage.version_key == version_key
            )
        rows = session.scalars(
            statement.order_by(
                TerminologyEditionPackage.terminology_key,
                TerminologyEditionPackage.version_key,
                TerminologyEditionPackage.include_order,
                TerminologyEditionPackage.package_key,
            )
        ).all()
        return [edition_package_to_dict(row) for row in rows]


def get_terminology_system(terminology_key: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.get(TerminologySystem, terminology_key)
        return terminology_to_dict(row) if row else None


def upsert_terminology_system(
    *,
    terminology_key: str,
    name: str,
    concept_table: str,
    kind: str,
    description: str | None,
    metadata: Any,
    keywords: Sequence[str],
    connections: Any,
) -> dict[str, Any]:
    clean_keywords = [str(item).strip() for item in keywords if str(item).strip()]
    with session_scope() as session:
        row = session.get(TerminologySystem, terminology_key)
        if row is None:
            row = TerminologySystem(
                terminology_key=terminology_key,
                name=name,
                concept_table=concept_table,
                kind=kind,
            )
            session.add(row)
        row.name = name
        row.concept_table = concept_table
        row.kind = kind
        row.description = description
        row.metadata_json = metadata
        row.keywords = clean_keywords
        row.connections = connections
        row.updated_at = func.now()
        session.flush()
        session.refresh(row)
        return terminology_to_dict(row)
