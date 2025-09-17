"""Database schema definitions for the 3dfs application.

This module centralises the SQLAlchemy declarative mappings used throughout the
project.  The schema models the core asset management concepts:

* :class:`Asset` – the top-level entity that groups printable resources.
* :class:`Version` – a concrete revision of an asset along with metadata.
* :class:`Attachment` – supplemental files (e.g., STL previews) linked to a
  version.
* :class:`Tag` – user defined labels that can be attached to assets via a
  many-to-many association table.
* :class:`PrinterProfile` – reusable printer settings that can be referenced
  by versions.
* :class:`AuditLog` – append-only records describing actions performed on an
  asset.

Alongside the ORM mappings the module provides helpers for instantiating a
SQLite-backed engine, constructing sessions, and accessing the shared
:class:`sqlalchemy.schema.MetaData` instance.  Tests and runtime code can rely on
``get_engine``/``create_session_factory`` to bootstrap the database without
having to duplicate configuration boilerplate.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

DEFAULT_DATABASE_URL = "sqlite+pysqlite:///:memory:"
"""Default connection string used for in-memory testing and local usage."""


def _utcnow() -> datetime:
    """Return an aware UTC timestamp used by default for temporal columns."""

    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Base class for all ORM models within the 3dfs database schema."""


metadata = Base.metadata
"""Exposed metadata object for migrations and table management."""


def _configure_sqlite_pragma(engine: Engine) -> None:
    """Ensure SQLite engines enforce foreign key constraints."""

    if engine.url.get_backend_name() != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def _create_engine(url: str, **kwargs: Any) -> Engine:
    """Create a configured SQLAlchemy engine and enable SQLite pragmas."""

    engine = create_engine(url, future=True, **kwargs)
    _configure_sqlite_pragma(engine)
    return engine


_DEFAULT_ENGINE: Engine = _create_engine(DEFAULT_DATABASE_URL)
"""Module-level default engine reused by :func:`get_engine`."""


def get_engine(url: str | None = None, **kwargs: Any) -> Engine:
    """Return a configured SQLAlchemy engine.

    Parameters
    ----------
    url:
        Optional database URL. When omitted the shared in-memory SQLite engine
        is returned.
    **kwargs:
        Additional keyword arguments forwarded to :func:`sqlalchemy.create_engine`.
    """

    if url is None and not kwargs:
        return _DEFAULT_ENGINE

    actual_url = url or DEFAULT_DATABASE_URL
    return _create_engine(actual_url, **kwargs)


def create_session_factory(
    engine: Engine | None = None,
    *,
    expire_on_commit: bool = False,
    autoflush: bool = False,
) -> sessionmaker[Session]:
    """Return a ``sessionmaker`` bound to the supplied engine.

    The helper mirrors the default configuration used by :data:`SessionLocal`
    which keeps objects live after commits.  Passing an explicit engine allows
    tests to operate on isolated databases.
    """

    bound_engine = engine or get_engine()
    return sessionmaker(
        bind=bound_engine,
        expire_on_commit=expire_on_commit,
        autoflush=autoflush,
        future=True,
    )


SessionLocal = create_session_factory()
"""Ready-to-use session factory bound to the default engine."""


asset_tag_table = Table(
    "asset_tags",
    metadata,
    Column("asset_id", ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    UniqueConstraint("asset_id", "tag_id", name="uq_asset_tag"),
)
"""Association table linking :class:`Asset` and :class:`Tag` records."""


class Asset(Base):
    """Top-level entity describing a printable asset and its relationships."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    versions: Mapped[list[Version]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Version.number",
    )
    tags: Mapped[list[Tag]] = relationship(
        secondary=lambda: asset_tag_table,
        back_populates="assets",
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="desc(AuditLog.created_at)",
    )


class Version(Base):
    """Concrete revision of an asset with optional printer profile linkage."""

    __tablename__ = "versions"
    __table_args__ = (
        UniqueConstraint("asset_id", "number", name="uq_version_number_per_asset"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), index=True, nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text())
    printer_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("printer_profiles.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
    )

    asset: Mapped[Asset] = relationship(back_populates="versions")
    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Attachment.id",
    )
    printer_profile: Mapped[PrinterProfile | None] = relationship(
        back_populates="versions"
    )


class Attachment(Base):
    """File linked to a specific version (e.g. preview images or archives)."""

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("versions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    uri: Mapped[str] = mapped_column(String(512), nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
    )

    version: Mapped[Version] = relationship(back_populates="attachments")


class Tag(Base):
    """User defined label that can be applied to multiple assets."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text())

    assets: Mapped[list[Asset]] = relationship(
        secondary=lambda: asset_tag_table,
        back_populates="tags",
    )


class PrinterProfile(Base):
    """Reusable printer configuration referenced by asset versions."""

    __tablename__ = "printer_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    nozzle_diameter: Mapped[float | None] = mapped_column(Float())
    material: Mapped[str | None] = mapped_column(String(64))
    settings: Mapped[dict[str, Any] | None] = mapped_column(JSON())

    versions: Mapped[list[Version]] = relationship(back_populates="printer_profile")


class AuditLog(Base):
    """Append-only log describing actions executed on assets."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), index=True, nullable=False
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
    )

    asset: Mapped[Asset] = relationship(back_populates="audit_logs")


__all__ = [
    "Attachment",
    "AuditLog",
    "Asset",
    "Base",
    "DEFAULT_DATABASE_URL",
    "SessionLocal",
    "Tag",
    "Version",
    "PrinterProfile",
    "asset_tag_table",
    "create_session_factory",
    "get_engine",
    "metadata",
]
