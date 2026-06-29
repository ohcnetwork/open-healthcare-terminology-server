from __future__ import annotations

import re

from psycopg import sql

from ots.terminology import (
    concept_table_name,
    normalize_terminology_key,
    normalize_version_key,
    versioned_concept_table_name,
)


def safe_index_suffix(value: str) -> str:
    suffix = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
    return suffix[:24] or "model"


def table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_name = %s
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def create_index_if_missing(cur, index_name: str, statement) -> None:
    row = cur.execute("SELECT to_regclass(%s) AS index_name", (index_name,)).fetchone()
    if row is not None and row["index_name"] is not None:
        return
    cur.execute(statement)


def table_columns(conn, table_name: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = %s
        """,
        (table_name,),
    ).fetchall()
    return {str(row["column_name"]) for row in rows}


def ensure_column(conn, table_name: str, column_name: str, ddl_fragment: str) -> None:
    if column_name in table_columns(conn, table_name):
        return
    conn.execute(
        sql.SQL("ALTER TABLE {table_name} ADD COLUMN {ddl_fragment}").format(
            table_name=sql.Identifier(table_name),
            ddl_fragment=sql.SQL(ddl_fragment),
        )
    )


def ensure_base_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS terminology_system (
                terminology_key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                concept_table TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'imported',
                description TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                keywords TEXT[] NOT NULL DEFAULT '{}',
                connections JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS terminology_version (
                terminology_key TEXT NOT NULL,
                version_key TEXT NOT NULL,
                version_label TEXT,
                edition_type TEXT NOT NULL DEFAULT 'standalone',
                base_version_key TEXT,
                concept_table TEXT NOT NULL,
                is_default BOOLEAN NOT NULL DEFAULT false,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (terminology_key, version_key)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS terminology_release_package (
                terminology_key TEXT NOT NULL,
                package_key TEXT NOT NULL,
                package_version TEXT NOT NULL,
                package_type TEXT NOT NULL DEFAULT 'release',
                description TEXT,
                source_uri TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (terminology_key, package_key, package_version)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS terminology_edition_package (
                terminology_key TEXT NOT NULL,
                version_key TEXT NOT NULL,
                package_key TEXT NOT NULL,
                package_version TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'primary',
                include_order INTEGER NOT NULL DEFAULT 100,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (terminology_key, version_key, package_key, package_version)
            )
            """
        )
    ensure_column(
        conn, "terminology_system", "kind", "kind TEXT NOT NULL DEFAULT 'imported'"
    )
    ensure_column(conn, "terminology_system", "description", "description TEXT")
    ensure_column(
        conn,
        "terminology_system",
        "metadata",
        "metadata JSONB NOT NULL DEFAULT '{}'::jsonb",
    )
    ensure_column(
        conn,
        "terminology_system",
        "keywords",
        "keywords TEXT[] NOT NULL DEFAULT '{}'",
    )
    ensure_column(
        conn,
        "terminology_system",
        "connections",
        "connections JSONB NOT NULL DEFAULT '[]'::jsonb",
    )
    ensure_column(
        conn,
        "terminology_version",
        "version_label",
        "version_label TEXT",
    )
    ensure_column(
        conn,
        "terminology_version",
        "edition_type",
        "edition_type TEXT NOT NULL DEFAULT 'standalone'",
    )
    ensure_column(
        conn,
        "terminology_version",
        "base_version_key",
        "base_version_key TEXT",
    )
    ensure_column(
        conn,
        "terminology_version",
        "is_default",
        "is_default BOOLEAN NOT NULL DEFAULT false",
    )
    ensure_column(
        conn,
        "terminology_version",
        "metadata",
        "metadata JSONB NOT NULL DEFAULT '{}'::jsonb",
    )
    with conn.cursor() as cur:
        create_index_if_missing(
            cur,
            "idx_terminology_version_default_unique",
            """
            CREATE UNIQUE INDEX idx_terminology_version_default_unique
            ON terminology_version(terminology_key)
            WHERE is_default
            """,
        )
        create_index_if_missing(
            cur,
            "idx_terminology_version_concept_table_unique",
            """
            CREATE UNIQUE INDEX idx_terminology_version_concept_table_unique
            ON terminology_version(terminology_key, concept_table)
            """,
        )
        create_index_if_missing(
            cur,
            "idx_terminology_edition_package_version",
            """
            CREATE INDEX idx_terminology_edition_package_version
            ON terminology_edition_package(terminology_key, version_key, include_order)
            """,
        )
        create_index_if_missing(
            cur,
            "idx_terminology_release_package_type",
            """
            CREATE INDEX idx_terminology_release_package_type
            ON terminology_release_package(terminology_key, package_type)
            """,
        )


def ensure_embedding_model_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS embedding_model (
                terminology_key TEXT NOT NULL DEFAULT 'snomed',
                terminology_version TEXT NOT NULL DEFAULT 'current',
                model_key TEXT NOT NULL,
                provider TEXT NOT NULL,
                provider_model TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                embedding_table TEXT,
                storage_type TEXT NOT NULL DEFAULT 'vector',
                distance TEXT NOT NULL DEFAULT 'cosine',
                text_source TEXT NOT NULL DEFAULT 'search_text',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        embedding_model_columns = table_columns(conn, "embedding_model")
        if "terminology_key" not in embedding_model_columns:
            cur.execute(
                """
                ALTER TABLE embedding_model
                ADD COLUMN terminology_key TEXT NOT NULL DEFAULT 'snomed'
                """
            )
        if "terminology_version" not in embedding_model_columns:
            cur.execute(
                """
                ALTER TABLE embedding_model
                ADD COLUMN terminology_version TEXT NOT NULL DEFAULT 'current'
                """
            )
        if "embedding_table" not in embedding_model_columns:
            cur.execute(
                """
                ALTER TABLE embedding_model
                ADD COLUMN embedding_table TEXT
                """
            )
        if "storage_type" not in embedding_model_columns:
            cur.execute(
                """
                ALTER TABLE embedding_model
                ADD COLUMN storage_type TEXT NOT NULL DEFAULT 'vector'
                """
            )
        pkey_row = cur.execute(
            """
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'embedding_model_pkey'
              AND conrelid = 'embedding_model'::regclass
            """
        ).fetchone()
        if pkey_row is not None:
            cur.execute(
                "ALTER TABLE embedding_model DROP CONSTRAINT embedding_model_pkey"
            )
        create_index_if_missing(
            cur,
            "idx_embedding_model_terminology_model_key",
            """
            CREATE UNIQUE INDEX idx_embedding_model_terminology_model_key
            ON embedding_model(terminology_key, terminology_version, model_key)
            """,
        )


def create_concept_document_table(
    conn,
    *,
    terminology_key: str,
    terminology_version: str | None = None,
    embedding_dimensions: int,
) -> str:
    terminology_key = normalize_terminology_key(terminology_key)
    version_key = normalize_version_key(terminology_version)
    table_name = versioned_concept_table_name(terminology_key, version_key)
    suffix = safe_index_suffix(f"{terminology_key}_{version_key}")
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
            CREATE TABLE IF NOT EXISTS {table_name} (
                concept_id BIGINT PRIMARY KEY,
                active BOOLEAN NOT NULL,
                effective_time INTEGER NOT NULL,
                module_id BIGINT NOT NULL,
                definition_status_id BIGINT NOT NULL,
                definition_status TEXT,
                fsn TEXT,
                preferred_term TEXT,
                semantic_tag TEXT,
                synonyms TEXT[] NOT NULL DEFAULT '{{}}',
                text_definitions TEXT[] NOT NULL DEFAULT '{{}}',
                parent_ids BIGINT[] NOT NULL DEFAULT '{{}}',
                ancestor_ids BIGINT[] NOT NULL DEFAULT '{{}}',
                child_ids BIGINT[] NOT NULL DEFAULT '{{}}',
                descriptions JSONB NOT NULL DEFAULT '[]'::jsonb,
                relationships JSONB NOT NULL DEFAULT '[]'::jsonb,
                concrete_values JSONB NOT NULL DEFAULT '[]'::jsonb,
                maps JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                associations JSONB NOT NULL DEFAULT '[]'::jsonb,
                refset_ids BIGINT[] NOT NULL DEFAULT '{{}}',
                attributes JSONB NOT NULL DEFAULT '[]'::jsonb,
                search_text TEXT NOT NULL,
                search_vector TSVECTOR GENERATED ALWAYS AS (
                    to_tsvector('english', coalesce(search_text, ''))
                ) STORED,
                embedding VECTOR({embedding_dimensions}),
                embedding_model TEXT,
                embedding_updated_at TIMESTAMPTZ,
                payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
            ).format(
                table_name=sql.Identifier(table_name),
                embedding_dimensions=sql.Literal(embedding_dimensions),
            )
        )
        indexes = [
            (
                f"idx_{suffix}_concept_document_active",
                sql.SQL("CREATE INDEX {index_name} ON {table_name}(active)"),
            ),
            (
                f"idx_{suffix}_concept_document_semantic_tag",
                sql.SQL("CREATE INDEX {index_name} ON {table_name}(semantic_tag)"),
            ),
            (
                f"idx_{suffix}_concept_document_search_vector",
                sql.SQL(
                    "CREATE INDEX {index_name} ON {table_name} USING GIN(search_vector)"
                ),
            ),
            (
                f"idx_{suffix}_concept_document_preferred_trgm",
                sql.SQL(
                    "CREATE INDEX {index_name} ON {table_name} "
                    "USING GIN(preferred_term gin_trgm_ops)"
                ),
            ),
            (
                f"idx_{suffix}_concept_document_fsn_trgm",
                sql.SQL(
                    "CREATE INDEX {index_name} ON {table_name} "
                    "USING GIN(fsn gin_trgm_ops)"
                ),
            ),
            (
                f"idx_{suffix}_concept_document_search_text_trgm",
                sql.SQL(
                    "CREATE INDEX {index_name} ON {table_name} "
                    "USING GIN(search_text gin_trgm_ops)"
                ),
            ),
            (
                f"idx_{suffix}_concept_document_ancestor_ids",
                sql.SQL(
                    "CREATE INDEX {index_name} ON {table_name} USING GIN(ancestor_ids)"
                ),
            ),
            (
                f"idx_{suffix}_concept_document_parent_ids",
                sql.SQL(
                    "CREATE INDEX {index_name} ON {table_name} USING GIN(parent_ids)"
                ),
            ),
            (
                f"idx_{suffix}_concept_document_child_ids",
                sql.SQL(
                    "CREATE INDEX {index_name} ON {table_name} USING GIN(child_ids)"
                ),
            ),
            (
                f"idx_{suffix}_concept_document_synonyms",
                sql.SQL(
                    "CREATE INDEX {index_name} ON {table_name} USING GIN(synonyms)"
                ),
            ),
            (
                f"idx_{suffix}_concept_document_payload_code",
                sql.SQL(
                    "CREATE INDEX {index_name} ON {table_name} ((payload->>'code'))"
                ),
            ),
            (
                f"idx_{suffix}_concept_document_payload_code_trgm",
                sql.SQL(
                    "CREATE INDEX {index_name} ON {table_name} "
                    "USING GIN((payload->>'code') gin_trgm_ops)"
                ),
            ),
            (
                f"idx_{suffix}_concept_document_payload_display_trgm",
                sql.SQL(
                    "CREATE INDEX {index_name} ON {table_name} "
                    "USING GIN((payload->>'displayCode') gin_trgm_ops)"
                ),
            ),
        ]
        for index_name, statement in indexes:
            create_index_if_missing(
                cur,
                index_name,
                statement.format(
                    index_name=sql.Identifier(index_name),
                    table_name=sql.Identifier(table_name),
                ),
            )
    return table_name


def ensure_custom_code_index(conn, *, terminology_key: str) -> None:
    table_name = concept_table_name(terminology_key)
    suffix = safe_index_suffix(terminology_key)
    index_name = f"idx_{suffix}_concept_document_code_unique"
    with conn.cursor() as cur:
        create_index_if_missing(
            cur,
            index_name,
            sql.SQL(
                """
                CREATE UNIQUE INDEX {index_name}
                ON {table_name} ((payload->>'code'))
                WHERE payload ? 'code'
                """
            ).format(
                index_name=sql.Identifier(index_name),
                table_name=sql.Identifier(table_name),
            ),
        )


def create_embedding_table(
    conn,
    *,
    table_name: str,
    concept_table: str,
    dimensions: int,
    storage_type: str,
    halfvec_storage: str,
) -> str:
    if storage_type == halfvec_storage:
        conn.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {table_name} (
                    concept_id BIGINT PRIMARY KEY REFERENCES {concept_table}(concept_id) ON DELETE CASCADE,
                    dimensions INTEGER NOT NULL,
                    embedding VECTOR({dimensions}) NOT NULL,
                    embedding_half HALFVEC({dimensions}) NOT NULL,
                    source_hash TEXT NOT NULL,
                    embedded_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            ).format(
                table_name=sql.Identifier(table_name),
                concept_table=sql.Identifier(concept_table),
                dimensions=sql.Literal(dimensions),
            )
        )
        conn.execute(
            sql.SQL(
                "ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS embedding_half HALFVEC({dimensions})"
            ).format(
                table_name=sql.Identifier(table_name),
                dimensions=sql.Literal(dimensions),
            )
        )
        return table_name
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {table_name} (
                concept_id BIGINT PRIMARY KEY REFERENCES {concept_table}(concept_id) ON DELETE CASCADE,
                dimensions INTEGER NOT NULL,
                embedding VECTOR({dimensions}) NOT NULL,
                source_hash TEXT NOT NULL,
                embedded_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ).format(
            table_name=sql.Identifier(table_name),
            concept_table=sql.Identifier(concept_table),
            dimensions=sql.Literal(dimensions),
        )
    )
    return table_name


def drop_index(conn, index_name: str) -> None:
    conn.execute(
        sql.SQL("DROP INDEX IF EXISTS {index_name}").format(
            index_name=sql.Identifier(index_name)
        )
    )


def create_embedding_hnsw_index(
    conn,
    *,
    table_name: str,
    index_name: str,
    storage_type: str,
    halfvec_storage: str,
) -> None:
    if storage_type == halfvec_storage:
        conn.execute(
            sql.SQL(
                """
                CREATE INDEX IF NOT EXISTS {index_name}
                ON {table_name}
                USING hnsw (embedding_half halfvec_cosine_ops)
                """
            ).format(
                index_name=sql.Identifier(index_name),
                table_name=sql.Identifier(table_name),
            )
        )
        return
    conn.execute(
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS {index_name}
            ON {table_name}
            USING hnsw (embedding vector_cosine_ops)
            """
        ).format(
            index_name=sql.Identifier(index_name),
            table_name=sql.Identifier(table_name),
        )
    )
