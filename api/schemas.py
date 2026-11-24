"""Shared contracts: API bodies, LLM JSON, and pipeline domain models.

Only the main session edits this file. Web types in `web/lib/api.ts` mirror the API section.
"""

import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_INPUT_CHARS = 20_000
MAX_TOPICS = 40  # hard v0 cap (PRD §6 F3); config.max_topics may lower it, never raise it

Level = Literal["beginner", "interview", "deep"]
Language = Literal["en", "hi", "both"]
JobStatus = Literal["queued", "searching", "fetching", "tagging", "optimizing", "done", "failed"]
TranscriptSource = Literal["manual", "auto", "none"]
SegmentSource = Literal["chapter", "window", "metadata"]
UncoveredReason = Literal["no candidates found", "below required depth", "over budget"]
Depth = Literal[1, 2, 3]

REQUIRED_DEPTH: dict[Level, Depth] = {"beginner": 1, "interview": 2, "deep": 3}


def _no_control_chars(v: str) -> str:
    if any(unicodedata.category(c) == "Cc" for c in v):
        raise ValueError("control characters are not allowed")
    return v


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------- API


class TopicOut(_Strict):
    topic_id: str
    name: str


class ModuleOut(_Strict):
    name: str
    topics: list[TopicOut]


class PlanCreate(_Strict):
    """POST /plans"""

    text: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)
    level: Level
    language: Language
    budget_hours: float | None = Field(default=None, gt=0, le=1000)


class PlanOut(_Strict):
    """Response of POST /plans and GET /plans/{id}."""

    plan_id: str
    title: str
    level: Level
    language: Language
    budget_hours: float | None
    modules: list[ModuleOut]


class TopicIn(_Strict):
    """`topic_id` is None for a topic the user added in the review step."""

    topic_id: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,64}$")
    name: str = Field(min_length=1, max_length=200)

    _single_line = field_validator("name")(_no_control_chars)


class ModuleIn(_Strict):
    name: str = Field(min_length=1, max_length=200)
    topics: list[TopicIn] = Field(min_length=1)

    _single_line = field_validator("name")(_no_control_chars)


class TopicsUpdate(_Strict):
    """PUT /plans/{id}/topics. Replaces the whole topic tree."""

    modules: list[ModuleIn] = Field(min_length=1)

    @model_validator(mode="after")
    def _cap_topics(self) -> "TopicsUpdate":
        total = sum(len(m.topics) for m in self.modules)
        if total > MAX_TOPICS:
            raise ValueError(f"{total} topics; the limit is {MAX_TOPICS}. Trim or split the plan.")
        return self


class Ok(_Strict):
    ok: bool = True


class RunOut(_Strict):
    """POST /plans/{id}/run"""

    job_id: str


class JobOut(_Strict):
    """GET /jobs/{id}. `progress` is 0-100."""

    job_id: str
    plan_id: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    message: str
    quota_units: int
    llm_tokens: int


class TopicRef(_Strict):
    topic_id: str
    name: str
    depth: Depth


class ResultItem(_Strict):
    video_id: str
    title: str
    channel: str
    thumbnail: str
    start_s: int
    end_s: int
    url: str
    topics: list[TopicRef]
    reason: str


class UncoveredTopic(_Strict):
    topic_id: str
    name: str
    module: str
    reason: UncoveredReason


class Coverage(_Strict):
    covered: int
    total: int


class ResultOut(_Strict):
    """GET /plans/{id}/result"""

    items: list[ResultItem]
    uncovered: list[UncoveredTopic]
    total_minutes: int
    coverage: Coverage


class HealthOut(_Strict):
    ok: bool
    quota_used_today: int


# --------------------------------------------------------------------------- LLM JSON
# Validated output of the LLM. Extra keys are ignored (models add chatter); types are not.


class _LLM(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class ParsedModule(_LLM):
    name: str = Field(min_length=1, max_length=200)
    topics: list[str] = Field(min_length=1)

    @field_validator("topics")
    @classmethod
    def _non_empty(cls, v: list[str]) -> list[str]:
        cleaned = [t.strip() for t in v if t.strip()]
        if any(len(t) > 200 for t in cleaned):
            raise ValueError("topic too long")
        if not cleaned:
            raise ValueError("module has no topics")
        return cleaned


class ParsedPlan(_LLM):
    """Plan parser output (PRD §6 F3)."""

    title: str = Field(min_length=1, max_length=200)
    modules: list[ParsedModule] = Field(min_length=1)


class LLMTag(_LLM):
    topic_id: str
    depth: Depth
    confidence: float = Field(ge=0, le=1)


class LLMSegmentTags(_LLM):
    segment_id: str
    tags: list[LLMTag]


class LLMTagBatch(_LLM):
    """Tagger output for one batch. JSON mode needs an object at the top level."""

    segments: list[LLMSegmentTags]


# --------------------------------------------------------------------------- Pipeline domain
# Plain data passed between stages. Stages never touch the DB (docs/CLAUDE.md §5.1).


class Topic(_Strict):
    topic_id: str
    module: str
    name: str
    position: int  # 0-based syllabus order across the whole plan
    required_depth: Depth


class Video(_Strict):
    id: str
    title: str
    channel_id: str
    channel_title: str
    duration_s: int
    views: int
    likes: int
    published_at: str | None  # ISO-8601 from the API
    description: str
    has_transcript: bool = False


class TranscriptLine(_Strict):
    start_s: float
    duration_s: float
    text: str


class Transcript(_Strict):
    video_id: str
    language: str | None
    source: TranscriptSource
    lines: list[TranscriptLine]


class Segment(_Strict):
    """`id` is `{video_id}_{index:02d}`."""

    id: str
    video_id: str
    start_s: int
    end_s: int
    text: str
    source: SegmentSource


class Tag(_Strict):
    segment_id: str
    topic_id: str
    depth: Depth
    confidence: float = Field(ge=0, le=1)


class PlannedRange(_Strict):
    """One output range of the optimizer, before titles and reasons are attached."""

    video_id: str
    start_s: int
    end_s: int
    topics: list[TopicRef]


class CoverResult(_Strict):
    ranges: list[PlannedRange]  # pruned, merged, in syllabus order
    uncovered: list[UncoveredTopic]
