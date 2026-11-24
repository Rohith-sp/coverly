import json
from collections.abc import Callable, Iterator

import httpx
import pytest

import llm
from config import get_settings
from pipeline.tag import TagOutcome, select_segments, tag_segments
from schemas import Segment, SegmentSource, Topic

Responder = Callable[[list[dict[str, str]]], dict[str, object] | None]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def llm_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GROQ_API_KEY_1", "key-a")
    monkeypatch.setenv("LLM_MODEL_SMALL", "model-small")
    monkeypatch.setenv("TAG_BATCH_SIZE", "2")
    monkeypatch.setenv("SEGMENT_TEXT_MAX_CHARS", "20")
    get_settings.cache_clear()
    monkeypatch.setattr(llm, "_bucket", llm.TokenBucket(6000, 100))
    yield
    get_settings.cache_clear()


TOPICS = [
    Topic(topic_id="jwt-1", module="Auth", name="JSON web tokens", position=0, required_depth=2),
    Topic(topic_id="idx-2", module="Databases", name="Indexes", position=1, required_depth=2),
]


def seg(sid: str, text: str = "jwt tokens", source: SegmentSource = "window") -> Segment:
    return Segment(
        id=sid, video_id=sid.split("_")[0], start_s=0, end_s=60, text=text, source=source
    )


def tag(sid: str, topic: str = "jwt-1", conf: float = 0.9) -> dict[str, object]:
    return {"segment_id": sid, "tags": [{"topic_id": topic, "depth": 2, "confidence": conf}]}


async def run(segments: list[Segment], respond: Responder) -> tuple[TagOutcome, list[list[str]]]:
    """`respond` gets the prompt's segments and returns the JSON body (None: invalid)."""
    seen: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "model-small"
        user = body["messages"][1]["content"]
        assert "jwt-1: Auth / JSON web tokens" in user
        segs = json.loads(user.split("Segments:\n", 1)[1])
        seen.append([s["text"] for s in segs])
        out = respond(segs)
        content = "not json" if out is None else json.dumps(out)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 10}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        outcome = await tag_segments(client, TOPICS, segments)
    return outcome, seen


def tag_all(segs: list[dict[str, str]]) -> dict[str, object]:
    return {"segments": [tag(s["segment_id"]) for s in segs]}


def test_video_cap_ranks_by_quality_ties_keep_search_order() -> None:
    segments = [seg(f"{v}_00") for v in "abcd"]
    out = select_segments(
        TOPICS,
        {"jwt-1": ["a", "b", "c"], "idx-2": ["d", "a"]},
        {"a": 0.5, "b": 0.9, "c": 0.5, "d": 0.7},
        segments,
        per_topic=2,
    )
    # jwt-1 keeps b (0.9) then a (tie with c, a first in search order); idx-2 keeps d and a.
    assert [s.id for s in out] == ["a_00", "b_00", "d_00"]


def test_keyword_prefilter_keeps_word_matches_only() -> None:
    segments = [
        seg("a_00", "Building an INDEXES primer"),  # topic name word, case-insensitive
        seg("a_01", "auth flows explained"),  # module name word
        seg("a_02", "web of trust and the stuff"),  # "web" is a keyword
        seg("a_03", "reindexes everything and the"),  # substring only, stop words only
        seg("a_04", "nothing relevant here"),
    ]
    out = select_segments(TOPICS, {"jwt-1": ["a"]}, {"a": 1.0}, segments, per_topic=5)
    assert [s.id for s in out] == ["a_00", "a_01", "a_02"]


@pytest.mark.anyio
async def test_batches_truncation_and_inputs_untouched() -> None:
    long = "x" * 50
    segments = [seg(f"v_{i:02d}", long) for i in range(5)]
    outcome, seen = await run(segments, tag_all)
    assert len(seen) == 3  # batch size 2
    assert all(len(t) == 20 for batch in seen for t in batch)
    assert segments[0].text == long
    assert len(outcome.tags) == 5 and outcome.tokens == 30 and outcome.failed_batches == 0


@pytest.mark.anyio
async def test_unknown_topic_and_segment_dropped() -> None:
    def respond(segs: list[dict[str, str]]) -> dict[str, object]:
        return {"segments": [tag("v_00"), tag("v_00", "made-up"), tag("other_09")]}

    outcome, _ = await run([seg("v_00")], respond)
    assert [(t.segment_id, t.topic_id) for t in outcome.tags] == [("v_00", "jwt-1")]


@pytest.mark.anyio
async def test_metadata_confidence_capped() -> None:
    outcome, _ = await run([seg("v_00", source="metadata"), seg("w_00")], tag_all)
    conf = {t.segment_id: t.confidence for t in outcome.tags}
    assert conf == {"v_00": 0.5, "w_00": 0.9}


@pytest.mark.anyio
async def test_dedupe_keeps_max_confidence() -> None:
    def respond(segs: list[dict[str, str]]) -> dict[str, object]:
        return {"segments": [tag("v_00", conf=0.6), tag("v_00", conf=0.8), tag("v_00", conf=0.7)]}

    outcome, _ = await run([seg("v_00")], respond)
    assert len(outcome.tags) == 1 and outcome.tags[0].confidence == 0.8


@pytest.mark.anyio
async def test_failed_batch_counted_others_returned() -> None:
    def respond(segs: list[dict[str, str]]) -> dict[str, object] | None:
        return None if segs[0]["segment_id"] == "bad_00" else tag_all(segs)

    segments = [seg("ok_00"), seg("ok_01"), seg("bad_00"), seg("bad_01")]
    outcome, seen = await run(segments, respond)
    assert outcome.failed_batches == 1
    assert {t.segment_id for t in outcome.tags} == {"ok_00", "ok_01"}
    assert len(seen) == 3  # good batch once, bad batch retried once by llm.complete_json
    assert outcome.tokens == 30  # tokens of the failed attempts still count


@pytest.mark.anyio
async def test_empty_input_makes_no_calls() -> None:
    outcome, seen = await run([], tag_all)
    assert outcome == TagOutcome([], 0, 0) and seen == []
