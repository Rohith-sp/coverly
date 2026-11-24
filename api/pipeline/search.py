"""YouTube Data API v3 search + metadata (PRD §6 F5-F6, §10.1). No DB, no caching here.

The caller passes an `httpx.AsyncClient` and persists/caches what is returned.
Quota errors: `search_topic` / `fetch_videos_batch` raise `QuotaExceededError`; the batch helpers
`search_topics` / `fetch_videos` catch it and return partial results with `quota_exceeded=True`,
so the job runner can finish with reduced coverage (docs/CLAUDE.md §5.5).
"""

import asyncio
import logging
import random
import re
from typing import Any, NamedTuple

import httpx

from schemas import Language, Topic, Video

log = logging.getLogger(__name__)

API_BASE = "https://www.googleapis.com/youtube/v3"
SEARCH_COST = 100
VIDEOS_COST = 1
VIDEOS_BATCH = 50
CONCURRENCY = 4
MAX_TRIES = 3
BACKOFF_BASE_S = 1.0  # tests set this to 0
TIMEOUT = httpx.Timeout(20.0, connect=5.0)

_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")

# JSON from the API is untyped.
JSON = dict[str, Any]


class QuotaExceededError(Exception):
    """YouTube returned 403 quotaExceeded (or rateLimitExceeded); stop calling the API."""


class SearchOutcome(NamedTuple):
    video_ids: dict[str, list[str]]  # topic_id -> ids in relevance order
    quota_units: int
    quota_exceeded: bool


class VideosOutcome(NamedTuple):
    videos: list[Video]
    quota_units: int
    quota_exceeded: bool


def build_query(topic: Topic, language: Language) -> str:
    query = f"{topic.name} {topic.module} tutorial"
    return f"{query} hindi" if language == "hi" else query


def parse_duration(iso: str) -> int:
    """ISO-8601 duration (PT1H2M3S, P1DT2H, PT45S) to seconds; unparseable -> 0."""
    m = _DURATION.match(iso)
    if not m:
        return 0
    d, h, mi, s = (int(g) if g else 0 for g in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _is_quota_error(resp: httpx.Response) -> bool:
    if resp.status_code != 403:
        return False
    try:
        errors = resp.json().get("error", {}).get("errors", [])
    except ValueError:
        return False
    return any(e.get("reason") in ("quotaExceeded", "rateLimitExceeded") for e in errors)


async def _get(client: httpx.AsyncClient, path: str, params: dict[str, str | int]) -> JSON:
    """GET with retry on 429/5xx/timeouts/transport errors (max 3 tries, jittered backoff)."""
    url = f"{API_BASE}/{path}"
    for attempt in range(1, MAX_TRIES + 1):
        try:
            resp = await client.get(url, params=params, timeout=TIMEOUT)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt == MAX_TRIES:
                raise
            log.warning("youtube %s transport error try=%d: %s", path, attempt, exc)
        else:
            if _is_quota_error(resp):
                raise QuotaExceededError(path)
            if resp.status_code != 429 and resp.status_code < 500:
                resp.raise_for_status()
                body: JSON = resp.json()
                return body
            if attempt == MAX_TRIES:
                resp.raise_for_status()
            log.warning("youtube %s status=%d try=%d", path, resp.status_code, attempt)
        await asyncio.sleep(BACKOFF_BASE_S * 2 ** (attempt - 1) * (0.5 + random.random()))  # noqa: S311
    raise AssertionError("unreachable")  # pragma: no cover


async def search_topic(
    client: httpx.AsyncClient,
    api_key: str,
    topic: Topic,
    language: Language,
    max_results: int,
) -> list[str]:
    """One `search.list` (100 units) for one topic; returns video ids.

    No caption filter, no retry search. `relevanceLanguage` is omitted for "both".
    """
    params: dict[str, str | int] = {
        "part": "snippet",
        "type": "video",
        "maxResults": max_results,
        "q": build_query(topic, language),
        "key": api_key,
    }
    if language != "both":
        params["relevanceLanguage"] = language
    data = await _get(client, "search", params)
    return [i["id"]["videoId"] for i in data.get("items", []) if i.get("id", {}).get("videoId")]


async def search_topics(
    client: httpx.AsyncClient,
    api_key: str,
    topics: list[Topic],
    language: Language,
    max_results: int,
) -> SearchOutcome:
    """Search every topic (concurrency 4). Stops spending quota once it is exhausted."""
    sem = asyncio.Semaphore(CONCURRENCY)
    results: dict[str, list[str]] = {}
    units = 0
    exhausted = False

    async def one(topic: Topic) -> None:
        nonlocal units, exhausted
        async with sem:
            if exhausted:
                return
            try:
                ids = await search_topic(client, api_key, topic, language, max_results)
            except QuotaExceededError:
                exhausted = True
                log.error("stage=search topic=%s quota exceeded", topic.topic_id)
                return
            units += SEARCH_COST
            results[topic.topic_id] = ids

    await asyncio.gather(*(one(t) for t in topics))
    log.info("stage=search topics=%d done=%d units=%d", len(topics), len(results), units)
    return SearchOutcome(results, units, exhausted)


def _to_video(item: JSON) -> Video:
    snippet: JSON = item.get("snippet", {})
    stats: JSON = item.get("statistics", {})
    return Video(
        id=item["id"],
        title=snippet.get("title", ""),
        channel_id=snippet.get("channelId", ""),
        channel_title=snippet.get("channelTitle", ""),
        duration_s=parse_duration(item.get("contentDetails", {}).get("duration", "")),
        views=int(stats.get("viewCount") or 0),
        likes=int(stats.get("likeCount") or 0),
        published_at=snippet.get("publishedAt"),
        description=snippet.get("description", ""),
    )


async def fetch_videos_batch(
    client: httpx.AsyncClient, api_key: str, ids: list[str]
) -> list[Video]:
    """One `videos.list` call (1 unit) for up to 50 ids. Unknown/deleted ids are omitted."""
    params: dict[str, str | int] = {
        "part": "snippet,contentDetails,statistics",
        "id": ",".join(ids),
        "maxResults": VIDEOS_BATCH,
        "key": api_key,
    }
    data = await _get(client, "videos", params)
    return [_to_video(i) for i in data.get("items", [])]


async def fetch_videos(
    client: httpx.AsyncClient, api_key: str, video_ids: list[str]
) -> VideosOutcome:
    """Metadata for ids (deduped, order kept), batched by 50, concurrency 4."""
    unique = list(dict.fromkeys(video_ids))
    chunks = [unique[i : i + VIDEOS_BATCH] for i in range(0, len(unique), VIDEOS_BATCH)]
    sem = asyncio.Semaphore(CONCURRENCY)
    out: dict[int, list[Video]] = {}
    units = 0
    exhausted = False

    async def one(idx: int, chunk: list[str]) -> None:
        nonlocal units, exhausted
        async with sem:
            if exhausted:
                return
            try:
                out[idx] = await fetch_videos_batch(client, api_key, chunk)
            except QuotaExceededError:
                exhausted = True
                log.error("stage=metadata batch=%d quota exceeded", idx)
                return
            units += VIDEOS_COST

    await asyncio.gather(*(one(i, c) for i, c in enumerate(chunks)))
    videos = [v for i in sorted(out) for v in out[i]]
    log.info("stage=metadata ids=%d videos=%d units=%d", len(unique), len(videos), units)
    return VideosOutcome(videos, units, exhausted)
