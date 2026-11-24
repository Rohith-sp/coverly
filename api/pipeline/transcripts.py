"""Transcript access and chapter parsing (PRD §6 F6, §10.2).

The only module that imports `youtube-transcript-api` (1.x instance API), so the source can be
swapped. Fallback: manual captions -> auto captions -> `source="none"`. Each fetch is bounded by
`FETCH_TIMEOUT_S`; a timed-out worker thread is abandoned (the library has no timeout option).
"""

import asyncio
import itertools
import logging
import re

import requests
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import CouldNotRetrieveTranscript, NoTranscriptFound

from schemas import Language, Transcript, TranscriptLine

log = logging.getLogger(__name__)

CONCURRENCY = 8
FETCH_TIMEOUT_S = 30.0

_CHAPTER = re.compile(
    r"^\s*(?:[-*•]\s*)?\(?((?:\d{1,2}:)?\d{1,2}:\d{2})\)?\s*[-\u2013\u2014:|]?\s*(\S.*?)\s*$"
)


def languages_for(language: Language) -> list[str]:
    return ["en", "hi"] if language == "both" else [language]


def _empty(video_id: str) -> Transcript:
    return Transcript(video_id=video_id, language=None, source="none", lines=[])


def _fetch_sync(video_id: str, languages: list[str]) -> Transcript:
    try:
        listing = YouTubeTranscriptApi().list(video_id)
        try:
            found = listing.find_manually_created_transcript(languages)
        except NoTranscriptFound:
            found = listing.find_generated_transcript(languages)
        fetched = found.fetch()
    except CouldNotRetrieveTranscript as exc:
        # Covers NoTranscriptFound, TranscriptsDisabled, VideoUnavailable, RequestBlocked, ...
        log.info("stage=transcript video_id=%s none: %s", video_id, type(exc).__name__)
        return _empty(video_id)
    except requests.RequestException as exc:
        log.warning("stage=transcript video_id=%s request error: %s", video_id, type(exc).__name__)
        return _empty(video_id)
    except Exception as exc:  # noqa: BLE001  # last resort: one video never fails the job
        log.error("stage=transcript video_id=%s unexpected: %s", video_id, type(exc).__name__)
        return _empty(video_id)
    lines = [
        TranscriptLine(start_s=s.start, duration_s=s.duration, text=s.text.strip())
        for s in fetched.snippets
        if s.text.strip()
    ]
    source = "auto" if fetched.is_generated else "manual"
    return Transcript(video_id=video_id, language=fetched.language_code, source=source, lines=lines)


async def fetch_transcript(
    video_id: str, languages: list[str], sem: asyncio.Semaphore
) -> Transcript:
    """Never raises for missing/blocked/slow transcripts; returns `source="none"` instead."""
    async with sem:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_fetch_sync, video_id, languages), FETCH_TIMEOUT_S
            )
        except TimeoutError:
            log.warning("stage=transcript video_id=%s timeout s=%.0f", video_id, FETCH_TIMEOUT_S)
            return _empty(video_id)


def _to_seconds(stamp: str) -> int:
    seconds = 0
    for part in stamp.split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


def parse_chapters(description: str, duration_s: int) -> list[tuple[int, int, str]]:
    """Chapters as (start_s, end_s, title). Needs >=2 entries, the first at 0, increasing starts."""
    starts: list[tuple[int, str]] = []
    for line in description.splitlines():
        m = _CHAPTER.match(line)
        if m:
            starts.append((_to_seconds(m.group(1)), m.group(2)))
    starts = [c for c in starts if c[0] < duration_s]
    if len(starts) < 2 or starts[0][0] != 0:
        return []
    if any(a[0] >= b[0] for a, b in itertools.pairwise(starts)):
        return []
    ends = [s for s, _ in starts[1:]] + [duration_s]
    return [(s, e, t) for (s, t), e in zip(starts, ends, strict=True)]
