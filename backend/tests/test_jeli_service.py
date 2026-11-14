import httpx
import pytest

import jeli_service


class _RaisingStreamCtx:
    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *args):
        return False


async def _collect(gen):
    return [chunk async for chunk in gen]


@pytest.mark.asyncio
async def test_stream_chat_ollama_unreachable_completes_gracefully(monkeypatch):
    """Ollama refusing the connection must not crash the widget — the stream
    should complete normally (no exception) with no content, letting the
    frontend show its 'AI offline' fallback instead of a hard connection reset."""

    def fake_stream(self, method, url, **kwargs):
        return _RaisingStreamCtx(httpx.ConnectError("Connection refused"))

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)

    chunks = await _collect(jeli_service.stream_chat("system prompt", "hello"))
    assert chunks == []


@pytest.mark.asyncio
async def test_stream_chat_ollama_http_error_completes_gracefully(monkeypatch):
    """A non-2xx from Ollama (e.g. model not pulled) must also degrade gracefully."""

    def fake_stream(self, method, url, **kwargs):
        exc = httpx.HTTPStatusError(
            "500", request=httpx.Request(method, url), response=httpx.Response(500)
        )
        return _RaisingStreamCtx(exc)

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)

    chunks = await _collect(jeli_service.stream_chat("system prompt", "hello"))
    assert chunks == []


@pytest.mark.asyncio
async def test_generate_narrative_returns_content(monkeypatch):
    async def fake_post(self, url, **kwargs):
        return httpx.Response(
            200, json={"message": {"content": "Summary text"}}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = await jeli_service.generate_narrative("system", "user msg")
    assert result == "Summary text"


@pytest.mark.asyncio
async def test_generate_narrative_raises_on_ollama_error(monkeypatch):
    async def fake_post(self, url, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(httpx.ConnectError):
        await jeli_service.generate_narrative("system", "user msg")


@pytest.mark.asyncio
async def test_generate_narrative_raises_on_empty_content(monkeypatch):
    async def fake_post(self, url, **kwargs):
        return httpx.Response(
            200, json={"message": {"content": ""}}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(ValueError):
        await jeli_service.generate_narrative("system", "user msg")


def _client_returning(status: int, body=None):
    def handler(req):
        return httpx.Response(status, json=body if body is not None else {})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _ctx(status: int, label: str, body=None):
    async with _client_returning(status, body) as c:
        return await jeli_service._fetch_context(c, "http://x/jeli/context", label, None)


@pytest.mark.asyncio
async def test_fetch_context_reports_stepup_not_offline():
    out = await _ctx(428, "Naforo")
    assert "identity verification" in out
    assert "offline" not in out


@pytest.mark.asyncio
async def test_fetch_context_reports_empty_data():
    assert "no data yet" in await _ctx(200, "Yiriba", {})


@pytest.mark.asyncio
async def test_fetch_context_offline():
    assert "offline" in await _ctx(503, "Naforo")


@pytest.mark.asyncio
async def test_stream_chat_sheds_load_when_ollama_slots_are_taken(monkeypatch):
    """Task 4.3: beyond the concurrency cap, a request waits briefly then is refused with
    JeliBusy (→ friendly 429) instead of piling onto a shared Ollama until it times out."""
    import asyncio

    from config import settings

    monkeypatch.setattr(settings, "ollama_max_concurrency", 1)
    monkeypatch.setattr(settings, "ollama_queue_wait_seconds", 0.05)
    jeli_service._reset_ollama_gate()
    release = asyncio.Event()

    class _SlowResp:
        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            await release.wait()
            yield '{"message": {"content": "hi"}, "done": true}'

    class _Ctx:
        async def __aenter__(self):
            return _SlowResp()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(httpx.AsyncClient, "stream", lambda self, m, u, **k: _Ctx())

    first = asyncio.create_task(_collect(jeli_service.stream_chat("s", "a")))
    await asyncio.sleep(0.01)  # first request now holds the only slot
    with pytest.raises(jeli_service.JeliBusy):
        await _collect(jeli_service.stream_chat("s", "b"))
    release.set()
    assert await first == ["hi"]
    # slot is released afterwards: a third request succeeds
    assert await _collect(jeli_service.stream_chat("s", "c")) == ["hi"]


@pytest.mark.asyncio
async def test_stream_chat_releases_slot_when_ollama_fails(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "ollama_max_concurrency", 1)
    monkeypatch.setattr(settings, "ollama_queue_wait_seconds", 0.05)
    jeli_service._reset_ollama_gate()
    monkeypatch.setattr(
        httpx.AsyncClient,
        "stream",
        lambda self, m, u, **k: _RaisingStreamCtx(httpx.ConnectError("down")),
    )
    for _ in range(3):  # would deadlock/JeliBusy on the 2nd call if the slot leaked
        assert await _collect(jeli_service.stream_chat("s", "x")) == []


def test_system_prompt_forbids_guaranteed_return_claims():
    """Task 4.1 matrix (ar6): the local model called bonds/CDs a 'guaranteed return'."""
    import jeli_service

    p = jeli_service.build_system_prompt("", "", "Ada", 5, 5, "en").lower()
    assert "guaranteed" in p and "risk-free" in p
