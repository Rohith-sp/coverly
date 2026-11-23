import sqlite3

import pytest

EXPECTED = {
    "plans",
    "topics",
    "jobs",
    "videos",
    "transcripts",
    "segments",
    "segment_tags",
    "search_cache",
    "results",
}


def test_schema_creates_all_tables(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert EXPECTED <= {r["name"] for r in rows}


def test_wal_and_foreign_keys(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO jobs(id, plan_id, status) VALUES ('j', 'missing', 'queued')")


def test_schema_is_idempotent(conn: sqlite3.Connection) -> None:
    import db

    db.init_schema(conn)
