from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from psycopg import sql

CONCEPT_UPSERT_SQL = """
    INSERT INTO {concept_table} (
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
    )
    VALUES (
        %(concept_id)s,
        %(active)s,
        %(effective_time)s,
        %(module_id)s,
        %(definition_status_id)s,
        %(definition_status)s,
        %(fsn)s,
        %(preferred_term)s,
        %(semantic_tag)s,
        %(synonyms)s,
        %(text_definitions)s,
        %(parent_ids)s,
        %(ancestor_ids)s,
        %(child_ids)s,
        %(descriptions)s,
        %(relationships)s,
        %(concrete_values)s,
        %(maps)s,
        %(associations)s,
        %(refset_ids)s,
        %(attributes)s,
        %(search_text)s,
        NULL,
        NULL,
        NULL,
        %(payload)s,
        now()
    )
    ON CONFLICT (concept_id) DO UPDATE SET
        active = excluded.active,
        effective_time = excluded.effective_time,
        module_id = excluded.module_id,
        definition_status_id = excluded.definition_status_id,
        definition_status = excluded.definition_status,
        fsn = excluded.fsn,
        preferred_term = excluded.preferred_term,
        semantic_tag = excluded.semantic_tag,
        synonyms = excluded.synonyms,
        text_definitions = excluded.text_definitions,
        parent_ids = excluded.parent_ids,
        ancestor_ids = excluded.ancestor_ids,
        child_ids = excluded.child_ids,
        descriptions = excluded.descriptions,
        relationships = excluded.relationships,
        concrete_values = excluded.concrete_values,
        maps = excluded.maps,
        associations = excluded.associations,
        refset_ids = excluded.refset_ids,
        attributes = excluded.attributes,
        search_text = excluded.search_text,
        payload = excluded.payload,
        updated_at = now()
"""


def batched(
    values: Iterable[dict[str, Any]], batch_size: int
) -> Iterable[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for value in values:
        batch.append(value)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def upsert_documents(conn, *, concept_table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            sql.SQL(CONCEPT_UPSERT_SQL).format(
                concept_table=sql.Identifier(concept_table)
            ),
            rows,
        )
