from __future__ import annotations

import pytest

from app.services import db


def test_backend_selection_is_explicit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    with pytest.raises(db.DatabaseConfigurationError, match="STORAGE_BACKEND is required"):
        db.backend()


def test_postgres_configuration_lists_missing_values(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    for name in db.POSTGRES_REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(db.DatabaseConfigurationError, match="RDS_HOST.*RDS_DB.*RDS_USER.*RDS_PASSWORD"):
        db.validate_configuration()


def test_sqlite_connection_validation_is_explicit(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "db.sqlite")
    assert db.validate_connection() == {"backend": "sqlite", "status": "connected"}
