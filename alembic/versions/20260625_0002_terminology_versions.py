from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260625_0002"
down_revision = "20260624_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "terminology_version",
        sa.Column("terminology_key", sa.Text(), nullable=False),
        sa.Column("version_key", sa.Text(), nullable=False),
        sa.Column("version_label", sa.Text()),
        sa.Column("concept_table", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("terminology_key", "version_key"),
        if_not_exists=True,
    )
    op.create_index(
        "idx_terminology_version_default_unique",
        "terminology_version",
        ["terminology_key"],
        unique=True,
        postgresql_where=sa.text("is_default"),
        if_not_exists=True,
    )
    op.create_index(
        "idx_terminology_version_concept_table_unique",
        "terminology_version",
        ["terminology_key", "concept_table"],
        unique=True,
        if_not_exists=True,
    )
    op.execute(
        """
        INSERT INTO terminology_version (
            terminology_key,
            version_key,
            version_label,
            concept_table,
            is_default,
            metadata,
            updated_at
        )
        SELECT
            terminology_key,
            'current',
            'Current import',
            concept_table,
            true,
            jsonb_build_object('migratedFromUnversioned', true),
            now()
        FROM terminology_system
        ON CONFLICT (terminology_key, version_key) DO UPDATE SET
            concept_table = excluded.concept_table,
            is_default = true,
            updated_at = now()
        """
    )
    op.add_column(
        "embedding_model",
        sa.Column(
            "terminology_version",
            sa.Text(),
            nullable=False,
            server_default="current",
        ),
        if_not_exists=True,
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'embedding_model_pkey'
                  AND conrelid = 'embedding_model'::regclass
            ) THEN
                ALTER TABLE embedding_model DROP CONSTRAINT embedding_model_pkey;
            END IF;
        END
        $$;
        """
    )
    op.drop_index(
        "idx_embedding_model_terminology_model_key",
        table_name="embedding_model",
        if_exists=True,
    )
    op.create_index(
        "idx_embedding_model_terminology_model_key",
        "embedding_model",
        ["terminology_key", "terminology_version", "model_key"],
        unique=True,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_embedding_model_terminology_model_key",
        table_name="embedding_model",
        if_exists=True,
    )
    op.create_index(
        "idx_embedding_model_terminology_model_key",
        "embedding_model",
        ["terminology_key", "model_key"],
        unique=True,
        if_not_exists=True,
    )
    op.drop_column("embedding_model", "terminology_version", if_exists=True)
    op.drop_index(
        "idx_terminology_version_concept_table_unique",
        table_name="terminology_version",
        if_exists=True,
    )
    op.drop_index(
        "idx_terminology_version_default_unique",
        table_name="terminology_version",
        if_exists=True,
    )
    op.drop_table("terminology_version", if_exists=True)
