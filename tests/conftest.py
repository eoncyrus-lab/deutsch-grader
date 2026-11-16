from __future__ import annotations

import dataclasses
import os

import pytest

os.environ.setdefault("DG_ENV", "dev")
os.environ.setdefault("DG_SECRET_KEY", "test-secret-key-with-enough-entropy")

import app.db as app_db  # noqa: E402


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    settings = dataclasses.replace(
        app_db.settings,
        db_path=tmp_path / "app.db",
        storage_dir=tmp_path / "objects",
        env="dev",
    )
    monkeypatch.setattr(app_db, "settings", settings)
    app_db.init_db()
    return settings


@pytest.fixture
def seeded(db_env):
    from app.bootstrap import bootstrap
    return bootstrap()


@pytest.fixture
def client(seeded):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as test_client:
        yield test_client


def login(client, email="teacher@demo.local", password="Demo2026!"):
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["user"]
