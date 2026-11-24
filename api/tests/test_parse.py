import json
from collections.abc import Iterator, Mapping

import httpx
import pytest

import llm
from config import get_settings
from pipeline.parse import TooManyTopicsError, parse_plan, topic_id


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def llm_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GROQ_API_KEY_1", "key-a")
    monkeypatch.setenv("LLM_MODEL_LARGE", "model-large")
    monkeypatch.setenv("LLM_MODEL_SMALL", "model-small")
    monkeypatch.setenv("MAX_TOPICS", "40")
    get_settings.cache_clear()
    monkeypatch.setattr(llm, "_bucket", llm.TokenBucket(6000, 100))
    yield
    get_settings.cache_clear()


async def run(plan: Mapping[str, object], tokens: int = 100) -> tuple[str, list[str], int]:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["model"] == "model-large"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(plan)}}],
                "usage": {"total_tokens": tokens},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        title, modules, used = await parse_plan(client, "syllabus text")
    ids = [t.topic_id for m in modules for t in m.topics]
    return title, ids, used


def test_topic_id_stable_and_shaped() -> None:
    a = topic_id("Auth", "Sessions vs JWT")
    assert a == topic_id("Auth", "Sessions vs JWT")
    assert a.startswith("sessions-vs-jwt-") and len(a.rsplit("-", 1)[1]) == 4
    assert a != topic_id("Security", "Sessions vs JWT")
    assert topic_id("M", "!!!").startswith("topic-")


@pytest.mark.anyio
async def test_parse_plan_happy_path() -> None:
    plan = {
        "title": "Backend Engineer",
        "modules": [
            {"name": "Auth", "topics": ["password hashing", "sessions vs JWT"]},
            {"name": "DB", "topics": ["indexes"]},
        ],
    }
    title, ids, tokens = await run(plan, 123)
    assert title == "Backend Engineer" and tokens == 123
    assert ids == [
        topic_id("Auth", "password hashing"),
        topic_id("Auth", "sessions vs JWT"),
        topic_id("DB", "indexes"),
    ]


@pytest.mark.anyio
async def test_duplicates_dropped() -> None:
    plan = {
        "title": "T",
        "modules": [
            {"name": "A", "topics": ["RBAC", "rbac", "OAuth"]},
            {"name": "A", "topics": ["RBAC"]},  # same module+name -> same id, dropped
            {"name": "B", "topics": ["RBAC"]},  # other module -> distinct id, kept
        ],
    }
    _, ids, _ = await run(plan)
    assert ids == [topic_id("A", "RBAC"), topic_id("A", "OAuth"), topic_id("B", "RBAC")]
    assert len(set(ids)) == len(ids)


@pytest.mark.anyio
async def test_too_many_topics() -> None:
    plan = {"title": "Big", "modules": [{"name": "M", "topics": [f"t{i}" for i in range(41)]}]}
    with pytest.raises(TooManyTopicsError) as err:
        await run(plan)
    assert err.value.count == 41 and err.value.limit == 40


@pytest.mark.anyio
async def test_exactly_40_topics_ok() -> None:
    plan = {"title": "Big", "modules": [{"name": "M", "topics": [f"t{i}" for i in range(40)]}]}
    _, ids, _ = await run(plan)
    assert len(ids) == 40
