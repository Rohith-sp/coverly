import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import db
from config import get_settings


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the app at a fresh temp DB."""
    path = tmp_path / "test.db"
    monkeypatch.setenv("DB_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(db_path)
    db.init_schema(c)
    yield c
    c.close()


@pytest.fixture
def client(db_path: Path) -> Iterator[TestClient]:
    from main import app

    with TestClient(app) as c:
        yield c
