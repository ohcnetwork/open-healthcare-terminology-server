from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ots import config
from ots.db import schema as db_schema
from ots.db.repositories import (
    list_edition_packages,
    list_release_packages,
    list_terminology_systems,
    list_terminology_versions,
    upsert_terminology_system,
)
from ots.terminology import (
    CUSTOM_TERMINOLOGY_KIND,
    DEFAULT_TERMINOLOGY_KEY,
    DEFAULT_TERMINOLOGY_VERSION_KEY,
    IMPORTED_TERMINOLOGY_KEYS,
    get_terminology_definition,
    versioned_concept_table_name,
)
from ots.terminology import (
    concept_table_name as _terminology_concept_table_name,
)
from ots.terminology import (
    normalize_terminology_key as _normalize_terminology_key,
)
from ots.terminology import (
    normalize_version_key as _normalize_version_key,
)

DEFAULT_DATABASE_URL = config.DEFAULT_DATABASE_URL
DEFAULT_EMBEDDING_DIMENSIONS = config.DEFAULT_EMBEDDING_DIMENSIONS
LEGACY_CONCEPT_TABLE = "concept_document"
VECTOR_STORAGE = "vector"
HALFVEC_STORAGE = "halfvec"
HALFVEC_RERANK_STRATEGY = "halfvec_rerank"
FULL_EXACT_STRATEGY = "full_exact"
HALFVEC_ONLY_STRATEGY = "halfvec_only"
MAX_VECTOR_INDEX_DIMENSIONS = 2000
MAX_HALFVEC_INDEX_DIMENSIONS = 4000

BASIC_CONCEPT_SELECTS = (
    ("concept_id", "concept_id"),
    ("payload->>'code'", "code"),
    ("COALESCE(payload->>'displayCode', payload->>'code')", "display_code"),
    ("active", "active"),
    ("fsn", "fsn"),
    ("preferred_term", "preferred_term"),
    ("semantic_tag", "semantic_tag"),
    ("synonyms", "synonyms"),
)

FULL_CONCEPT_SELECTS = (
    *BASIC_CONCEPT_SELECTS,
    ("parent_ids", "parent_ids"),
    ("ancestor_ids", "ancestor_ids"),
    ("child_ids", "child_ids"),
    ("descriptions", "descriptions"),
    ("relationships", "relationships"),
    ("concrete_values", "concrete_values"),
    ("maps", "maps"),
    ("associations", "associations"),
    ("refset_ids", "refset_ids"),
    ("attributes", "attributes"),
    ("search_text", "search_text"),
    ("payload", "payload"),
)

CONCEPT_DOCUMENT_COLUMNS = """
    concept_id,
    active,
    effective_time,
    module_id,
    definition_status_id,
    definition_status,
    fsn,
    preferred_term,
    semantic_tag,
    synonyms,
    text_definitions,
    parent_ids,
    ancestor_ids,
    child_ids,
    descriptions,
    relationships,
    concrete_values,
    maps,
    associations,
    refset_ids,
    attributes,
    search_text,
    embedding,
    embedding_model,
    embedding_updated_at,
    payload,
    updated_at
"""


def concept_columns(
    *,
    table_alias: str | None = None,
    include_details: bool = False,
    projected: bool = False,
) -> str:
    selects = FULL_CONCEPT_SELECTS if include_details else BASIC_CONCEPT_SELECTS
    rendered: list[str] = []
    for expression, alias in selects:
        if projected:
            rendered.append(f"{table_alias}.{alias}" if table_alias else alias)
            continue
        qualified_expression = (
            expression.replace("payload", f"{table_alias}.payload")
            if table_alias and "payload" in expression
            else f"{table_alias}.{expression}"
            if table_alias
            else expression
        )
        if expression == alias:
            rendered.append(qualified_expression)
        else:
            rendered.append(f"{qualified_expression} AS {alias}")
    return ",\n            ".join(rendered)


def _normalized_sql(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.strip().splitlines())


def normalize_terminology_key(terminology_key: str | None = None) -> str:
    return _normalize_terminology_key(terminology_key)


def normalize_version_key(version_key: str | None = None) -> str:
    return _normalize_version_key(version_key)


def concept_table_name(
    terminology_key: str | None = None,
    terminology_version: str | None = None,
) -> str:
    if terminology_version not in (None, ""):
        return versioned_concept_table_name(terminology_key, terminology_version)
    return _terminology_concept_table_name(terminology_key)


def _concept_columns_list() -> list[str]:
    return [
        name.strip() for name in CONCEPT_DOCUMENT_COLUMNS.split(",") if name.strip()
    ]


def _query_payload(sql_text: str, params: Sequence[Any]) -> dict[str, Any]:
    return {
        "sql": _normalized_sql(sql_text),
        "parameters": list(params),
    }


def _vector_query_payload(
    *,
    sql_text: str,
    params: Sequence[Any],
    vector: str,
    dimensions: int,
) -> dict[str, Any]:
    redacted_params = [
        f"<embedding:{dimensions} dimensions>" if value == vector else value
        for value in params
    ]
    payload = _query_payload(sql_text, redacted_params)
    payload["sessionSettings"] = {
        "hnsw.iterative_scan": "relaxed_order",
        "hnsw.ef_search": 100,
    }
    return payload


def resolve_embedding_storage_type(
    *,
    dimensions: int,
    requested_storage_type: str | None = None,
) -> str:
    storage_type = (requested_storage_type or "auto").strip().lower()
    if storage_type in {"auto", ""}:
        return (
            HALFVEC_STORAGE
            if dimensions > MAX_VECTOR_INDEX_DIMENSIONS
            else VECTOR_STORAGE
        )
    if storage_type not in {VECTOR_STORAGE, HALFVEC_STORAGE}:
        raise ValueError("storage_type must be one of: auto, vector, halfvec")
    if storage_type == HALFVEC_STORAGE and dimensions > MAX_HALFVEC_INDEX_DIMENSIONS:
        raise ValueError(
            f"halfvec HNSW indexing supports up to {MAX_HALFVEC_INDEX_DIMENSIONS} dimensions"
        )
    return storage_type


def database_url() -> str:
    return config.DATABASE_URL


def connect_db():
    return psycopg.connect(database_url(), row_factory=dict_row)


def _create_index_if_missing(cur, index_name: str, statement) -> None:
    db_schema.create_index_if_missing(cur, index_name, statement)


def _table_columns(conn, table_name: str) -> set[str]:
    return db_schema.table_columns(conn, table_name)


def _ensure_column(conn, table_name: str, column_name: str, ddl_fragment: str) -> None:
    db_schema.ensure_column(conn, table_name, column_name, ddl_fragment)


def _create_concept_document_table(
    conn,
    *,
    terminology_key: str,
    terminology_version: str | None = None,
    embedding_dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS,
) -> str:
    return db_schema.create_concept_document_table(
        conn,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
        embedding_dimensions=embedding_dimensions,
    )


def _default_version_row(conn, terminology_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM terminology_version
        WHERE terminology_key = %s
          AND is_default
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (terminology_key,),
    ).fetchone()
    return dict(row) if row else None


def _terminology_version_row(
    conn,
    terminology_key: str,
    terminology_version: str | None = None,
) -> dict[str, Any] | None:
    if terminology_version in (None, ""):
        return _default_version_row(conn, terminology_key)
    version_key = normalize_version_key(terminology_version)
    row = conn.execute(
        """
        SELECT *
        FROM terminology_version
        WHERE terminology_key = %s
          AND version_key = %s
        """,
        (terminology_key, version_key),
    ).fetchone()
    return dict(row) if row else None


def _upsert_terminology_version(
    conn,
    *,
    terminology_key: str,
    terminology_version: str | None,
    concept_table: str,
    is_default: bool = False,
    version_label: str | None = None,
    edition_type: str = "standalone",
    base_version_key: str | None = None,
    metadata: Any | None = None,
) -> dict[str, Any]:
    version_key = normalize_version_key(terminology_version)
    if is_default:
        conn.execute(
            """
            UPDATE terminology_version
            SET is_default = false,
                updated_at = now()
            WHERE terminology_key = %s
              AND is_default
            """,
            (terminology_key,),
        )
    row = conn.execute(
        """
        INSERT INTO terminology_version (
            terminology_key,
            version_key,
            version_label,
            edition_type,
            base_version_key,
            concept_table,
            is_default,
            metadata,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
        ON CONFLICT (terminology_key, version_key) DO UPDATE SET
            version_label = COALESCE(excluded.version_label, terminology_version.version_label),
            edition_type = COALESCE(excluded.edition_type, terminology_version.edition_type),
            base_version_key = COALESCE(excluded.base_version_key, terminology_version.base_version_key),
            concept_table = excluded.concept_table,
            is_default = CASE
                WHEN excluded.is_default THEN true
                ELSE terminology_version.is_default
            END,
            metadata = terminology_version.metadata || excluded.metadata,
            updated_at = now()
        RETURNING *
        """,
        (
            terminology_key,
            version_key,
            version_label,
            edition_type,
            base_version_key,
            concept_table,
            bool(is_default),
            Jsonb(metadata if metadata is not None else {}),
        ),
    ).fetchone()
    return dict(row)


def resolve_terminology_version(
    conn,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
) -> dict[str, Any]:
    terminology_key = normalize_terminology_key(terminology_key)
    db_schema.ensure_base_schema(conn)
    row = _terminology_version_row(conn, terminology_key, terminology_version)
    if row is not None:
        return row
    version_key = normalize_version_key(terminology_version)
    concept_table = versioned_concept_table_name(terminology_key, version_key)
    return _upsert_terminology_version(
        conn,
        terminology_key=terminology_key,
        terminology_version=version_key,
        concept_table=concept_table,
        is_default=terminology_version in (None, ""),
        version_label="Current import"
        if version_key == DEFAULT_TERMINOLOGY_VERSION_KEY
        else version_key,
    )


def concept_table_for(
    conn,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
) -> str:
    version = resolve_terminology_version(
        conn,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
    )
    return str(version["concept_table"])


def register_release_package(
    conn,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    package_key: str,
    package_version: str,
    package_type: str = "release",
    description: str | None = None,
    source_uri: str | None = None,
    metadata: Any | None = None,
) -> dict[str, Any]:
    terminology_key = normalize_terminology_key(terminology_key)
    row = conn.execute(
        """
        INSERT INTO terminology_release_package (
            terminology_key,
            package_key,
            package_version,
            package_type,
            description,
            source_uri,
            metadata,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, now())
        ON CONFLICT (terminology_key, package_key, package_version) DO UPDATE SET
            package_type = excluded.package_type,
            description = COALESCE(excluded.description, terminology_release_package.description),
            source_uri = COALESCE(excluded.source_uri, terminology_release_package.source_uri),
            metadata = terminology_release_package.metadata || excluded.metadata,
            updated_at = now()
        RETURNING *
        """,
        (
            terminology_key,
            str(package_key).strip(),
            str(package_version).strip(),
            str(package_type or "release").strip(),
            description,
            source_uri,
            Jsonb(metadata if metadata is not None else {}),
        ),
    ).fetchone()
    return dict(row)


def link_package_to_edition(
    conn,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
    package_key: str,
    package_version: str,
    role: str = "primary",
    include_order: int = 100,
    metadata: Any | None = None,
) -> dict[str, Any]:
    terminology_key = normalize_terminology_key(terminology_key)
    version_key = normalize_version_key(terminology_version)
    row = conn.execute(
        """
        INSERT INTO terminology_edition_package (
            terminology_key,
            version_key,
            package_key,
            package_version,
            role,
            include_order,
            metadata,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, now())
        ON CONFLICT (terminology_key, version_key, package_key, package_version) DO UPDATE SET
            role = excluded.role,
            include_order = excluded.include_order,
            metadata = terminology_edition_package.metadata || excluded.metadata,
            updated_at = now()
        RETURNING *
        """,
        (
            terminology_key,
            version_key,
            str(package_key).strip(),
            str(package_version).strip(),
            str(role or "primary").strip(),
            int(include_order),
            Jsonb(metadata if metadata is not None else {}),
        ),
    ).fetchone()
    return dict(row)


def _copy_legacy_snomed_if_needed(conn) -> None:
    snomed_table = concept_table_name(DEFAULT_TERMINOLOGY_KEY)
    if not _table_exists(conn, LEGACY_CONCEPT_TABLE) or not _table_exists(
        conn, snomed_table
    ):
        return
    target_count = conn.execute(
        sql.SQL("SELECT COUNT(*) AS count FROM {table_name}").format(
            table_name=sql.Identifier(snomed_table)
        )
    ).fetchone()["count"]
    if int(target_count) > 0:
        return
    columns = _concept_columns_list()
    column_sql = sql.SQL(", ").join(sql.Identifier(column) for column in columns)
    conn.execute(
        sql.SQL(
            """
            INSERT INTO {target_table} ({columns})
            SELECT {columns}
            FROM {source_table}
            ON CONFLICT (concept_id) DO NOTHING
            """
        ).format(
            target_table=sql.Identifier(snomed_table),
            source_table=sql.Identifier(LEGACY_CONCEPT_TABLE),
            columns=column_sql,
        )
    )


def resync_terminology_edition(
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    source_version: str,
    target_version: str,
    clear_inherited: bool = True,
) -> dict[str, Any]:
    terminology_key = normalize_terminology_key(terminology_key)
    source_version_key = normalize_version_key(source_version)
    target_version_key = normalize_version_key(target_version)
    if source_version_key == target_version_key:
        raise ValueError("source_version and target_version must be different")

    with connect_db() as conn:
        init_schema(
            conn,
            terminology_key=terminology_key,
            terminology_version=source_version_key,
        )
        init_schema(
            conn,
            terminology_key=terminology_key,
            terminology_version=target_version_key,
            edition_type="composed",
            base_version_key=source_version_key,
        )
        source = resolve_terminology_version(
            conn,
            terminology_key=terminology_key,
            terminology_version=source_version_key,
        )
        target = resolve_terminology_version(
            conn,
            terminology_key=terminology_key,
            terminology_version=target_version_key,
        )
        source_table = str(source["concept_table"])
        target_table = str(target["concept_table"])
        deleted = 0
        if clear_inherited:
            cursor = conn.execute(
                sql.SQL(
                    """
                    DELETE FROM {target_table}
                    WHERE payload->>'inheritedFromTerminology' = %s
                      AND payload->>'inheritedFromEdition' = %s
                    """
                ).format(target_table=sql.Identifier(target_table)),
                (terminology_key, source_version_key),
            )
            deleted = int(cursor.rowcount or 0)

        columns = [
            column for column in _concept_columns_list() if column != "updated_at"
        ]
        insert_columns = sql.SQL(", ").join(
            sql.Identifier(column) for column in columns
        )
        select_columns = []
        for column in columns:
            if column == "payload":
                select_columns.append(
                    sql.SQL(
                        """
                        payload || jsonb_build_object(
                            'inheritedFromTerminology', %s::text,
                            'inheritedFromEdition', %s::text,
                            'inheritedFromConceptTable', %s::text
                        ) AS payload
                        """
                    )
                )
            else:
                select_columns.append(sql.Identifier(column))
        update_columns = sql.SQL(", ").join(
            sql.SQL("{column} = excluded.{column}").format(
                column=sql.Identifier(column)
            )
            for column in columns
            if column != "concept_id"
        )
        cursor = conn.execute(
            sql.SQL(
                """
                INSERT INTO {target_table} ({insert_columns}, updated_at)
                SELECT {select_columns}, now()
                FROM {source_table}
                ON CONFLICT (concept_id) DO UPDATE SET
                    {update_columns},
                    updated_at = now()
                WHERE {target_table}.payload->>'inheritedFromTerminology' = %s
                  AND {target_table}.payload->>'inheritedFromEdition' = %s
                """
            ).format(
                target_table=sql.Identifier(target_table),
                source_table=sql.Identifier(source_table),
                insert_columns=insert_columns,
                select_columns=sql.SQL(", ").join(select_columns),
                update_columns=update_columns,
            ),
            (
                terminology_key,
                source_version_key,
                source_table,
                terminology_key,
                source_version_key,
            ),
        )
        copied_or_updated = int(cursor.rowcount or 0)
        conn.execute(
            """
            INSERT INTO terminology_edition_package (
                terminology_key,
                version_key,
                package_key,
                package_version,
                role,
                include_order,
                metadata,
                updated_at
            )
            SELECT
                terminology_key,
                %s,
                package_key,
                package_version,
                'base',
                include_order,
                metadata || jsonb_build_object('copiedFromEdition', %s::text),
                now()
            FROM terminology_edition_package
            WHERE terminology_key = %s
              AND version_key = %s
            ON CONFLICT (terminology_key, version_key, package_key, package_version) DO UPDATE SET
                role = excluded.role,
                include_order = excluded.include_order,
                metadata = terminology_edition_package.metadata || excluded.metadata,
                updated_at = now()
            """,
            (
                target_version_key,
                source_version_key,
                terminology_key,
                source_version_key,
            ),
        )
        _upsert_terminology_version(
            conn,
            terminology_key=terminology_key,
            terminology_version=target_version_key,
            concept_table=target_table,
            edition_type="composed",
            base_version_key=source_version_key,
            metadata={"resyncedFromEdition": source_version_key},
        )
        conn.commit()
    return {
        "terminology": terminology_key,
        "sourceVersion": source_version_key,
        "targetVersion": target_version_key,
        "sourceConceptTable": source_table,
        "targetConceptTable": target_table,
        "deletedInheritedRows": deleted,
        "copiedOrUpdatedRows": copied_or_updated,
    }


def init_schema(
    conn,
    *,
    embedding_dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
    set_default_version: bool = False,
    edition_type: str | None = None,
    base_version_key: str | None = None,
    package_key: str | None = None,
    package_version: str | None = None,
    package_type: str = "release",
    package_role: str = "primary",
    package_source_uri: str | None = None,
    package_metadata: Any | None = None,
) -> None:
    terminology_key = normalize_terminology_key(terminology_key)
    version_key = normalize_version_key(terminology_version)
    terminology = get_terminology_definition(terminology_key)
    db_schema.ensure_base_schema(conn)
    concept_table = _create_concept_document_table(
        conn,
        terminology_key=terminology_key,
        terminology_version=version_key,
        embedding_dimensions=embedding_dimensions,
    )
    conn.execute(
        """
        INSERT INTO terminology_system (
            terminology_key,
            name,
            concept_table,
            kind,
            description,
            metadata,
            keywords,
            connections,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, '{}'::jsonb, '{}', '[]'::jsonb, now())
        ON CONFLICT (terminology_key) DO UPDATE SET
            concept_table = excluded.concept_table,
            kind = CASE
                WHEN terminology_system.kind = 'custom' THEN terminology_system.kind
                ELSE excluded.kind
            END,
            updated_at = now()
        """,
        (
            terminology_key,
            terminology.name,
            concept_table,
            terminology.kind,
            terminology.description,
        ),
    )
    if terminology_key == DEFAULT_TERMINOLOGY_KEY:
        _copy_legacy_snomed_if_needed(conn)
    _upsert_terminology_version(
        conn,
        terminology_key=terminology_key,
        terminology_version=version_key,
        concept_table=concept_table,
        is_default=set_default_version or terminology_version in (None, ""),
        version_label="Current import"
        if version_key == DEFAULT_TERMINOLOGY_VERSION_KEY
        else version_key,
        edition_type=edition_type or ("composed" if base_version_key else "standalone"),
        base_version_key=normalize_version_key(base_version_key)
        if base_version_key
        else None,
    )
    should_register_package = (
        any(
            value is not None
            for value in (
                package_key,
                package_version,
                package_source_uri,
                package_metadata,
            )
        )
        or package_type != "release"
        or package_role != "primary"
    )
    if should_register_package:
        package_key = (package_key or f"{terminology_key}-{version_key}").strip()
        package_version = (package_version or version_key).strip()
        register_release_package(
            conn,
            terminology_key=terminology_key,
            package_key=package_key,
            package_version=package_version,
            package_type=package_type,
            source_uri=package_source_uri,
            metadata=package_metadata,
        )
        link_package_to_edition(
            conn,
            terminology_key=terminology_key,
            terminology_version=version_key,
            package_key=package_key,
            package_version=package_version,
            role=package_role,
            include_order=100 if package_role == "primary" else 200,
        )
    db_schema.ensure_embedding_model_schema(conn)
    conn.commit()


def database_status(
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
) -> dict[str, Any]:
    terminology_key = normalize_terminology_key(terminology_key)
    with connect_db() as conn:
        init_schema(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        version = resolve_terminology_version(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        concept_table = str(version["concept_table"])
        row = conn.execute(
            sql.SQL(
                """
            SELECT
                COUNT(*) AS concept_count,
                COUNT(*) FILTER (WHERE active) AS active_concept_count,
                MAX(updated_at) AS last_updated_at
            FROM {concept_table}
            """
            ).format(concept_table=sql.Identifier(concept_table))
        ).fetchone()
        embedded_row_count = 0
        for model in _embedding_models(
            conn,
            terminology_key=terminology_key,
            terminology_version=str(version["version_key"]),
        ):
            table_name = model.get("embedding_table") or embedding_table_name(
                model_key=str(model["model_key"]),
                dimensions=int(model["dimensions"]),
                terminology_key=terminology_key,
                terminology_version=str(version["version_key"]),
            )
            if _table_exists(conn, table_name):
                model_row = conn.execute(
                    sql.SQL("SELECT COUNT(*) AS count FROM {table_name}").format(
                        table_name=sql.Identifier(table_name),
                    )
                ).fetchone()
                embedded_row_count += int(model_row["count"])
    payload = dict(row)
    payload["terminology"] = terminology_key
    payload["version"] = version["version_key"]
    payload["is_default_version"] = version["is_default"]
    payload["concept_table"] = concept_table
    payload["embedded_row_count"] = embedded_row_count
    return payload


def list_terminologies() -> list[dict[str, Any]]:
    with connect_db() as conn:
        init_schema(conn, terminology_key=DEFAULT_TERMINOLOGY_KEY)
        rows = list_terminology_systems()
        versions_by_terminology: dict[str, list[dict[str, Any]]] = {}
        for version in list_terminology_versions():
            versions_by_terminology.setdefault(
                str(version["terminology_key"]), []
            ).append(version)
        edition_packages_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for package in list_edition_packages():
            edition_packages_by_key.setdefault(
                (str(package["terminology_key"]), str(package["version_key"])),
                [],
            ).append(package)
        release_packages_by_terminology: dict[str, list[dict[str, Any]]] = {}
        for package in list_release_packages():
            release_packages_by_terminology.setdefault(
                str(package["terminology_key"]),
                [],
            ).append(package)
        results = []
        for row in rows:
            item = dict(row)
            versions = versions_by_terminology.get(str(item["terminology_key"]), [])
            for version in versions:
                version["packages"] = edition_packages_by_key.get(
                    (str(version["terminology_key"]), str(version["version_key"])),
                    [],
                )
            default_version = next(
                (version for version in versions if version["is_default"]), None
            )
            concept_table = str(
                default_version["concept_table"]
                if default_version
                else item["concept_table"]
            )
            item["default_version"] = (
                default_version["version_key"] if default_version else None
            )
            item["versions"] = versions
            item["release_packages"] = release_packages_by_terminology.get(
                str(item["terminology_key"]),
                [],
            )
            if _table_exists(conn, concept_table):
                count_row = conn.execute(
                    sql.SQL("SELECT COUNT(*) AS count FROM {concept_table}").format(
                        concept_table=sql.Identifier(concept_table),
                    )
                ).fetchone()
                item["concept_count"] = int(count_row["count"])
            else:
                item["concept_count"] = 0
            model_row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM embedding_model
                WHERE terminology_key = %s
                """,
                (item["terminology_key"],),
            ).fetchone()
            item["model_count"] = int(model_row["count"])
            results.append(item)
    return results


def _terminology_system_row(conn, terminology_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT
            terminology_key,
            name,
            concept_table,
            kind,
            description,
            metadata,
            keywords,
            connections,
            created_at,
            updated_at
        FROM terminology_system
        WHERE terminology_key = %s
        """,
        (terminology_key,),
    ).fetchone()
    return dict(row) if row else None


def _ensure_custom_code_index(conn, terminology_key: str) -> None:
    db_schema.ensure_custom_code_index(conn, terminology_key=terminology_key)


def create_custom_terminology(
    *,
    terminology_key: str,
    name: str | None = None,
    description: str | None = None,
    metadata: Any | None = None,
    keywords: Sequence[str] | None = None,
    connections: Any | None = None,
) -> dict[str, Any]:
    terminology_key = normalize_terminology_key(terminology_key)
    if terminology_key in IMPORTED_TERMINOLOGY_KEYS:
        raise ValueError(
            f"{terminology_key!r} is an imported terminology and cannot be created"
        )
    clean_keywords = [
        str(item).strip() for item in (keywords or []) if str(item).strip()
    ]
    terminology = get_terminology_definition(
        terminology_key,
        name=name,
        description=description,
    )
    with connect_db() as conn:
        init_schema(conn, terminology_key=terminology_key)
        _ensure_custom_code_index(conn, terminology_key)
        row = _terminology_system_row(conn, terminology_key)
        if row and row["kind"] not in {CUSTOM_TERMINOLOGY_KIND, None}:
            raise ValueError(f"{terminology_key!r} is not a custom terminology")
        conn.commit()
    return upsert_terminology_system(
        terminology_key=terminology_key,
        name=name or terminology.name,
        concept_table=concept_table_name(terminology_key),
        kind=CUSTOM_TERMINOLOGY_KIND,
        description=description,
        metadata=metadata if metadata is not None else {},
        keywords=clean_keywords,
        connections=connections if connections is not None else [],
    )


def delete_custom_terminology(*, terminology_key: str) -> bool:
    terminology_key = normalize_terminology_key(terminology_key)
    if terminology_key in IMPORTED_TERMINOLOGY_KEYS:
        raise ValueError(
            f"{terminology_key!r} is an imported terminology and cannot be deleted"
        )
    with connect_db() as conn:
        init_schema(conn, terminology_key=DEFAULT_TERMINOLOGY_KEY)
        row = _terminology_system_row(conn, terminology_key)
        if row is None:
            return False
        if row["kind"] != CUSTOM_TERMINOLOGY_KIND:
            raise ValueError(f"{terminology_key!r} is not a custom terminology")
        version_rows = conn.execute(
            """
            SELECT concept_table
            FROM terminology_version
            WHERE terminology_key = %s
            """,
            (terminology_key,),
        ).fetchall()
        embedding_rows = conn.execute(
            """
            SELECT embedding_table
            FROM embedding_model
            WHERE terminology_key = %s
              AND embedding_table IS NOT NULL
            """,
            (terminology_key,),
        ).fetchall()
        for embedding_row in embedding_rows:
            conn.execute(
                sql.SQL("DROP TABLE IF EXISTS {table_name}").format(
                    table_name=sql.Identifier(str(embedding_row["embedding_table"]))
                )
            )
        conn.execute(
            "DELETE FROM embedding_model WHERE terminology_key = %s", (terminology_key,)
        )
        for version_row in version_rows:
            conn.execute(
                sql.SQL("DROP TABLE IF EXISTS {table_name} CASCADE").format(
                    table_name=sql.Identifier(str(version_row["concept_table"]))
                )
            )
        if not version_rows:
            conn.execute(
                sql.SQL("DROP TABLE IF EXISTS {table_name} CASCADE").format(
                    table_name=sql.Identifier(str(row["concept_table"]))
                )
            )
        conn.execute(
            "DELETE FROM terminology_edition_package WHERE terminology_key = %s",
            (terminology_key,),
        )
        conn.execute(
            "DELETE FROM terminology_release_package WHERE terminology_key = %s",
            (terminology_key,),
        )
        conn.execute(
            "DELETE FROM terminology_version WHERE terminology_key = %s",
            (terminology_key,),
        )
        conn.execute(
            "DELETE FROM terminology_system WHERE terminology_key = %s",
            (terminology_key,),
        )
        conn.commit()
    return True


def _require_custom_terminology(conn, terminology_key: str) -> dict[str, Any]:
    init_schema(conn, terminology_key=DEFAULT_TERMINOLOGY_KEY)
    row = _terminology_system_row(conn, terminology_key)
    if row is None:
        raise ValueError(f"Custom terminology {terminology_key!r} has not been created")
    if row["kind"] != CUSTOM_TERMINOLOGY_KIND:
        raise ValueError(
            f"{terminology_key!r} is an imported terminology and is read-only"
        )
    _create_concept_document_table(conn, terminology_key=terminology_key)
    _ensure_custom_code_index(conn, terminology_key)
    return row


def upsert_custom_record(
    *,
    terminology_key: str,
    code: str,
    display: str | None = None,
    description: str | None = None,
    metadata: Any | None = None,
    keywords: Sequence[str] | None = None,
    connections: Any | None = None,
    active: bool = True,
    semantic_tag: str | None = None,
) -> dict[str, Any]:
    terminology_key = normalize_terminology_key(terminology_key)
    normalized_code = str(code).strip()
    if not normalized_code:
        raise ValueError("code is required")
    clean_keywords = [
        str(item).strip() for item in (keywords or []) if str(item).strip()
    ]
    display_text = " ".join(str(display or normalized_code).split())
    description_text = " ".join(str(description).split()) if description else None
    tag = str(semantic_tag or "custom").strip() or "custom"
    terminology = get_terminology_definition(terminology_key)
    concept_id = terminology.code_to_concept_id(normalized_code)
    search_text = terminology.record_search_text(
        display=display_text,
        description=description_text,
        keywords=clean_keywords,
    )
    descriptions: list[dict[str, Any]] = [
        {
            "term": display_text,
            "type": "display",
            "active": True,
            "languageCode": "en",
        }
    ]
    if description_text:
        descriptions.append(
            {
                "term": description_text,
                "type": "description",
                "active": True,
                "languageCode": "en",
            }
        )
    descriptions.extend(
        {
            "term": keyword,
            "type": "keyword",
            "active": True,
            "languageCode": "en",
        }
        for keyword in clean_keywords
    )
    payload = {
        "code": normalized_code,
        "display": display_text,
        "description": description_text,
        "metadata": metadata if metadata is not None else {},
        "keywords": clean_keywords,
        "connections": connections if connections is not None else [],
        "terminology": terminology_key,
        "kind": CUSTOM_TERMINOLOGY_KIND,
    }
    row_data = {
        "concept_id": concept_id,
        "active": active,
        "effective_time": 0,
        "module_id": 0,
        "definition_status_id": 0,
        "definition_status": CUSTOM_TERMINOLOGY_KIND,
        "fsn": display_text,
        "preferred_term": display_text,
        "semantic_tag": tag,
        "synonyms": clean_keywords,
        "text_definitions": [description_text] if description_text else [],
        "parent_ids": [],
        "ancestor_ids": [],
        "child_ids": [],
        "descriptions": Jsonb(descriptions),
        "relationships": Jsonb([]),
        "concrete_values": Jsonb([]),
        "maps": Jsonb({}),
        "associations": Jsonb(connections if connections is not None else []),
        "refset_ids": [],
        "attributes": Jsonb([]),
        "search_text": search_text,
        "embedding": None,
        "embedding_model": None,
        "embedding_updated_at": None,
        "payload": Jsonb(payload),
    }
    concept_table = concept_table_name(terminology_key)
    columns = [column for column in _concept_columns_list() if column != "updated_at"]
    insert_columns = sql.SQL(", ").join(sql.Identifier(column) for column in columns)
    value_columns = sql.SQL(", ").join(sql.Placeholder(column) for column in columns)
    update_columns = sql.SQL(", ").join(
        sql.SQL("{column} = excluded.{column}").format(column=sql.Identifier(column))
        for column in columns
        if column != "concept_id"
    )
    with connect_db() as conn:
        _require_custom_terminology(conn, terminology_key)
        row = conn.execute(
            sql.SQL(
                """
                INSERT INTO {concept_table} ({insert_columns}, updated_at)
                VALUES ({value_columns}, now())
                ON CONFLICT (concept_id) DO UPDATE SET
                    {update_columns},
                    updated_at = now()
                RETURNING *
                """
            ).format(
                concept_table=sql.Identifier(concept_table),
                insert_columns=insert_columns,
                value_columns=value_columns,
                update_columns=update_columns,
            ),
            row_data,
        ).fetchone()
        conn.commit()
    return dict(row)


def delete_custom_record(*, terminology_key: str, code: str) -> bool:
    terminology_key = normalize_terminology_key(terminology_key)
    normalized_code = str(code).strip()
    if not normalized_code:
        raise ValueError("code is required")
    concept_id = get_terminology_definition(terminology_key).code_to_concept_id(
        normalized_code
    )
    concept_table = concept_table_name(terminology_key)
    with connect_db() as conn:
        _require_custom_terminology(conn, terminology_key)
        result = conn.execute(
            sql.SQL(
                """
                DELETE FROM {concept_table}
                WHERE concept_id = %s
                   OR payload->>'code' = %s
                """
            ).format(concept_table=sql.Identifier(concept_table)),
            (concept_id, normalized_code),
        )
        conn.commit()
    return bool(result.rowcount)


def get_concept(
    concept_id: int,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
) -> dict[str, Any] | None:
    terminology_key = normalize_terminology_key(terminology_key)
    with connect_db() as conn:
        init_schema(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        concept_table = concept_table_for(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        row = conn.execute(
            sql.SQL(
                """
            SELECT *
            FROM {concept_table}
            WHERE concept_id = %s
            """
            ).format(concept_table=sql.Identifier(concept_table)),
            (concept_id,),
        ).fetchone()
    return dict(row) if row else None


def get_concept_by_code(
    code: str,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
) -> dict[str, Any] | None:
    terminology_key = normalize_terminology_key(terminology_key)
    normalized_code = str(code).strip()
    normalized_code_without_dot = normalized_code.replace(".", "")
    with connect_db() as conn:
        init_schema(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        concept_table = concept_table_for(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        row = conn.execute(
            sql.SQL(
                """
            SELECT *
            FROM {concept_table}
            WHERE payload->>'code' = %s
               OR payload->>'loincNumber' = %s
               OR payload->>'rawCode' = %s
               OR payload->>'icdCode' = %s
               OR payload->>'blockId' = %s
               OR payload->>'externalId' = %s
               OR payload->>'rawCode' = %s
            LIMIT 1
            """
            ).format(concept_table=sql.Identifier(concept_table)),
            (
                normalized_code,
                normalized_code,
                normalized_code,
                normalized_code,
                normalized_code,
                normalized_code,
                normalized_code_without_dot,
            ),
        ).fetchone()
    return dict(row) if row else None


def expand_value_set_concepts(
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
    concept_ids: Sequence[int] | None = None,
    isa_concept_ids: Sequence[int] | None = None,
    semantic_tags: Sequence[str] | None = None,
    query: str | None = None,
    active_only: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    terminology_key = normalize_terminology_key(terminology_key)
    exact_ids = [int(value) for value in (concept_ids or [])]
    parent_ids = [int(value) for value in (isa_concept_ids or [])]
    tags = [str(value).strip() for value in (semantic_tags or []) if str(value).strip()]
    if not exact_ids and not parent_ids and not tags:
        return [], 0
    if limit < 1:
        raise ValueError("limit must be greater than 0")
    if offset < 0:
        raise ValueError("offset must be greater than or equal to 0")

    clauses: list[str] = []
    params: list[Any] = []
    if exact_ids:
        clauses.append("concept_id = ANY(%s::bigint[])")
        params.append(exact_ids)
    if parent_ids:
        clauses.append(
            "("
            "concept_id = ANY(%s::bigint[]) "
            "OR parent_ids && %s::bigint[] "
            "OR ancestor_ids && %s::bigint[]"
            ")"
        )
        params.extend([parent_ids, parent_ids, parent_ids])
    hierarchy_sql = " OR ".join(clauses) if clauses else "TRUE"
    active_sql = "AND active" if active_only else ""
    semantic_tags_sql = ""
    if tags:
        semantic_tags_sql = "AND semantic_tag = ANY(%s::text[])"
        params.append(tags)
    query_sql = ""
    order_sql = "preferred_term NULLS LAST, fsn NULLS LAST, concept_id"
    if query:
        like_query = f"%{query}%"
        prefix_query = f"{query}%"
        query_sql = """
          AND (
            payload->>'code' ILIKE %s
            OR payload->>'displayCode' ILIKE %s
            OR preferred_term ILIKE %s
            OR fsn ILIKE %s
            OR search_text ILIKE %s
          )
        """
        params.extend([like_query, like_query, like_query, like_query, like_query])
        order_sql = """
            CASE
                WHEN semantic_tag = 'disorder' AND lower(preferred_term) = lower(%s) THEN 0
                WHEN semantic_tag = 'disorder' AND preferred_term ILIKE %s THEN 1
                WHEN semantic_tag = 'disorder' AND preferred_term ILIKE %s THEN 2
                WHEN semantic_tag = 'disorder' AND EXISTS (
                    SELECT 1 FROM unnest(synonyms) synonym WHERE synonym ILIKE %s
                ) THEN 3
                WHEN semantic_tag = 'disorder' AND fsn ILIKE %s THEN 4
                WHEN lower(preferred_term) = lower(%s) THEN 5
                WHEN preferred_term ILIKE %s THEN 6
                WHEN EXISTS (
                    SELECT 1 FROM unnest(synonyms) synonym WHERE synonym ILIKE %s
                ) THEN 7
                WHEN lower(fsn) = lower(%s) THEN 8
                WHEN fsn ILIKE %s THEN 9
                WHEN lower(payload->>'displayCode') = lower(%s) THEN 10
                WHEN payload->>'displayCode' ILIKE %s THEN 11
                WHEN payload->>'code' ILIKE %s THEN 12
                WHEN search_text ILIKE %s THEN 13
                ELSE 14
            END,
            preferred_term NULLS LAST,
            fsn NULLS LAST,
            concept_id
        """
        params.extend(
            [
                query,
                prefix_query,
                like_query,
                like_query,
                prefix_query,
                query,
                prefix_query,
                like_query,
                query,
                prefix_query,
                query,
                prefix_query,
                like_query,
                like_query,
            ]
        )
    params.extend([limit, offset])

    sql_template = sql.SQL(
        f"""
        WITH matching AS MATERIALIZED (
            SELECT
                concept_id,
                payload->>'code' AS code,
                COALESCE(payload->>'displayCode', payload->>'code') AS display_code,
                active,
                preferred_term,
                fsn,
                semantic_tag,
                synonyms,
                text_definitions,
                descriptions,
                search_text,
                parent_ids,
                ancestor_ids,
                child_ids,
                payload
            FROM {{concept_table}}
            WHERE ({hierarchy_sql})
              {active_sql}
              {semantic_tags_sql}
              {query_sql}
        ),
        counted AS (
            SELECT COUNT(*) AS total FROM matching
        )
        SELECT matching.*, counted.total
        FROM matching, counted
        ORDER BY {order_sql}
        LIMIT %s OFFSET %s
        """
    )
    with connect_db() as conn:
        init_schema(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        concept_table = concept_table_for(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        sql_text = sql_template.format(concept_table=sql.Identifier(concept_table))
        rows = [dict(row) for row in conn.execute(sql_text, params).fetchall()]
    total = int(rows[0].pop("total")) if rows else 0
    for row in rows:
        row.pop("total", None)
    return rows, total


def _scope_clause(
    *,
    ancestor_concept_id: int | None,
    include_ancestor: bool,
    table_alias: str | None = None,
) -> tuple[str, list[Any]]:
    if ancestor_concept_id is None:
        return "", []
    concept_id_column = f"{table_alias}.concept_id" if table_alias else "concept_id"
    ancestor_ids_column = (
        f"{table_alias}.ancestor_ids" if table_alias else "ancestor_ids"
    )
    if include_ancestor:
        return (
            f"AND (%s = {concept_id_column} OR {ancestor_ids_column} @> ARRAY[%s]::bigint[])",
            [
                ancestor_concept_id,
                ancestor_concept_id,
            ],
        )
    return f"AND {ancestor_ids_column} @> ARRAY[%s]::bigint[]", [ancestor_concept_id]


def _semantic_tags_filter(
    semantic_tags: Sequence[str] | None,
    *,
    table_alias: str | None = "d",
) -> tuple[str, list[Any]]:
    if not semantic_tags:
        return "", []
    tags = [str(tag).strip() for tag in semantic_tags if str(tag).strip()]
    if not tags:
        return "", []
    column = f"{table_alias}.semantic_tag" if table_alias else "semantic_tag"
    return f"{column} = ANY(%s::text[])", [tags]


def search_concepts(
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
    query: str,
    limit: int = 25,
    ancestor_concept_id: int | None = None,
    include_ancestor: bool = True,
    active_only: bool = True,
    include_details: bool = False,
    include_query: bool = False,
    semantic_tags: Sequence[str] | None = None,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, Any]]:
    terminology_key = normalize_terminology_key(terminology_key)
    scope_sql, scope_params = _scope_clause(
        ancestor_concept_id=ancestor_concept_id,
        include_ancestor=include_ancestor,
    )
    active_sql = "AND active" if active_only else ""
    semantic_tags_clause, semantic_tags_params = _semantic_tags_filter(
        semantic_tags,
        table_alias=None,
    )
    semantic_tags_sql = f"AND {semantic_tags_clause}" if semantic_tags_clause else ""
    like_query = f"%{query}%"
    params: list[Any] = [
        query,
        query,
        like_query,
        like_query,
        like_query,
        *semantic_tags_params,
        *scope_params,
        limit,
    ]
    columns = concept_columns(include_details=include_details)
    query_template = sql.SQL(
        f"""
        WITH q AS (
            SELECT websearch_to_tsquery('english', %s) AS tsq, %s::text AS raw_query
        )
        SELECT
            {columns},
            ts_rank_cd(search_vector, q.tsq) AS score
        FROM {{concept_table}}, q
        WHERE (
            search_vector @@ q.tsq
            OR preferred_term ILIKE %s
            OR fsn ILIKE %s
            OR search_text ILIKE %s
        )
        {active_sql}
        {semantic_tags_sql}
        {scope_sql}
        ORDER BY
            ts_rank_cd(search_vector, q.tsq) DESC,
            CASE WHEN lower(preferred_term) = lower(q.raw_query) THEN 0 ELSE 1 END,
            preferred_term NULLS LAST,
            concept_id
        LIMIT %s
    """
    )
    with connect_db() as conn:
        init_schema(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        concept_table = concept_table_for(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        query_sql = query_template.format(concept_table=sql.Identifier(concept_table))
        rows = conn.execute(query_sql, params).fetchall()
        query_info = (
            _query_payload(query_sql.as_string(conn), params) if include_query else None
        )
    results = [dict(row) for row in rows]
    if include_query:
        return results, query_info or {}
    return results


def list_children(
    concept_id: int,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
    limit: int = 100,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    terminology_key = normalize_terminology_key(terminology_key)
    active_sql = "AND active" if active_only else ""
    with connect_db() as conn:
        init_schema(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        concept_table = concept_table_for(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        rows = conn.execute(
            sql.SQL(
                f"""
                SELECT
                    concept_id,
                    payload->>'code' AS code,
                    COALESCE(payload->>'displayCode', payload->>'code') AS display_code,
                    active,
                    fsn,
                    preferred_term,
                    semantic_tag,
                    parent_ids,
                    child_ids
            FROM {{concept_table}}
            WHERE parent_ids @> ARRAY[%s]::bigint[]
            {active_sql}
            ORDER BY preferred_term NULLS LAST, fsn NULLS LAST, concept_id
            LIMIT %s
            """
            ).format(concept_table=sql.Identifier(concept_table)),
            (concept_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def list_descendants(
    concept_id: int,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
    limit: int = 100,
    include_self: bool = False,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    terminology_key = normalize_terminology_key(terminology_key)
    active_sql = "AND active" if active_only else ""
    self_sql = "OR concept_id = %s" if include_self else ""
    params: list[Any] = [concept_id]
    if include_self:
        params.append(concept_id)
    params.append(limit)
    with connect_db() as conn:
        init_schema(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        concept_table = concept_table_for(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        rows = conn.execute(
            sql.SQL(
                f"""
            SELECT
                concept_id,
                payload->>'code' AS code,
                COALESCE(payload->>'displayCode', payload->>'code') AS display_code,
                active,
                fsn,
                preferred_term,
                semantic_tag,
                parent_ids,
                ancestor_ids,
                child_ids
            FROM {{concept_table}}
            WHERE (ancestor_ids @> ARRAY[%s]::bigint[] {self_sql})
            {active_sql}
            ORDER BY preferred_term NULLS LAST, fsn NULLS LAST, concept_id
            LIMIT %s
            """
            ).format(concept_table=sql.Identifier(concept_table)),
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def embedding_status(
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
) -> dict[str, Any]:
    terminology_key = normalize_terminology_key(terminology_key)
    with connect_db() as conn:
        init_schema(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        version = resolve_terminology_version(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        version_key = str(version["version_key"])
        concept_table = str(version["concept_table"])
        summary = conn.execute(
            sql.SQL(
                """
            SELECT
                COUNT(*) AS concept_count
            FROM {concept_table}
            """
            ).format(concept_table=sql.Identifier(concept_table))
        ).fetchone()
        models = []
        for model in _embedding_models(
            conn,
            terminology_key=terminology_key,
            terminology_version=version_key,
        ):
            model_payload = dict(model)
            table_name = model_payload.get("embedding_table") or embedding_table_name(
                model_key=str(model_payload["model_key"]),
                dimensions=int(model_payload["dimensions"]),
                terminology_key=terminology_key,
                terminology_version=version_key,
            )
            model_payload["embedding_table"] = table_name
            if _table_exists(conn, table_name):
                row = conn.execute(
                    sql.SQL(
                        """
                        SELECT
                            COUNT(*) AS embedded_concept_count,
                            MAX(embedded_at) AS last_embedding_updated_at
                        FROM {table_name}
                        """
                    ).format(table_name=sql.Identifier(table_name))
                ).fetchone()
                model_payload["embedded_concept_count"] = int(
                    row["embedded_concept_count"]
                )
                model_payload["last_embedding_updated_at"] = row[
                    "last_embedding_updated_at"
                ]
            else:
                model_payload["embedded_concept_count"] = 0
                model_payload["last_embedding_updated_at"] = None
            models.append(model_payload)
    return {
        "terminology": terminology_key,
        "version": version_key,
        "is_default_version": bool(version["is_default"]),
        "concept_table": concept_table,
        "concept_count": int(summary["concept_count"]),
        "models": models,
        "defaultEmbeddingDimensions": DEFAULT_EMBEDDING_DIMENSIONS,
    }


def _embedding_models(
    conn,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
) -> list[dict[str, Any]]:
    terminology_key = normalize_terminology_key(terminology_key)
    version_key = normalize_version_key(terminology_version)
    rows = conn.execute(
        """
        SELECT
            terminology_key,
            terminology_version,
            model_key,
            provider,
            provider_model,
            dimensions,
            embedding_table,
            storage_type,
            distance,
            text_source
        FROM embedding_model
        WHERE terminology_key = %s
          AND terminology_version = %s
        ORDER BY model_key
        """,
        (terminology_key, version_key),
    ).fetchall()
    return [dict(row) for row in rows]


def default_model_key(provider: str, provider_model: str) -> str:
    return f"{provider}:{provider_model}"


def get_embedding_model(
    model_key: str,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
) -> dict[str, Any] | None:
    terminology_key = normalize_terminology_key(terminology_key)
    with connect_db() as conn:
        init_schema(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        version_key = str(
            resolve_terminology_version(
                conn,
                terminology_key=terminology_key,
                terminology_version=terminology_version,
            )["version_key"]
        )
        row = conn.execute(
            """
            SELECT *
            FROM embedding_model
            WHERE terminology_key = %s
              AND terminology_version = %s
              AND model_key = %s
            """,
            (terminology_key, version_key, model_key),
        ).fetchone()
    return dict(row) if row else None


def register_embedding_model(
    conn,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
    model_key: str,
    provider: str,
    provider_model: str,
    dimensions: int,
    storage_type: str | None = None,
    distance: str = "cosine",
    text_source: str = "search_text",
) -> None:
    terminology_key = normalize_terminology_key(terminology_key)
    version = resolve_terminology_version(
        conn,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
    )
    version_key = str(version["version_key"])
    storage_type = resolve_embedding_storage_type(
        dimensions=dimensions,
        requested_storage_type=storage_type,
    )
    table_name = _resolve_embedding_table_name(
        conn,
        terminology_key=terminology_key,
        terminology_version=version_key,
        model_key=model_key,
        dimensions=dimensions,
        storage_type=storage_type,
    )
    conn.execute(
        """
        INSERT INTO embedding_model (
            terminology_key,
            terminology_version,
            model_key,
            provider,
            provider_model,
            dimensions,
            embedding_table,
            storage_type,
            distance,
            text_source,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (terminology_key, terminology_version, model_key) DO UPDATE SET
            provider = excluded.provider,
            provider_model = excluded.provider_model,
            dimensions = excluded.dimensions,
            embedding_table = excluded.embedding_table,
            storage_type = excluded.storage_type,
            distance = excluded.distance,
            text_source = excluded.text_source,
            updated_at = now()
        """,
        (
            terminology_key,
            version_key,
            model_key,
            provider,
            provider_model,
            dimensions,
            table_name,
            storage_type,
            distance,
            text_source,
        ),
    )
    ensure_model_embedding_table(
        conn,
        terminology_key=terminology_key,
        terminology_version=version_key,
        model_key=model_key,
        dimensions=dimensions,
        storage_type=storage_type,
    )
    if (
        terminology_key == DEFAULT_TERMINOLOGY_KEY
        and version_key == DEFAULT_TERMINOLOGY_VERSION_KEY
    ):
        migrate_legacy_embeddings(conn, model_key=model_key, dimensions=dimensions)


def _safe_index_suffix(value: str) -> str:
    return db_schema.safe_index_suffix(value)


def embedding_table_name(
    *,
    model_key: str,
    dimensions: int,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
) -> str:
    terminology_key = normalize_terminology_key(terminology_key)
    version_key = normalize_version_key(terminology_version)
    digest = hashlib.sha1(
        f"{terminology_key}:{version_key}:{model_key}:{dimensions}".encode()
    ).hexdigest()[:8]
    return (
        f"concept_embedding_{_safe_index_suffix(f'{terminology_key}_{version_key}')}_"
        f"{_safe_index_suffix(model_key)}_{digest}_{dimensions}"
    )


def _table_exists(conn, table_name: str) -> bool:
    return db_schema.table_exists(conn, table_name)


def _resolve_embedding_table_name(
    conn,
    *,
    terminology_key: str,
    terminology_version: str,
    model_key: str,
    dimensions: int,
    storage_type: str,
) -> str:
    row = conn.execute(
        """
        SELECT embedding_table, dimensions, storage_type
        FROM embedding_model
        WHERE terminology_key = %s
          AND terminology_version = %s
          AND model_key = %s
        """,
        (terminology_key, terminology_version, model_key),
    ).fetchone()
    if row and row.get("embedding_table"):
        existing_dimensions = int(row["dimensions"])
        existing_storage_type = str(row.get("storage_type") or VECTOR_STORAGE)
        if existing_dimensions == dimensions and existing_storage_type == storage_type:
            return str(row["embedding_table"])
    return embedding_table_name(
        terminology_key=terminology_key,
        terminology_version=terminology_version,
        model_key=model_key,
        dimensions=dimensions,
    )


def ensure_model_embedding_table(
    conn,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
    model_key: str,
    dimensions: int,
    storage_type: str | None = None,
) -> str:
    terminology_key = normalize_terminology_key(terminology_key)
    version = resolve_terminology_version(
        conn,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
    )
    version_key = str(version["version_key"])
    storage_type = resolve_embedding_storage_type(
        dimensions=dimensions,
        requested_storage_type=storage_type,
    )
    table_name = _resolve_embedding_table_name(
        conn,
        terminology_key=terminology_key,
        terminology_version=version_key,
        model_key=model_key,
        dimensions=dimensions,
        storage_type=storage_type,
    )
    concept_table = str(version["concept_table"])
    return db_schema.create_embedding_table(
        conn,
        table_name=table_name,
        concept_table=concept_table,
        dimensions=dimensions,
        storage_type=storage_type,
        halfvec_storage=HALFVEC_STORAGE,
    )


def migrate_legacy_embeddings(conn, *, model_key: str, dimensions: int) -> None:
    if not _table_exists(conn, "concept_embedding"):
        return
    table_name = ensure_model_embedding_table(
        conn,
        terminology_key=DEFAULT_TERMINOLOGY_KEY,
        terminology_version=DEFAULT_TERMINOLOGY_VERSION_KEY,
        model_key=model_key,
        dimensions=dimensions,
    )
    conn.execute(
        sql.SQL(
            """
            INSERT INTO {table_name} (
                concept_id,
                dimensions,
                embedding,
                source_hash,
                embedded_at
            )
            SELECT
                concept_id,
                dimensions,
                embedding::vector({dimensions}),
                source_hash,
                embedded_at
            FROM concept_embedding
            WHERE model_key = {model_key}
              AND dimensions = {dimensions}
            ON CONFLICT (concept_id) DO NOTHING
            """
        ).format(
            table_name=sql.Identifier(table_name),
            dimensions=sql.Literal(dimensions),
            model_key=sql.Literal(model_key),
        )
    )


def _embedding_index_name(
    *,
    model_key: str,
    dimensions: int,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
) -> str:
    terminology_key = normalize_terminology_key(terminology_key)
    version_key = normalize_version_key(terminology_version)
    digest = hashlib.sha1(
        f"{terminology_key}:{version_key}:{model_key}:{dimensions}".encode()
    ).hexdigest()[:8]
    return (
        f"idx_embedding_{_safe_index_suffix(f'{terminology_key}_{version_key}')}_"
        f"{_safe_index_suffix(model_key)}_{digest}_hnsw"
    )


def drop_embedding_index(
    conn,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
    model_key: str,
    dimensions: int,
) -> None:
    db_schema.drop_index(
        conn,
        _embedding_index_name(
            terminology_key=terminology_key,
            terminology_version=terminology_version,
            model_key=model_key,
            dimensions=dimensions,
        ),
    )


def ensure_embedding_index(
    conn,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
    model_key: str,
    dimensions: int,
    storage_type: str | None = None,
    distance: str = "cosine",
) -> None:
    terminology_key = normalize_terminology_key(terminology_key)
    if distance != "cosine":
        raise ValueError("Only cosine distance is currently indexed")
    storage_type = resolve_embedding_storage_type(
        dimensions=dimensions,
        requested_storage_type=storage_type,
    )
    table_name = ensure_model_embedding_table(
        conn,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
        model_key=model_key,
        dimensions=dimensions,
        storage_type=storage_type,
    )
    index_name = _embedding_index_name(
        terminology_key=terminology_key,
        terminology_version=terminology_version,
        model_key=model_key,
        dimensions=dimensions,
    )
    if storage_type == VECTOR_STORAGE and dimensions > MAX_VECTOR_INDEX_DIMENSIONS:
        raise ValueError(
            f"vector HNSW indexing supports up to {MAX_VECTOR_INDEX_DIMENSIONS} dimensions; "
            f"use {HALFVEC_STORAGE} storage for {dimensions} dimensions"
        )
    db_schema.create_embedding_hnsw_index(
        conn,
        table_name=table_name,
        index_name=index_name,
        storage_type=storage_type,
        halfvec_storage=HALFVEC_STORAGE,
    )


def iter_embedding_inputs(
    conn,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
    model_key: str,
    limit: int | None = None,
    refresh: bool = False,
    active_only: bool = True,
    after_concept_id: int | None = None,
    semantic_tags: Sequence[str] | None = None,
    fetch_size: int = 1_000,
) -> Iterable[dict[str, Any]]:
    terminology_key = normalize_terminology_key(terminology_key)
    version = resolve_terminology_version(
        conn,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
    )
    version_key = str(version["version_key"])
    model = get_embedding_model(
        model_key,
        terminology_key=terminology_key,
        terminology_version=version_key,
    )
    dimensions = int(model["dimensions"]) if model else DEFAULT_EMBEDDING_DIMENSIONS
    storage_type = str(model.get("storage_type") or VECTOR_STORAGE) if model else None
    table_name = ensure_model_embedding_table(
        conn,
        terminology_key=terminology_key,
        terminology_version=version_key,
        model_key=model_key,
        dimensions=dimensions,
        storage_type=storage_type,
    )
    concept_table = str(version["concept_table"])
    base_filters = []
    base_params: list[Any] = []
    base_filters.append("NULLIF(trim(d.search_text), '') IS NOT NULL")
    if active_only:
        base_filters.append("d.active")
    semantic_tags_sql, semantic_tags_params = _semantic_tags_filter(semantic_tags)
    if semantic_tags_sql:
        base_filters.append(semantic_tags_sql)
        base_params.extend(semantic_tags_params)
    if not refresh:
        base_filters.append(
            sql.SQL(
                """
            NOT EXISTS (
                SELECT 1
                FROM {table_name} e
                WHERE e.concept_id = d.concept_id
            )
            """
            )
            .format(table_name=sql.Identifier(table_name))
            .as_string(conn)
        )
    page_size = max(fetch_size, 1)
    remaining = limit
    last_concept_id = after_concept_id
    while remaining is None or remaining > 0:
        filters = list(base_filters)
        params = list(base_params)
        if last_concept_id is not None:
            filters.append("d.concept_id > %s")
            params.append(last_concept_id)
        current_page_size = (
            page_size if remaining is None else min(page_size, remaining)
        )
        params.append(current_page_size)
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        rows = conn.execute(
            sql.SQL(
                f"""
            SELECT concept_id, search_text
            FROM {{concept_table}} d
            {where_sql}
            ORDER BY concept_id
            LIMIT %s
            """
            ).format(concept_table=sql.Identifier(concept_table)),
            params,
        ).fetchall()
        if not rows:
            break
        for row in rows:
            item = dict(row)
            last_concept_id = int(item["concept_id"])
            if remaining is not None:
                remaining -= 1
            yield item


def count_embedding_inputs(
    conn,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
    model_key: str,
    limit: int | None = None,
    refresh: bool = False,
    active_only: bool = True,
    after_concept_id: int | None = None,
    semantic_tags: Sequence[str] | None = None,
) -> int:
    terminology_key = normalize_terminology_key(terminology_key)
    version = resolve_terminology_version(
        conn,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
    )
    version_key = str(version["version_key"])
    model = get_embedding_model(
        model_key,
        terminology_key=terminology_key,
        terminology_version=version_key,
    )
    dimensions = int(model["dimensions"]) if model else DEFAULT_EMBEDDING_DIMENSIONS
    storage_type = str(model.get("storage_type") or VECTOR_STORAGE) if model else None
    table_name = ensure_model_embedding_table(
        conn,
        terminology_key=terminology_key,
        terminology_version=version_key,
        model_key=model_key,
        dimensions=dimensions,
        storage_type=storage_type,
    )
    concept_table = str(version["concept_table"])
    filters = []
    params: list[Any] = []
    filters.append("NULLIF(trim(d.search_text), '') IS NOT NULL")
    if active_only:
        filters.append("d.active")
    if after_concept_id is not None:
        filters.append("d.concept_id > %s")
        params.append(after_concept_id)
    semantic_tags_sql, semantic_tags_params = _semantic_tags_filter(semantic_tags)
    if semantic_tags_sql:
        filters.append(semantic_tags_sql)
        params.extend(semantic_tags_params)
    if not refresh:
        filters.append(
            sql.SQL(
                """
            NOT EXISTS (
                SELECT 1
                FROM {table_name} e
                WHERE e.concept_id = d.concept_id
            )
            """
            )
            .format(table_name=sql.Identifier(table_name))
            .as_string(conn)
        )
    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
    row = conn.execute(
        sql.SQL(
            f"""
        SELECT COUNT(*) AS count
        FROM {{concept_table}} d
        {where_sql}
        """
        ).format(concept_table=sql.Identifier(concept_table)),
        params,
    ).fetchone()
    count = int(row["count"])
    return min(count, limit) if limit is not None else count


def delete_model_embeddings_outside_filter(
    conn,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
    model_key: str,
    active_only: bool = True,
    semantic_tags: Sequence[str] | None = None,
) -> int:
    terminology_key = normalize_terminology_key(terminology_key)
    version = resolve_terminology_version(
        conn,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
    )
    version_key = str(version["version_key"])
    model = get_embedding_model(
        model_key,
        terminology_key=terminology_key,
        terminology_version=version_key,
    )
    if model is None:
        return 0
    dimensions = int(model["dimensions"])
    table_name = ensure_model_embedding_table(
        conn,
        terminology_key=terminology_key,
        terminology_version=version_key,
        model_key=model_key,
        dimensions=dimensions,
        storage_type=str(model.get("storage_type") or VECTOR_STORAGE),
    )
    concept_table = str(version["concept_table"])
    remove_clauses: list[str] = []
    params: list[Any] = []
    if active_only:
        remove_clauses.append("NOT d.active")
    if semantic_tags:
        tags = [str(tag).strip() for tag in semantic_tags if str(tag).strip()]
        if tags:
            remove_clauses.append("NOT (d.semantic_tag = ANY(%s::text[]))")
            params.append(tags)
    if not remove_clauses:
        return 0
    cursor = conn.execute(
        sql.SQL(
            """
            DELETE FROM {table_name} e
            USING {concept_table} d
            WHERE e.concept_id = d.concept_id
              AND ({remove_sql})
            """
        ).format(
            table_name=sql.Identifier(table_name),
            concept_table=sql.Identifier(concept_table),
            remove_sql=sql.SQL(" OR ").join(
                sql.SQL(clause) for clause in remove_clauses
            ),
        ),
        params,
    )
    return int(cursor.rowcount or 0)


def upsert_concept_embeddings(
    conn,
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
    model_key: str,
    dimensions: int,
    storage_type: str | None = None,
    rows: Sequence[dict[str, Any]],
    vectors: Sequence[Sequence[float]],
    source_hashes: Sequence[str],
) -> None:
    terminology_key = normalize_terminology_key(terminology_key)
    if len(rows) != len(vectors) or len(rows) != len(source_hashes):
        raise ValueError("rows, vectors, and source_hashes must have the same length")
    storage_type = resolve_embedding_storage_type(
        dimensions=dimensions,
        requested_storage_type=storage_type,
    )
    table_name = ensure_model_embedding_table(
        conn,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
        model_key=model_key,
        dimensions=dimensions,
        storage_type=storage_type,
    )
    payload = [
        (
            int(row["concept_id"]),
            dimensions,
            _format_vector(vector),
            source_hash,
        )
        for row, vector, source_hash in zip(rows, vectors, source_hashes, strict=True)
    ]
    with conn.cursor() as cur:
        if storage_type == HALFVEC_STORAGE:
            cur.executemany(
                sql.SQL(
                    """
                    INSERT INTO {table_name} (
                        concept_id,
                        dimensions,
                        embedding,
                        embedding_half,
                        source_hash,
                        embedded_at
                    )
                    VALUES (%s, %s, %s::vector, %s::halfvec, %s, now())
                    ON CONFLICT (concept_id) DO UPDATE SET
                        dimensions = excluded.dimensions,
                        embedding = excluded.embedding,
                        embedding_half = excluded.embedding_half,
                        source_hash = excluded.source_hash,
                        embedded_at = now()
                    """
                ).format(table_name=sql.Identifier(table_name)),
                [
                    (concept_id, row_dimensions, vector, vector, source_hash)
                    for concept_id, row_dimensions, vector, source_hash in payload
                ],
            )
            return
        cur.executemany(
            sql.SQL(
                """
                INSERT INTO {table_name} (
                    concept_id,
                    dimensions,
                    embedding,
                    source_hash,
                    embedded_at
                )
                VALUES (%s, %s, %s::vector, %s, now())
                ON CONFLICT (concept_id) DO UPDATE SET
                    dimensions = excluded.dimensions,
                    embedding = excluded.embedding,
                    source_hash = excluded.source_hash,
                    embedded_at = now()
                """
            ).format(table_name=sql.Identifier(table_name)),
            payload,
        )


def _format_vector(values: Sequence[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def _rerank_candidate_limit(limit: int) -> int:
    return max(limit * 20, 100)


def resolve_vector_search_strategy(
    *,
    storage_type: str,
    requested_strategy: str | None = None,
) -> str:
    strategy = (requested_strategy or HALFVEC_RERANK_STRATEGY).strip().lower()
    valid = {HALFVEC_RERANK_STRATEGY, FULL_EXACT_STRATEGY, HALFVEC_ONLY_STRATEGY}
    if strategy not in valid:
        raise ValueError(
            "vectorSearchStrategy must be one of: halfvec_rerank, full_exact, halfvec_only"
        )
    if storage_type != HALFVEC_STORAGE and requested_strategy is not None:
        raise ValueError(
            "vectorSearchStrategy is only supported for halfvec-backed models"
        )
    if storage_type != HALFVEC_STORAGE:
        return HALFVEC_RERANK_STRATEGY
    return strategy


def vector_search_concepts(
    *,
    terminology_key: str | None = DEFAULT_TERMINOLOGY_KEY,
    terminology_version: str | None = None,
    model_key: str,
    embedding: Sequence[float],
    limit: int = 25,
    ancestor_concept_id: int | None = None,
    include_ancestor: bool = True,
    active_only: bool = True,
    include_details: bool = False,
    include_query: bool = False,
    semantic_tags: Sequence[str] | None = None,
    vector_search_strategy: str | None = None,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, Any]]:
    terminology_key = normalize_terminology_key(terminology_key)
    model = get_embedding_model(
        model_key,
        terminology_key=terminology_key,
        terminology_version=terminology_version,
    )
    if model is None:
        raise ValueError(f"Embedding model {model_key!r} has not been populated")
    dimensions = int(model["dimensions"])
    storage_type = resolve_embedding_storage_type(
        dimensions=dimensions,
        requested_storage_type=str(model.get("storage_type") or VECTOR_STORAGE),
    )
    vector_search_strategy = resolve_vector_search_strategy(
        storage_type=storage_type,
        requested_strategy=vector_search_strategy,
    )
    table_name = str(
        model.get("embedding_table")
        or embedding_table_name(
            terminology_key=terminology_key,
            terminology_version=str(
                model.get("terminology_version") or DEFAULT_TERMINOLOGY_VERSION_KEY
            ),
            model_key=model_key,
            dimensions=dimensions,
        )
    )
    if len(embedding) != dimensions:
        raise ValueError(
            f"Expected {dimensions} embedding dimensions for {model_key!r}, got {len(embedding)}"
        )
    scope_sql, scope_params = _scope_clause(
        ancestor_concept_id=ancestor_concept_id,
        include_ancestor=include_ancestor,
    )
    active_sql = "AND active" if active_only else ""
    joined_active_sql = "AND d.active" if active_only else ""
    semantic_sql, semantic_params = _semantic_tags_filter(
        semantic_tags, table_alias=None
    )
    semantic_filter_sql = f"AND {semantic_sql}" if semantic_sql else ""
    semantic_sql_d, semantic_params_d = _semantic_tags_filter(
        semantic_tags, table_alias="d"
    )
    semantic_filter_sql_d = f"AND {semantic_sql_d}" if semantic_sql_d else ""
    semantic_sql_c, semantic_params_c = _semantic_tags_filter(
        semantic_tags, table_alias="c"
    )
    semantic_filter_sql_c = f"AND {semantic_sql_c}" if semantic_sql_c else ""
    candidate_columns = concept_columns(
        table_alias="c", include_details=include_details
    )
    document_columns = concept_columns(table_alias="d", include_details=include_details)
    vector = _format_vector(embedding)
    query_info: dict[str, Any] | None = None
    with connect_db() as conn:
        init_schema(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        version = resolve_terminology_version(
            conn,
            terminology_key=terminology_key,
            terminology_version=terminology_version,
        )
        concept_table = str(version["concept_table"])
        ensure_model_embedding_table(
            conn,
            terminology_key=terminology_key,
            terminology_version=str(version["version_key"]),
            model_key=model_key,
            dimensions=dimensions,
            storage_type=storage_type,
        )
        conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        conn.execute("SET LOCAL hnsw.ef_search = 100")
        if (
            storage_type == HALFVEC_STORAGE
            and vector_search_strategy == FULL_EXACT_STRATEGY
        ):
            if ancestor_concept_id is not None:
                half_scope_sql, half_scope_params = _scope_clause(
                    ancestor_concept_id=ancestor_concept_id,
                    include_ancestor=include_ancestor,
                    table_alias="d",
                )
                params = [
                    vector,
                    model_key,
                    *semantic_params_d,
                    *half_scope_params,
                    limit,
                ]
                query_sql = sql.SQL(
                    f"""
                    WITH q AS (SELECT %s::vector({dimensions}) AS embedding)
                    SELECT
                        {document_columns},
                        %s AS model_key,
                        1 - (e.embedding::vector({dimensions}) <=> q.embedding) AS score
                    FROM {{table_name}} e
                    JOIN {{concept_table}} d ON d.concept_id = e.concept_id
                    CROSS JOIN q
                    WHERE TRUE
                      {joined_active_sql}
                      {semantic_filter_sql_d}
                      {half_scope_sql}
                    ORDER BY e.embedding::vector({dimensions}) <=> q.embedding
                    LIMIT %s
                    """
                ).format(
                    table_name=sql.Identifier(table_name),
                    concept_table=sql.Identifier(concept_table),
                )
            else:
                params = (vector, model_key, *semantic_params_d, limit)
                query_sql = sql.SQL(
                    f"""
                    WITH q AS (SELECT %s::vector({dimensions}) AS embedding)
                    SELECT
                        {document_columns},
                        %s AS model_key,
                        1 - (e.embedding::vector({dimensions}) <=> q.embedding) AS score
                    FROM {{table_name}} e
                    JOIN {{concept_table}} d ON d.concept_id = e.concept_id
                    CROSS JOIN q
                    WHERE TRUE
                      {joined_active_sql}
                      {semantic_filter_sql_d}
                    ORDER BY e.embedding::vector({dimensions}) <=> q.embedding
                    LIMIT %s
                    """
                ).format(
                    table_name=sql.Identifier(table_name),
                    concept_table=sql.Identifier(concept_table),
                )
            rows = conn.execute(query_sql, params).fetchall()
            if include_query:
                query_info = _vector_query_payload(
                    sql_text=query_sql.as_string(conn),
                    params=params,
                    vector=vector,
                    dimensions=dimensions,
                )
                query_info["storageType"] = storage_type
                query_info["vectorSearchStrategy"] = vector_search_strategy
        elif storage_type == HALFVEC_STORAGE:
            candidate_limit = _rerank_candidate_limit(limit)
            nearest_columns = concept_columns(
                table_alias="n",
                include_details=include_details,
                projected=True,
            )
            if ancestor_concept_id is not None:
                half_scope_sql, half_scope_params = _scope_clause(
                    ancestor_concept_id=ancestor_concept_id,
                    include_ancestor=include_ancestor,
                    table_alias="c",
                )
                params = [
                    vector,
                    vector,
                    *semantic_params_c,
                    *half_scope_params,
                    model_key,
                    candidate_limit,
                    limit,
                ]
                query_sql = sql.SQL(
                    f"""
                    WITH q AS (
                        SELECT
                            %s::vector({dimensions}) AS embedding,
                            %s::halfvec({dimensions}) AS embedding_half
                    ),
                    filtered AS MATERIALIZED (
                        SELECT
                            {candidate_columns},
                            e.embedding,
                            e.embedding_half
                        FROM {{concept_table}} c
                        JOIN {{table_name}} e ON e.concept_id = c.concept_id
                        WHERE TRUE
                        {"AND c.active" if active_only else ""}
                        {semantic_filter_sql_c}
                        {half_scope_sql}
                    ),
                    nearest AS MATERIALIZED (
                        SELECT
                            f.*,
                            %s AS model_key,
                            1 - (f.embedding_half <=> q.embedding_half) AS half_score
                        FROM filtered f
                        CROSS JOIN q
                        ORDER BY f.embedding_half <=> q.embedding_half
                        LIMIT %s
                    )
                    SELECT
                        {nearest_columns},
                        n.model_key,
                        {"n.half_score AS score" if vector_search_strategy == HALFVEC_ONLY_STRATEGY else f"1 - (n.embedding::vector({dimensions}) <=> q.embedding) AS score"},
                        n.half_score
                    FROM nearest n
                    CROSS JOIN q
                    ORDER BY {"n.embedding_half <=> q.embedding_half" if vector_search_strategy == HALFVEC_ONLY_STRATEGY else f"n.embedding::vector({dimensions}) <=> q.embedding"}
                    LIMIT %s
                    """
                ).format(
                    table_name=sql.Identifier(table_name),
                    concept_table=sql.Identifier(concept_table),
                )
            else:
                params = [
                    vector,
                    vector,
                    *semantic_params_d,
                    model_key,
                    candidate_limit,
                    limit,
                ]
                query_sql = sql.SQL(
                    f"""
                    WITH q AS (
                        SELECT
                            %s::vector({dimensions}) AS embedding,
                            %s::halfvec({dimensions}) AS embedding_half
                    ),
                    nearest AS MATERIALIZED (
                        SELECT
                            {document_columns},
                            e.embedding,
                            e.embedding_half,
                            %s AS model_key,
                            1 - (e.embedding_half <=> q.embedding_half) AS half_score
                        FROM {{table_name}} e
                        JOIN {{concept_table}} d ON d.concept_id = e.concept_id
                        CROSS JOIN q
                        WHERE TRUE
                          {joined_active_sql}
                          {semantic_filter_sql_d}
                        ORDER BY e.embedding_half <=> q.embedding_half
                        LIMIT %s
                    )
                    SELECT
                        {nearest_columns},
                        n.model_key,
                        {"n.half_score AS score" if vector_search_strategy == HALFVEC_ONLY_STRATEGY else f"1 - (n.embedding::vector({dimensions}) <=> q.embedding) AS score"},
                        n.half_score
                    FROM nearest n
                    CROSS JOIN q
                    ORDER BY {"n.embedding_half <=> q.embedding_half" if vector_search_strategy == HALFVEC_ONLY_STRATEGY else f"n.embedding::vector({dimensions}) <=> q.embedding"}
                    LIMIT %s
                    """
                ).format(
                    table_name=sql.Identifier(table_name),
                    concept_table=sql.Identifier(concept_table),
                )
            rows = conn.execute(query_sql, params).fetchall()
            if include_query:
                query_info = _vector_query_payload(
                    sql_text=query_sql.as_string(conn),
                    params=params,
                    vector=vector,
                    dimensions=dimensions,
                )
                query_info["storageType"] = storage_type
                query_info["candidateLimit"] = candidate_limit
                query_info["vectorSearchStrategy"] = vector_search_strategy
                query_info["rerank"] = (
                    "none"
                    if vector_search_strategy == HALFVEC_ONLY_STRATEGY
                    else "full_vector_cosine"
                )
        elif ancestor_concept_id is not None:
            params: list[Any] = [
                *semantic_params,
                *scope_params,
                vector,
                model_key,
                limit,
            ]
            query_sql = sql.SQL(
                f"""
                WITH candidates AS MATERIALIZED (
                    SELECT *
                    FROM {{concept_table}}
                    WHERE TRUE
                    {active_sql}
                    {semantic_filter_sql}
                    {scope_sql}
                ),
                q AS (SELECT %s::vector({dimensions}) AS embedding)
                SELECT
                    {candidate_columns},
                    %s AS model_key,
                    1 - (e.embedding::vector({dimensions}) <=> q.embedding) AS score
                FROM candidates c
                JOIN {{table_name}} e ON e.concept_id = c.concept_id
                CROSS JOIN q
                ORDER BY e.embedding::vector({dimensions}) <=> q.embedding
                LIMIT %s
                """
            ).format(
                table_name=sql.Identifier(table_name),
                concept_table=sql.Identifier(concept_table),
            )
            rows = conn.execute(
                query_sql,
                params,
            ).fetchall()
            if include_query:
                query_info = _vector_query_payload(
                    sql_text=query_sql.as_string(conn),
                    params=params,
                    vector=vector,
                    dimensions=dimensions,
                )
                query_info["storageType"] = storage_type
        else:
            params = (vector, model_key, *semantic_params_d, limit)
            query_sql = sql.SQL(
                f"""
                WITH q AS (SELECT %s::vector({dimensions}) AS embedding)
                SELECT
                    {document_columns},
                    %s AS model_key,
                    1 - (e.embedding::vector({dimensions}) <=> q.embedding) AS score
                FROM {{table_name}} e
                JOIN {{concept_table}} d ON d.concept_id = e.concept_id
                CROSS JOIN q
                WHERE TRUE
                  {joined_active_sql}
                  {semantic_filter_sql_d}
                ORDER BY e.embedding::vector({dimensions}) <=> q.embedding
                LIMIT %s
                """
            ).format(
                table_name=sql.Identifier(table_name),
                concept_table=sql.Identifier(concept_table),
            )
            rows = conn.execute(
                query_sql,
                params,
            ).fetchall()
            if include_query:
                query_info = _vector_query_payload(
                    sql_text=query_sql.as_string(conn),
                    params=params,
                    vector=vector,
                    dimensions=dimensions,
                )
                query_info["storageType"] = storage_type
    results = [dict(row) for row in rows]
    if include_query:
        return results, query_info or {}
    return results
