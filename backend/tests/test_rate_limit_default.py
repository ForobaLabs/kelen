"""AW-1 / H-1: every route gets a default limit, keyed by a *verified* client identity.

A fresh Limiter is built from the production `client_key` with a tiny default so the test
stays fast and does not reload `rate_limit` (other modules hold references to its limiter).
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

import rate_limit
from auth import keys
from auth.security import create_token
from config import settings


@pytest.fixture
def client():
    test_limiter = Limiter(key_func=rate_limit.client_key, default_limits=["3/minute"])
    app = FastAPI()
    app.state.limiter = test_limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit.rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    @app.get("/ping")
    def ping(request: Request):
        return {"ok": True}

    return TestClient(app)


@pytest.fixture
def token_for():
    private_key, _, _ = keys.get_keypair(settings)
    return lambda user_id: create_token(user_id, private_key, 60)


def test_production_limiter_has_a_default_limit():
    assert rate_limit.DEFAULT_LIMIT
    assert rate_limit.limiter._default_limits, "limiter must carry a default limit"


def test_undecorated_route_gets_default_limit(client):
    codes = [client.get("/ping").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_429_carries_retry_after(client):
    for _ in range(4):
        resp = client.get("/ping")
    assert resp.status_code == 429 and int(resp.headers["Retry-After"]) > 0


def test_forged_cookie_uses_ip_bucket(client):
    codes = [
        client.get("/ping", cookies={"suite_session": f"junk{i}"}).status_code for i in range(4)
    ]
    assert codes[-1] == 429


def test_two_users_same_ip_have_separate_buckets(client, token_for):
    a, b = token_for(1), token_for(2)
    for _ in range(3):
        assert client.get("/ping", headers={"Authorization": f"Bearer {a}"}).status_code == 200
    assert client.get("/ping", headers={"Authorization": f"Bearer {a}"}).status_code == 429
    assert client.get("/ping", headers={"Authorization": f"Bearer {b}"}).status_code == 200


def test_client_key_shapes(token_for):
    def req(headers=None, cookies=None):
        raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        if cookies:
            jar = "; ".join(f"{k}={v}" for k, v in cookies.items())
            raw.append((b"cookie", jar.encode()))
        return Request({"type": "http", "headers": raw, "client": ("203.0.113.9", 1234)})

    assert rate_limit.client_key(req(headers={"Authorization": f"Bearer {token_for(7)}"})) == "u:7"
    assert rate_limit.client_key(req(cookies={"suite_session": token_for(8)})) == "u:8"
    assert rate_limit.client_key(req(cookies={"suite_session": "junk"})) == "ip:203.0.113.9"
    assert rate_limit.client_key(req()) == "ip:203.0.113.9"
