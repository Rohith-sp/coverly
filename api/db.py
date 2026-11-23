"""SQLite connection and schema (PRD §11). Raw `sqlite3`, parameterized queries only."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    raw_input TEXT NOT NULL,
    level TEXT NOT NULL,
    language TEXT NOT NULL,
    budget_minutes INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topics (
    id TEXT NOT NULL,
    plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    module TEXT NOT NULL,
    name TEXT NOT NULL,
    position INTEGER NOT NULL,
    required_depth INTEGER NOT NULL,
    PRIMARY KEY (plan_id, id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    quota_units INTEGER NOT NULL DEFAULT 0,
    llm_tokens INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_jobs_plan ON jobs(plan_id);
CREATE INDEX IF NOT EXISTS idx_jobs_started ON jobs(started_at);

CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    channel_title TEXT NOT NULL,
    duration_s INTEGER NOT NULL,
    views INTEGER NOT NULL,
    likes INTEGER NOT NULL,
    published_at TIMESTAMP,
    description TEXT NOT NULL,
    has_transcript INTEGER NOT NULL DEFAULT 0,
    fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transcripts (
    video_id TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    language TEXT,
    source TEXT NOT NULL,
    text_json TEXT NOT NULL,
    fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS segments (
    id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    start_s INTEGER NOT NULL,
    end_s INTEGER NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_video ON segments(video_id);

CREATE TABLE IF NOT EXISTS segment_tags (
    segment_id TEXT NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    plan_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    depth INTEGER NOT NULL,
    confidence REAL NOT NULL,
    PRIMARY KEY (segment_id, plan_id, topic_id),
    FOREIGN KEY (plan_id, topic_id) REFERENCES topics(plan_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_segment_tags_plan_topic ON segment_tags(plan_id, topic_id);

CREATE TABLE IF NOT EXISTS search_cache (
    query TEXT NOT NULL,
    language TEXT NOT NULL,
    video_ids TEXT NOT NULL,
    cached_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (query, language)
);
CREATE INDEX IF NOT EXISTS idx_search_cache_cached_at ON search_cache(cached_at);

CREATE TABLE IF NOT EXISTS results (
    plan_id TEXT PRIMARY KEY REFERENCES plans(id) ON DELETE CASCADE,
    items_json TEXT NOT NULL,
    uncovered_json TEXT NOT NULL,
    total_minutes INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open a connection with WAL, foreign keys and dict-like rows."""
    conn = sqlite3.connect(path or get_settings().db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
