from fastapi.testclient import TestClient

from auth.dependencies import get_current_user
from main import app


async def _fake_gather_context(*a, **k):
    return (
        "Pazuru is currently offline — no data available.",
        "Tsumi is currently offline — no data available.",
    )


async def _empty_stream_chat(system_prompt, message):
    return
    yield  # pragma: no cover — makes this an async generator


def test_chat_degrades_gracefully_when_ollama_down(monkeypatch):
    import main

    monkeypatch.setattr(main, "gather_context", _fake_gather_context)
    monkeypatch.setattr(main, "stream_chat", _empty_stream_chat)

    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "email": "u1@test.local"}
    try:
        client = TestClient(app)
        response = client.post(
            "/chat",
            json={"message": "hi", "user_name": "Test", "personality": {"tone": 5, "expertise": 5}},
        )
        assert response.status_code == 200
        assert response.text == ""
    finally:
        app.dependency_overrides.clear()


def test_chat_returns_429_daily_limit_when_free_cap_is_spent(monkeypatch):
    import ai_router
    import main

    monkeypatch.setattr(main, "gather_context", _fake_gather_context)

    async def _capped(*a, **k):
        raise ai_router.DailyLimitReached
        yield  # pragma: no cover

    monkeypatch.setattr(main.ai_router, "stream", _capped)
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "email": "u1@test.local"}
    try:
        response = TestClient(app).post(
            "/chat",
            json={"message": "hi", "user_name": "Test", "personality": {"tone": 5, "expertise": 5}},
        )
        assert response.status_code == 429
        assert response.json()["detail"]["code"] == "daily_limit"
        assert response.headers["X-AI-Notice"] == "daily_limit"
    finally:
        app.dependency_overrides.clear()
