"""YouTube Data API v3 search + metadata (PRD §6 F5-F6, §10.1). No DB, no caching here.

The caller passes an `httpx.AsyncClient` and persists/caches what is returned.
The API key goes in the `X-Goog-Api-Key` header (never the URL, which httpx logs).
`search_topic` makes exactly one `search.list` attempt (no retry, docs/CLAUDE.md §5.6);
`videos.list` batches retry on 429/5xx/transport errors. `search_topic` / `_fetch_videos_batch`
raise `QuotaExceededError` or `httpx.HTTPError`; the batch helpers `search_topics` /
`fetch_videos` catch both per topic/batch and return partial results (with `quota_exceeded=True`
on quota), so the job runner can finish with reduced coverage (docs/CLAUDE.md §5.5).
"""

import asyncio
import logging
import random
import re
from dataclasses import dataclass
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

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")

# JSON from the API is untyped.
JSON = dict[str, Any]


class QuotaExceededError(Exception):
    """YouTube returned 403 quotaExceeded (or rateLimitExceeded); stop calling the API."""


@dataclass
class _Meter:
    """Quota units spent by requests actually sent."""

    units: int = 0


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


def _valid_id(video_id: object) -> bool:
    return isinstance(video_id, str) and bool(_VIDEO_ID.match(video_id))


def _describe(exc: httpx.HTTPError) -> str:
    """Error type and status only: the exception text may contain the request URL."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{type(exc).__name__} status={exc.response.status_code}"
    return type(exc).__name__


async def _get(
    client: httpx.AsyncClient,
    path: str,
    params: dict[str, str | int],
    api_key: str,
    *,
    tries: int,
    cost: int,
    meter: _Meter,
) -> JSON:
    """GET, retrying 429/5xx/transport errors up to `tries` (jittered backoff).

    Adds `cost` to `meter` for every attempt that reached the server.
    """
    url = f"{API_BASE}/{path}"
    headers = {"X-Goog-Api-Key": api_key}
    for attempt in range(1, tries + 1):
        try:
            resp = await client.get(url, params=params, headers=headers, timeout=TIMEOUT)
        except httpx.TransportError as exc:
            if not isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
                meter.units += cost  # may have been processed server-side
            if attempt == tries:
                raise
            log.warning("youtube %s transport error try=%d: %s", path, attempt, type(exc).__name__)
        else:
            meter.units += cost
            if _is_quota_error(resp):
                raise QuotaExceededError(path)
            if resp.status_code != 429 and resp.status_code < 500:
                resp.raise_for_status()
                body: JSON = resp.json()
                return body
            if attempt == tries:
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
    meter: _Meter | None = None,
) -> list[str]:
    """Exactly one `search.list` attempt (100 units) for one topic; returns valid video ids.

    No caption filter, no retry. `relevanceLanguage` is omitted for "both".
    """
    params: dict[str, str | int] = {
        "part": "snippet",
        "type": "video",
        "maxResults": max_results,
        "q": build_query(topic, language),
    }
    if language != "both":
        params["relevanceLanguage"] = language
    data = await _get(
        client, "search", params, api_key, tries=1, cost=SEARCH_COST, meter=meter or _Meter()
    )
    ids = [i.get("id", {}).get("videoId") for i in data.get("items", [])]
    return [v for v in ids if _valid_id(v)]


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
    meter = _Meter()
    exhausted = False

    async def one(topic: Topic) -> None:
        nonlocal exhausted
        async with sem:
            if exhausted:
                return
            try:
                ids = await search_topic(client, api_key, topic, language, max_results, meter)
            except QuotaExceededError:
                exhausted = True
                log.error("stage=search topic=%s quota exceeded", topic.topic_id)
                return
            except httpx.HTTPError as exc:
                log.error("stage=search topic=%s failed: %s", topic.topic_id, _describe(exc))
                return
            results[topic.topic_id] = ids

    await asyncio.gather(*(one(t) for t in topics))
    log.info("stage=search topics=%d done=%d units=%d", len(topics), len(results), meter.units)
    return SearchOutcome(results, meter.units, exhausted)


def _to_video(item: JSON) -> Video | None:
    snippet: JSON = item.get("snippet", {})
    stats: JSON = item.get("statistics", {})
    if not _valid_id(item.get("id")):
        return None
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


async def _fetch_videos_batch(
    client: httpx.AsyncClient, api_key: str, ids: list[str], meter: _Meter
) -> list[Video]:
    """One `videos.list` call (1 unit) for up to 50 ids. Unknown/deleted ids are omitted."""
    params: dict[str, str | int] = {
        "part": "snippet,contentDetails,statistics",
        "id": ",".join(ids),
        "maxResults": VIDEOS_BATCH,
    }
    data = await _get(
        client, "videos", params, api_key, tries=MAX_TRIES, cost=VIDEOS_COST, meter=meter
    )
    return [v for v in (_to_video(i) for i in data.get("items", [])) if v is not None]


async def fetch_videos(
    client: httpx.AsyncClient, api_key: str, video_ids: list[str]
) -> VideosOutcome:
    """Metadata for valid ids (deduped, order kept), batched by 50, concurrency 4."""
    unique = [v for v in dict.fromkeys(video_ids) if _valid_id(v)]
    chunks = [unique[i : i + VIDEOS_BATCH] for i in range(0, len(unique), VIDEOS_BATCH)]
    sem = asyncio.Semaphore(CONCURRENCY)
    out: dict[int, list[Video]] = {}
    meter = _Meter()
    exhausted = False

    async def one(idx: int, chunk: list[str]) -> None:
        nonlocal exhausted
        async with sem:
            if exhausted:
                return
            try:
                out[idx] = await _fetch_videos_batch(client, api_key, chunk, meter)
            except QuotaExceededError:
                exhausted = True
                log.error("stage=metadata batch=%d quota exceeded", idx)
            except httpx.HTTPError as exc:
                log.error("stage=metadata batch=%d failed: %s", idx, _describe(exc))

    await asyncio.gather(*(one(i, c) for i, c in enumerate(chunks)))
    videos = [v for i in sorted(out) for v in out[i]]
    log.info("stage=metadata ids=%d videos=%d units=%d", len(unique), len(videos), meter.units)
    return VideosOutcome(videos, meter.units, exhausted)
