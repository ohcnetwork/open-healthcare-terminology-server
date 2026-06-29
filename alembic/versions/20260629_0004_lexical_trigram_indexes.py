from __future__ import annotations

from alembic import op

revision = "20260629_0004"
down_revision = "20260625_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        DO $$
        DECLARE
            rec record;
            suffix text;
        BEGIN
            FOR rec IN
                SELECT DISTINCT terminology_key, version_key, concept_table
                FROM terminology_version
                WHERE concept_table IS NOT NULL
                  AND to_regclass(concept_table) IS NOT NULL
            LOOP
                suffix := left(
                    COALESCE(
                        NULLIF(
                            lower(
                                regexp_replace(
                                    rec.terminology_key || '_' || rec.version_key,
                                    '[^a-zA-Z0-9_]+',
                                    '_',
                                    'g'
                                )
                            ),
                            ''
                        ),
                        'model'
                    ),
                    24
                );

                EXECUTE format(
                    'CREATE INDEX IF NOT EXISTS %I ON %I(semantic_tag)',
                    'idx_' || suffix || '_concept_document_semantic_tag',
                    rec.concept_table
                );
                EXECUTE format(
                    'CREATE INDEX IF NOT EXISTS %I ON %I USING GIN(preferred_term gin_trgm_ops)',
                    'idx_' || suffix || '_concept_document_preferred_trgm',
                    rec.concept_table
                );
                EXECUTE format(
                    'CREATE INDEX IF NOT EXISTS %I ON %I USING GIN(fsn gin_trgm_ops)',
                    'idx_' || suffix || '_concept_document_fsn_trgm',
                    rec.concept_table
                );
                EXECUTE format(
                    'CREATE INDEX IF NOT EXISTS %I ON %I USING GIN(search_text gin_trgm_ops)',
                    'idx_' || suffix || '_concept_document_search_text_trgm',
                    rec.concept_table
                );
                EXECUTE format(
                    'CREATE INDEX IF NOT EXISTS %I ON %I USING GIN((payload->>''code'') gin_trgm_ops)',
                    'idx_' || suffix || '_concept_document_payload_code_trgm',
                    rec.concept_table
                );
                EXECUTE format(
                    'CREATE INDEX IF NOT EXISTS %I ON %I USING GIN((payload->>''displayCode'') gin_trgm_ops)',
                    'idx_' || suffix || '_concept_document_payload_display_trgm',
                    rec.concept_table
                );
            END LOOP;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            rec record;
            suffix text;
        BEGIN
            FOR rec IN
                SELECT DISTINCT terminology_key, version_key, concept_table
                FROM terminology_version
                WHERE concept_table IS NOT NULL
            LOOP
                suffix := left(
                    COALESCE(
                        NULLIF(
                            lower(
                                regexp_replace(
                                    rec.terminology_key || '_' || rec.version_key,
                                    '[^a-zA-Z0-9_]+',
                                    '_',
                                    'g'
                                )
                            ),
                            ''
                        ),
                        'model'
                    ),
                    24
                );

                EXECUTE format('DROP INDEX IF EXISTS %I', 'idx_' || suffix || '_concept_document_payload_display_trgm');
                EXECUTE format('DROP INDEX IF EXISTS %I', 'idx_' || suffix || '_concept_document_payload_code_trgm');
                EXECUTE format('DROP INDEX IF EXISTS %I', 'idx_' || suffix || '_concept_document_search_text_trgm');
                EXECUTE format('DROP INDEX IF EXISTS %I', 'idx_' || suffix || '_concept_document_fsn_trgm');
                EXECUTE format('DROP INDEX IF EXISTS %I', 'idx_' || suffix || '_concept_document_preferred_trgm');
                EXECUTE format('DROP INDEX IF EXISTS %I', 'idx_' || suffix || '_concept_document_semantic_tag');
            END LOOP;
        END
        $$;
        """
    )
