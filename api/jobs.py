"""Job runner: search -> fetch -> tag -> optimize -> save result (PRD §6, PLAN §3).

All DB writes for a run happen here. Stage logic lives in `pipeline/*`.
"""

import asyncio
import json
import logging
import sqlite3
import time
from typing import cast

import httpx
from pydantic import TypeAdapter

import db
from config import Settings, get_settings
from llm import LLMError
from pipeline import cover, score, search, segment, tag, transcripts
from schemas import (
    Coverage,
    Language,
    Level,
    PlannedRange,
    ResultItem,
    ResultOut,
    Segment,
    Tag,
    Topic,
    TopicRef,
    Transcript,
    TranscriptLine,
    Video,
)

log = logging.getLogger(__name__)

HTTP_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
SEARCH_TTL = "-7 days"
_LINES = TypeAdapter(list[TranscriptLine])
_IDS = TypeAdapter(list[str])
_VIDEO_COLS = (
    "id, title, channel_id, channel_title, duration_s, views, likes, published_at,"
    " description, has_transcript"
)


class _Run:
    """Mutable job state; every change is written to the `jobs` row."""

    def __init__(self, conn: sqlite3.Connection, job_id: str) -> None:
        self.conn = conn
        self.job_id = job_id
        self.quota = 0
        self.tokens = 0
        self.progress = 0
        self.message = ""
        self._t0 = time.monotonic()

    def update(self, status: str, progress: int | None = None, message: str | None = None) -> None:
        self.progress = self.progress if progress is None else progress
        self.message = self.message if message is None else message
        self.conn.execute(
            "UPDATE jobs SET status=?, progress=?, message=?, quota_units=?, llm_tokens=?"
            " WHERE id=?",
            (status, self.progress, self.message, self.quota, self.tokens, self.job_id),
        )
        self.conn.commit()

    def stage_done(self, stage: str) -> None:
        now = time.monotonic()
        log.info(
            "job_id=%s stage=%s ms=%d quota_units=%d tokens=%d",
            self.job_id,
            stage,
            (now - self._t0) * 1000,
            self.quota,
            self.tokens,
        )
        self._t0 = now


def _load_topics(conn: sqlite3.Connection, plan_id: str) -> list[Topic]:
    rows = conn.execute(
        "SELECT id, module, name, position, required_depth FROM topics"
        " WHERE plan_id=? ORDER BY position",
        (plan_id,),
    ).fetchall()
    return [
        Topic.model_validate(
            {
                "topic_id": r["id"],
                "module": r["module"],
                "name": r["name"],
                "position": r["position"],
                "required_depth": r["required_depth"],
            }
        )
        for r in rows
    ]


def _row_video(r: sqlite3.Row) -> Video:
    return Video.model_validate({**dict(r), "has_transcript": bool(r["has_transcript"])})


def _load_videos(conn: sqlite3.Connection, ids: list[str]) -> dict[str, Video]:
    out: dict[str, Video] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(f"SELECT {_VIDEO_COLS} FROM videos WHERE id IN ({marks})", chunk)  # noqa: S608
        out.update({r["id"]: _row_video(r) for r in rows})
    return out


def _cached_search(
    conn: sqlite3.Connection, topics: list[Topic], language: Language
) -> tuple[dict[str, list[str]], list[Topic]]:
    """Split topics into (cached ids by topic, topics still to search)."""
    hits: dict[str, list[str]] = {}
    misses: list[Topic] = []
    for t in topics:
        row = conn.execute(
            "SELECT video_ids FROM search_cache WHERE query=? AND language=?"
            " AND cached_at >= datetime('now', ?)",
            (search.build_query(t, language), language, SEARCH_TTL),
        ).fetchone()
        if row is None:
            misses.append(t)
        else:
            hits[t.topic_id] = _IDS.validate_json(row["video_ids"])
    return hits, misses


def _store_search(
    conn: sqlite3.Connection, topics: list[Topic], ids: dict[str, list[str]], language: Language
) -> None:
    for t in topics:
        if t.topic_id in ids:
            conn.execute(
                "INSERT OR REPLACE INTO search_cache (query, language, video_ids) VALUES (?, ?, ?)",
                (search.build_query(t, language), language, json.dumps(ids[t.topic_id])),
            )
    conn.commit()


def _store_videos(conn: sqlite3.Connection, videos: list[Video]) -> None:
    conn.executemany(
        f"INSERT OR IGNORE INTO videos ({_VIDEO_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?)",  # noqa: S608
        [
            (
                v.id,
                v.title,
                v.channel_id,
                v.channel_title,
                v.duration_s,
                v.views,
                v.likes,
                v.published_at,
                v.description,
                int(v.has_transcript),
            )
            for v in videos
        ],
    )
    conn.commit()


def _load_transcripts(conn: sqlite3.Connection, ids: list[str]) -> dict[str, Transcript]:
    out: dict[str, Transcript] = {}
    for vid in ids:
        r = conn.execute(
            "SELECT language, source, text_json FROM transcripts"
            " WHERE video_id=? AND source != 'none'",  # failed fetches are retried next run
            (vid,),
        ).fetchone()
        if r is not None:
            out[vid] = Transcript.model_validate(
                {
                    "video_id": vid,
                    "language": r["language"],
                    "source": r["source"],
                    "lines": _LINES.validate_json(r["text_json"]),
                }
            )
    return out


def _store_transcript(conn: sqlite3.Connection, tr: Transcript) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO transcripts (video_id, language, source, text_json)"
        " VALUES (?, ?, ?, ?)",
        (tr.video_id, tr.language, tr.source, _LINES.dump_json(tr.lines).decode()),
    )
    conn.execute(
        "UPDATE videos SET has_transcript=? WHERE id=?", (int(tr.source != "none"), tr.video_id)
    )
    conn.commit()


def _store_segments(conn: sqlite3.Connection, segments: list[Segment]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO segments (id, video_id, start_s, end_s, text, source)"
        " VALUES (?,?,?,?,?,?)",
        [(s.id, s.video_id, s.start_s, s.end_s, s.text, s.source) for s in segments],
    )
    conn.commit()


def _store_tags(conn: sqlite3.Connection, plan_id: str, tags: list[Tag]) -> None:
    conn.execute("DELETE FROM segment_tags WHERE plan_id=?", (plan_id,))
    conn.executemany(
        "INSERT OR REPLACE INTO segment_tags (segment_id, plan_id, topic_id, depth, confidence)"
        " VALUES (?,?,?,?,?)",
        [(t.segment_id, plan_id, t.topic_id, t.depth, t.confidence) for t in tags],
    )
    conn.commit()


def _reason(names: list[str], level: Level, minutes: int) -> str:
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
    return f"Covers {joined} at {level} depth in {minutes} min"


def build_items(
    ranges: list[PlannedRange], videos: dict[str, Video], level: Level
) -> list[ResultItem]:
    items: list[ResultItem] = []
    for r in ranges:
        v = videos[r.video_id]
        minutes = max(1, round((r.end_s - r.start_s) / 60))
        items.append(
            ResultItem(
                video_id=v.id,
                title=v.title,
                channel=v.channel_title,
                thumbnail=f"https://i.ytimg.com/vi/{v.id}/mqdefault.jpg",
                start_s=r.start_s,
                end_s=r.end_s,
                url=f"https://www.youtube.com/watch?v={v.id}&t={r.start_s}s",
                topics=[
                    TopicRef(topic_id=t.topic_id, name=t.name, depth=t.depth) for t in r.topics
                ],
                reason=_reason([t.name for t in r.topics], level, minutes),
            )
        )
    return items


async def _fetch_transcripts(
    run: _Run, videos: list[Video], language: Language
) -> dict[str, Transcript]:
    have = _load_transcripts(run.conn, [v.id for v in videos])
    todo = [v.id for v in videos if v.id not in have]
    langs = transcripts.languages_for(language)
    sem = asyncio.Semaphore(transcripts.CONCURRENCY)
    done = 0

    async def one(vid: str) -> None:
        nonlocal done
        tr = await transcripts.fetch_transcript(vid, langs, sem)
        have[vid] = tr
        _store_transcript(run.conn, tr)
        done += 1
        run.update("fetching", 30 + 15 * done // len(todo), f"Transcripts {done}/{len(todo)}")

    await asyncio.gather(*(one(v) for v in todo))
    return have


async def _pipeline(
    run: _Run, client: httpx.AsyncClient, plan: sqlite3.Row, cfg: Settings
) -> ResultOut:
    conn = run.conn
    plan_id: str = plan["id"]
    level = cast(Level, plan["level"])
    language = cast(Language, plan["language"])
    topics = _load_topics(conn, plan_id)
    notes: list[str] = []

    # searching
    run.update("searching", 5, f"Searching {len(topics)} topics")
    ids_by_topic, misses = _cached_search(conn, topics, language)
    if misses:
        try:
            out = await search.search_topics(
                client, cfg.youtube_api_key, misses, language, cfg.search_results_per_topic
            )
        except (httpx.HTTPError, search.QuotaExceededError) as exc:
            log.warning("job_id=%s stage=search error=%s", run.job_id, exc)
            notes.append("search failed")
        else:
            run.quota += out.quota_units
            ids_by_topic.update(out.video_ids)
            _store_search(conn, misses, out.video_ids, language)
            if out.quota_exceeded:
                notes.append("YouTube quota exceeded")
    run.update("searching", 20, "Search done")
    run.stage_done("search")

    # fetching
    all_ids = list(dict.fromkeys(v for ids in ids_by_topic.values() for v in ids))
    run.update("fetching", 22, f"Fetching {len(all_ids)} videos")
    videos = _load_videos(conn, all_ids)
    missing = [v for v in all_ids if v not in videos]
    if missing:
        try:
            vout = await search.fetch_videos(client, cfg.youtube_api_key, missing)
        except (httpx.HTTPError, search.QuotaExceededError) as exc:
            log.warning("job_id=%s stage=metadata error=%s", run.job_id, exc)
            notes.append("video metadata failed")
        else:
            run.quota += vout.quota_units
            _store_videos(conn, vout.videos)
            videos.update({v.id: v for v in vout.videos})
            if vout.quota_exceeded:
                notes.append("YouTube quota exceeded")
    run.update("fetching", 30, f"{len(videos)} videos")
    trs = await _fetch_transcripts(run, list(videos.values()), language)
    videos = {
        k: v.model_copy(update={"has_transcript": trs[k].source != "none"})
        for k, v in videos.items()
    }
    run.stage_done("fetch")

    segments: list[Segment] = []
    for v in videos.values():
        segments.extend(segment.build_segments(v, trs[v.id], cfg.segment_window_seconds))
    _store_segments(conn, segments)
    quality = score.quality_scores(list(videos.values()))
    chosen = tag.select_segments(topics, ids_by_topic, quality, segments, cfg.tag_videos_per_topic)

    # tagging
    run.update("tagging", 45, f"Tagging {len(chosen)} segments")
    tags: list[Tag] = []
    if not cfg.groq_api_keys:
        notes.append("no GROQ_API_KEY configured, tagging skipped")
    elif chosen:
        try:
            tout = await tag.tag_segments(client, topics, chosen, cfg)
        except (httpx.HTTPError, LLMError) as exc:
            log.warning("job_id=%s stage=tag error=%s", run.job_id, exc)
            notes.append("tagging failed")
        else:
            tags = tout.tags
            run.tokens += tout.tokens
            if tout.failed_batches:
                notes.append(f"{tout.failed_batches} tag batches failed")
    _store_tags(conn, plan_id, tags)
    run.stage_done("tag")

    # optimizing
    run.update("optimizing", 85, "Choosing the smallest set of videos")
    budget = plan["budget_minutes"]
    res = cover.cover(topics, segments, tags, quality, level, budget, cfg.tag_confidence_min)
    items = build_items(res.ranges, videos, level)
    total = round(sum(r.end_s - r.start_s for r in res.ranges) / 60)
    run.stage_done("optimize")
    result = ResultOut(
        items=items,
        uncovered=res.uncovered,
        total_minutes=total,
        coverage=Coverage(covered=len(topics) - len(res.uncovered), total=len(topics)),
    )
    run.message = ("Done. " + "; ".join(notes)) if notes else "Done"
    return result


async def run_job(job_id: str, plan_id: str) -> None:
    """Background entry point. Only a bug marks the job `failed`."""
    conn = db.connect()
    run = _Run(conn, job_id)
    try:
        plan = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        run.update("queued", 0, "Starting")
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            result = await _pipeline(run, client, plan, get_settings())
        conn.execute(
            "INSERT OR REPLACE INTO results (plan_id, items_json, uncovered_json, total_minutes)"
            " VALUES (?,?,?,?)",
            (
                plan_id,
                json.dumps([i.model_dump() for i in result.items]),
                json.dumps([u.model_dump() for u in result.uncovered]),
                result.total_minutes,
            ),
        )
        run.update("done", 100)
        conn.execute("UPDATE jobs SET finished_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
        conn.commit()
    except Exception as exc:
        log.exception("job_id=%s run crashed", job_id)
        run.update("failed", None, f"Internal error: {type(exc).__name__}")
        conn.execute("UPDATE jobs SET finished_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
        conn.commit()
    finally:
        conn.close()
