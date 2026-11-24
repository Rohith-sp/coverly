import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from pipeline import search
from pipeline.search import QuotaExceededError
from schemas import Topic

FIX = Path(__file__).parent / "fixtures"


def _topic(i: int = 0) -> Topic:
    return Topic(topic_id=f"t{i}", module="Auth", name=f"JWT {i}", position=i, required_depth=2)


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search, "BACKOFF_BASE_S", 0.0)


def test_build_query() -> None:
    t = _topic()
    assert search.build_query(t, "en") == "JWT 0 Auth tutorial"
    assert search.build_query(t, "both") == "JWT 0 Auth tutorial"
    assert search.build_query(t, "hi") == "JWT 0 Auth tutorial hindi"


@pytest.mark.parametrize(
    ("iso", "secs"),
    [
        ("PT1H2M3S", 3723),
        ("P1DT2H", 93600),
        ("PT45S", 45),
        ("PT10M", 600),
        ("PT2H", 7200),
        ("P0D", 0),
        ("garbage", 0),
    ],
)
def test_parse_duration(iso: str, secs: int) -> None:
    assert search.parse_duration(iso) == secs


@pytest.mark.anyio
async def test_one_search_per_topic_and_quota() -> None:
    seen: list[httpx.Request] = []
    page = json.loads((FIX / "youtube_search_page.json").read_text())

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=page)

    async with _client(handler) as c:
        out = await search.search_topics(c, "K", [_topic(0), _topic(1), _topic(2)], "hi", 10)
    assert len(seen) == 3
    assert out.quota_units == 300
    assert not out.quota_exceeded
    assert out.video_ids["t1"] == ["aaa", "bbb"]
    p = seen[0].url.params
    assert p["type"] == "video"
    assert p["maxResults"] == "10"
    assert p["relevanceLanguage"] == "hi"
    assert "videoCaption" not in p


@pytest.mark.anyio
async def test_both_language_omits_relevance_language() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"items": []})

    async with _client(handler) as c:
        await search.search_topic(c, "K", _topic(), "both", 5)
    assert "relevanceLanguage" not in seen[0].url.params


@pytest.mark.anyio
async def test_videos_batched_by_50() -> None:
    calls: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        ids = req.url.params["id"].split(",")
        calls.append(len(ids))
        return httpx.Response(200, json={"items": []})

    async with _client(handler) as c:
        out = await search.fetch_videos(c, "K", [f"v{i}" for i in range(120)] + ["v0"])
    assert sorted(calls) == [20, 50, 50]
    assert out.quota_units == 3


@pytest.mark.anyio
async def test_video_mapping_and_missing_stats() -> None:
    page = json.loads((FIX / "youtube_videos_page.json").read_text())

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    async with _client(handler) as c:
        out = await search.fetch_videos(c, "K", ["vid_full", "vid_nostats"])
    full, bare = out.videos
    assert (full.duration_s, full.views, full.likes) == (3723, 1500, 90)
    assert full.channel_title == "Chan One"
    assert (bare.duration_s, bare.views, bare.likes) == (45, 0, 0)


@pytest.mark.anyio
async def test_retry_on_429_then_success() -> None:
    n = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        if n < 3:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"items": []})

    async with _client(handler) as c:
        assert await search.search_topic(c, "K", _topic(), "en", 10) == []
    assert n == 3


@pytest.mark.anyio
async def test_gives_up_after_three_tries() -> None:
    n = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        return httpx.Response(503)

    async with _client(handler) as c:
        with pytest.raises(httpx.HTTPStatusError):
            await search.search_topic(c, "K", _topic(), "en", 10)
    assert n == 3


@pytest.mark.anyio
async def test_timeout_retried() -> None:
    n = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        if n == 1:
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(200, json={"items": []})

    async with _client(handler) as c:
        await search.search_topic(c, "K", _topic(), "en", 10)
    assert n == 2


def _quota(_: httpx.Request) -> httpx.Response:
    body = {"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}}
    return httpx.Response(403, json=body)


@pytest.mark.anyio
async def test_quota_exceeded_raises_single_call() -> None:
    async with _client(_quota) as c:
        with pytest.raises(QuotaExceededError):
            await search.search_topic(c, "K", _topic(), "en", 10)


@pytest.mark.anyio
async def test_quota_exceeded_partial_outcome() -> None:
    async with _client(_quota) as c:
        s = await search.search_topics(c, "K", [_topic(0), _topic(1)], "en", 10)
        v = await search.fetch_videos(c, "K", ["a"])
    assert s.quota_exceeded and s.quota_units == 0 and s.video_ids == {}
    assert v.quota_exceeded and v.videos == []


@pytest.mark.anyio
async def test_other_403_is_not_quota() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"errors": [{"reason": "forbidden"}]}})

    async with _client(handler) as c:
        with pytest.raises(httpx.HTTPStatusError):
            await search.search_topic(c, "K", _topic(), "en", 10)
