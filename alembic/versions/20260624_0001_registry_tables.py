from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260624_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "terminology_system",
        sa.Column("terminology_key", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("concept_table", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="imported"),
        sa.Column("description", sa.Text()),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "keywords",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "connections",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        if_not_exists=True,
    )
    op.create_table(
        "embedding_model",
        sa.Column("terminology_key", sa.Text(), primary_key=True, server_default="snomed"),
        sa.Column("model_key", sa.Text(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_model", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding_table", sa.Text()),
        sa.Column("storage_type", sa.Text(), nullable=False, server_default="vector"),
        sa.Column("distance", sa.Text(), nullable=False, server_default="cosine"),
        sa.Column("text_source", sa.Text(), nullable=False, server_default="search_text"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        if_not_exists=True,
    )
    op.create_index(
        "idx_embedding_model_terminology_model_key",
        "embedding_model",
        ["terminology_key", "model_key"],
        unique=True,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_embedding_model_terminology_model_key",
        table_name="embedding_model",
        if_exists=True,
    )
    op.drop_table("embedding_model", if_exists=True)
    op.drop_table("terminology_system", if_exists=True)
