from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260625_0003"
down_revision = "20260625_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "terminology_version",
        sa.Column("edition_type", sa.Text(), nullable=False, server_default="standalone"),
        if_not_exists=True,
    )
    op.add_column(
        "terminology_version",
        sa.Column("base_version_key", sa.Text()),
        if_not_exists=True,
    )
    op.create_table(
        "terminology_release_package",
        sa.Column("terminology_key", sa.Text(), nullable=False),
        sa.Column("package_key", sa.Text(), nullable=False),
        sa.Column("package_version", sa.Text(), nullable=False),
        sa.Column("package_type", sa.Text(), nullable=False, server_default="release"),
        sa.Column("description", sa.Text()),
        sa.Column("source_uri", sa.Text()),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("terminology_key", "package_key", "package_version"),
        if_not_exists=True,
    )
    op.create_table(
        "terminology_edition_package",
        sa.Column("terminology_key", sa.Text(), nullable=False),
        sa.Column("version_key", sa.Text(), nullable=False),
        sa.Column("package_key", sa.Text(), nullable=False),
        sa.Column("package_version", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="primary"),
        sa.Column("include_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("terminology_key", "version_key", "package_key", "package_version"),
        if_not_exists=True,
    )
    op.create_index(
        "idx_terminology_edition_package_version",
        "terminology_edition_package",
        ["terminology_key", "version_key", "include_order"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_terminology_release_package_type",
        "terminology_release_package",
        ["terminology_key", "package_type"],
        if_not_exists=True,
    )
    op.execute(
        """
        INSERT INTO terminology_release_package (
            terminology_key,
            package_key,
            package_version,
            package_type,
            description,
            metadata,
            updated_at
        )
        SELECT
            terminology_key,
            terminology_key || '-current',
            version_key,
            CASE WHEN metadata->>'migratedFromUnversioned' = 'true' THEN 'migrated' ELSE 'release' END,
            'Package registered from existing edition ' || version_key,
            jsonb_build_object('migratedFromEdition', version_key),
            now()
        FROM terminology_version
        ON CONFLICT (terminology_key, package_key, package_version) DO NOTHING
        """
    )
    op.execute(
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
            version_key,
            terminology_key || '-current',
            version_key,
            'primary',
            100,
            jsonb_build_object('migratedFromUnversioned', true),
            now()
        FROM terminology_version
        ON CONFLICT (terminology_key, version_key, package_key, package_version) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index(
        "idx_terminology_release_package_type",
        table_name="terminology_release_package",
        if_exists=True,
    )
    op.drop_index(
        "idx_terminology_edition_package_version",
        table_name="terminology_edition_package",
        if_exists=True,
    )
    op.drop_table("terminology_edition_package", if_exists=True)
    op.drop_table("terminology_release_package", if_exists=True)
    op.drop_column("terminology_version", "base_version_key", if_exists=True)
    op.drop_column("terminology_version", "edition_type", if_exists=True)
