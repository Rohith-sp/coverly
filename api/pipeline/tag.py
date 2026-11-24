"""Segment tagging against the closed plan vocabulary. PRD §6 F7, docs/CLAUDE.md §5.3, §5.8.

`select_segments` cuts the LLM workload (video cap + keyword pre-filter); `tag_segments` sends
batches to the small model and keeps only tags that reference plan topics and batch segments.
"""

import asyncio
import json
import logging
import re
from typing import NamedTuple

import httpx

import llm
from config import Settings, get_settings
from schemas import LLMTagBatch, Segment, Tag, Topic

log = logging.getLogger(__name__)

METADATA_CONFIDENCE_CAP = 0.5
MIN_KEYWORD_LEN = 3
STOP_WORDS = frozenset(
    "the and for with from into onto that this these those what when where which while how why "
    "are was were has have had not but its your you our their them they all any can will vs "
    "about over under using use basics intro introduction overview part module topic "
    "advanced".split()
)
_WORD = re.compile(r"[a-z0-9]+")

SYSTEM_PROMPT = """You tag transcript segments of educational videos with topics from a study plan.
Use ONLY topic ids from the provided topic list. Never invent topics or ids.
For each segment, list the topics it actually teaches, with:
- depth: 1 = mentions or introduces, 2 = explains with an example, 3 = deep / internals / edge cases
- confidence: 0 to 1, how sure you are the segment covers the topic at that depth
A segment may have no tags. Return JSON only, shaped as:
{"segments": [{"segment_id": "...",
  "tags": [{"topic_id": "...", "depth": 2, "confidence": 0.8}]}]}"""


class TagOutcome(NamedTuple):
    tags: list[Tag]
    tokens: int
    failed_batches: int


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _keywords(topics: list[Topic]) -> set[str]:
    words: set[str] = set()
    for t in topics:
        words |= _words(f"{t.module} {t.name}")
    return {w for w in words if len(w) >= MIN_KEYWORD_LEN and w not in STOP_WORDS}


def select_segments(
    topics: list[Topic],
    video_ids_by_topic: dict[str, list[str]],
    quality: dict[str, float],
    segments: list[Segment],
    per_topic: int,
) -> list[Segment]:
    """Video cap then keyword pre-filter. Pure, no I/O."""
    keep: set[str] = set()
    for ids in video_ids_by_topic.values():
        ranked = sorted(ids, key=lambda v: -quality.get(v, 0.0))  # stable: ties keep search order
        keep.update(ranked[:per_topic])
    keywords = _keywords(topics)
    return [s for s in segments if s.video_id in keep and _words(s.text) & keywords]


def _user_prompt(topics: list[Topic], batch: list[Segment], max_chars: int) -> str:
    topic_lines = "\n".join(f"{t.topic_id}: {t.module} / {t.name}" for t in topics)
    segs = [{"segment_id": s.id, "text": s.text[:max_chars]} for s in batch]
    return f"Topics:\n{topic_lines}\n\nSegments:\n{json.dumps(segs, ensure_ascii=False)}"


async def _tag_batch(
    client: httpx.AsyncClient,
    index: int,
    topics: list[Topic],
    batch: list[Segment],
    settings: Settings,
) -> tuple[list[Tag] | None, int]:
    user = _user_prompt(topics, batch, settings.segment_text_max_chars)
    try:
        result, tokens = await llm.complete_json(
            client, "small", SYSTEM_PROMPT, user, LLMTagBatch, settings
        )
    except llm.LLMValidationError as exc:
        log.warning("tag batch skipped batch=%d segments=%d: %s", index, len(batch), exc)
        return None, exc.tokens
    except llm.LLMError as exc:
        log.warning("tag batch skipped batch=%d segments=%d: %s", index, len(batch), exc)
        return None, 0
    topic_ids = {t.topic_id for t in topics}
    by_id = {s.id: s for s in batch}
    tags: list[Tag] = []
    for seg in result.segments:
        segment = by_id.get(seg.segment_id)
        if segment is None:
            continue
        for t in seg.tags:
            if t.topic_id not in topic_ids:
                continue
            conf = t.confidence
            if segment.source == "metadata":
                conf = min(conf, METADATA_CONFIDENCE_CAP)
            tags.append(
                Tag(segment_id=segment.id, topic_id=t.topic_id, depth=t.depth, confidence=conf)
            )
    return tags, tokens


async def tag_segments(
    client: httpx.AsyncClient,
    topics: list[Topic],
    segments: list[Segment],
    settings: Settings | None = None,
) -> TagOutcome:
    """Tag segments in concurrent batches. A failed batch is logged and skipped, never raised."""
    settings = settings or get_settings()
    size = max(1, settings.tag_batch_size)
    batches = [segments[i : i + size] for i in range(0, len(segments), size)]
    results = await asyncio.gather(
        *(_tag_batch(client, i, topics, b, settings) for i, b in enumerate(batches))
    )
    best: dict[tuple[str, str], Tag] = {}
    tokens = failed = 0
    for tags, used in results:
        tokens += used
        if tags is None:
            failed += 1
            continue
        for tag in tags:
            key = (tag.segment_id, tag.topic_id)
            if key not in best or tag.confidence > best[key].confidence:
                best[key] = tag
    log.info(
        "tag segments=%d batches=%d failed=%d tags=%d tokens=%d",
        len(segments),
        len(batches),
        failed,
        len(best),
        tokens,
    )
    return TagOutcome(list(best.values()), tokens, failed)
