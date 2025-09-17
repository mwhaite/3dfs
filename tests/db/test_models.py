"""Relationship and constraint tests for the SQLAlchemy ORM models."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from three_dfs.db import (
    Asset,
    Attachment,
    AuditLog,
    Base,
    PrinterProfile,
    Tag,
    Version,
    asset_tag_table,
    create_session_factory,
    get_engine,
)


@pytest.fixture()
def engine(tmp_path):
    """Create a temporary SQLite database for testing."""

    db_path = tmp_path / "orm.sqlite"
    engine = get_engine(f"sqlite+pysqlite:///{db_path}")
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def session(engine):
    """Yield a SQLAlchemy session bound to the temporary engine."""

    SessionFactory = create_session_factory(engine)
    db_session = SessionFactory()
    try:
        yield db_session
    finally:
        db_session.rollback()
        db_session.close()


def test_asset_version_and_attachment_cascade(session):
    """Deleting an asset removes dependent versions, attachments, and logs."""

    asset = Asset(name="Calibration Cube", description="Baseline test asset")
    version = Version(number=1, label="Initial release")
    version.attachments.append(
        Attachment(kind="model", uri="file://cube-v1.stl", checksum="abc123")
    )
    asset.versions.append(version)
    asset.audit_logs.append(AuditLog(action="create", actor="tester"))

    session.add(asset)
    session.commit()

    stored_asset = session.get(Asset, asset.id)
    assert stored_asset is not None
    assert stored_asset.versions[0].attachments[0].uri.endswith("cube-v1.stl")

    session.delete(stored_asset)
    session.commit()

    assert session.scalars(select(Version)).all() == []
    assert session.scalars(select(Attachment)).all() == []
    assert session.scalars(select(AuditLog)).all() == []


def test_asset_tag_association(session):
    """Assets expose tag relationships via the association table."""

    asset = Asset(name="Widget Mk2")
    featured = Tag(name="Featured")
    material = Tag(name="PLA")
    asset.tags.extend([featured, material])

    session.add(asset)
    session.commit()

    stored_asset = session.get(Asset, asset.id)
    assert stored_asset is not None
    assert {tag.name for tag in stored_asset.tags} == {"Featured", "PLA"}

    stored_asset.tags.remove(featured)
    session.commit()

    refreshed_asset = session.get(Asset, asset.id)
    assert {tag.name for tag in refreshed_asset.tags} == {"PLA"}

    with pytest.raises(IntegrityError):
        session.execute(
            asset_tag_table.insert().values(asset_id=asset.id, tag_id=material.id)
        )
    session.rollback()


def test_version_number_unique_per_asset(session):
    """Version numbers are unique per asset as enforced by the constraint."""

    asset = Asset(name="Constraint Demo")
    session.add(asset)
    session.flush()

    session.add(Version(number=1, label="Initial", asset=asset))
    session.flush()

    session.add(Version(number=1, label="Duplicate", asset=asset))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_printer_profile_relationship(session):
    """Versions can reference printer profiles and nullify when deleted."""

    profile = PrinterProfile(
        name="Prusa MK3",
        nozzle_diameter=0.4,
        material="PLA",
        settings={"temperature": 215},
    )
    asset = Asset(name="Calibration Fin")
    version = Version(number=1, label="Profiled", printer_profile=profile)
    asset.versions.append(version)

    session.add(asset)
    session.commit()

    stored_version = session.get(Version, version.id)
    assert stored_version is not None
    assert stored_version.printer_profile is not None
    assert stored_version.printer_profile.name == "Prusa MK3"
    assert stored_version.printer_profile.settings == {"temperature": 215}

    session.delete(profile)
    session.commit()
    session.expire_all()

    refreshed_version = session.get(Version, version.id)
    assert refreshed_version is not None
    assert refreshed_version.printer_profile is None
    assert refreshed_version.printer_profile_id is None
