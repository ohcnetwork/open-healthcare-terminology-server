from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Column, Computed, DateTime, Integer, MetaData, Table, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ots.terminology import concept_table_name


class Base(DeclarativeBase):
    metadata = MetaData()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )


class TerminologySystem(Base, TimestampMixin):
    __tablename__ = "terminology_system"

    terminology_key: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    concept_table: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'imported'"))
    description: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[Any] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'"),
    )
    connections: Mapped[Any] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )


class TerminologyVersion(Base, TimestampMixin):
    __tablename__ = "terminology_version"

    terminology_key: Mapped[str] = mapped_column(Text, primary_key=True)
    version_key: Mapped[str] = mapped_column(Text, primary_key=True)
    version_label: Mapped[str | None] = mapped_column(Text)
    edition_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'standalone'"))
    base_version_key: Mapped[str | None] = mapped_column(Text)
    concept_table: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    metadata_json: Mapped[Any] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )


class TerminologyReleasePackage(Base, TimestampMixin):
    __tablename__ = "terminology_release_package"

    terminology_key: Mapped[str] = mapped_column(Text, primary_key=True)
    package_key: Mapped[str] = mapped_column(Text, primary_key=True)
    package_version: Mapped[str] = mapped_column(Text, primary_key=True)
    package_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'release'"))
    description: Mapped[str | None] = mapped_column(Text)
    source_uri: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[Any] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )


class TerminologyEditionPackage(Base, TimestampMixin):
    __tablename__ = "terminology_edition_package"

    terminology_key: Mapped[str] = mapped_column(Text, primary_key=True)
    version_key: Mapped[str] = mapped_column(Text, primary_key=True)
    package_key: Mapped[str] = mapped_column(Text, primary_key=True)
    package_version: Mapped[str] = mapped_column(Text, primary_key=True)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'primary'"))
    include_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))
    metadata_json: Mapped[Any] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )


class EmbeddingModel(Base):
    __tablename__ = "embedding_model"

    terminology_key: Mapped[str] = mapped_column(Text, primary_key=True, server_default=text("'snomed'"))
    terminology_version: Mapped[str] = mapped_column(Text, primary_key=True, server_default=text("'current'"))
    model_key: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_model: Mapped[str] = mapped_column(Text, nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_table: Mapped[str | None] = mapped_column(Text)
    storage_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'vector'"))
    distance: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'cosine'"))
    text_source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'search_text'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )


def concept_document_table(
    terminology_key: str,
    *,
    metadata: MetaData | None = None,
) -> Table:
    table_metadata = metadata or Base.metadata
    table_name = concept_table_name(terminology_key)
    if table_name in table_metadata.tables:
        return table_metadata.tables[table_name]
    return Table(
        table_name,
        table_metadata,
        Column("concept_id", BigInteger, primary_key=True),
        Column("active", Boolean, nullable=False),
        Column("effective_time", Integer, nullable=False),
        Column("module_id", BigInteger, nullable=False),
        Column("definition_status_id", BigInteger, nullable=False),
        Column("definition_status", Text),
        Column("fsn", Text),
        Column("preferred_term", Text),
        Column("semantic_tag", Text),
        Column("synonyms", ARRAY(Text), nullable=False, server_default=text("'{}'")),
        Column("text_definitions", ARRAY(Text), nullable=False, server_default=text("'{}'")),
        Column("parent_ids", ARRAY(BigInteger), nullable=False, server_default=text("'{}'")),
        Column("ancestor_ids", ARRAY(BigInteger), nullable=False, server_default=text("'{}'")),
        Column("child_ids", ARRAY(BigInteger), nullable=False, server_default=text("'{}'")),
        Column("descriptions", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
        Column("relationships", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
        Column("concrete_values", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
        Column("maps", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
        Column("associations", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
        Column("refset_ids", ARRAY(BigInteger), nullable=False, server_default=text("'{}'")),
        Column("attributes", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
        Column("search_text", Text, nullable=False),
        Column(
            "search_vector",
            TSVECTOR,
            Computed("to_tsvector('english', coalesce(search_text, ''))", persisted=True),
        ),
        Column("embedding_model", Text),
        Column("embedding_updated_at", DateTime(timezone=True)),
        Column("payload", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
