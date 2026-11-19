"""AW-6 / L-7: breached-password check (HIBP k-anonymity) on register, change and reset."""

from __future__ import annotations

import hashlib
import logging
import sqlite3

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import breached_passwords
from auth.conn import get_conn
from auth.router import router as auth_router
from config import settings
from db import init_schema
from rate_limit import limiter
from settings_store import init_settings

BREACHED = "Password1234!"
CLEAN = "Zq7-unique-horse-battery-4821"


def _sha(p: str) -> str:
    return hashlib.sha1(p.encode()).hexdigest().upper()


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "hibp_check_enabled", True)
    limiter.reset()
    breached_passwords._warned = False
    yield
    limiter.reset()


@pytest.fixture
def mock_range(monkeypatch):
    """Fake HIBP range endpoint. Records requested URLs and headers."""
    calls: list[tuple[str, dict]] = []

    def fake_get(url, headers=None, timeout=None, **_):
        calls.append((url, headers or {}))
        body = f"{_sha(BREACHED)[5:]}:42\r\n0018A45C4D1DEF81644B54AB7F969B88D65:3\r\n"
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(breached_passwords.httpx, "get", fake_get)
    return calls


def test_known_breached_password_detected(mock_range):
    assert breached_passwords.is_breached(BREACHED) is True


def test_unknown_password_ok(mock_range):
    assert breached_passwords.is_breached(CLEAN) is False


def test_padding_rows_with_zero_count_are_ignored(monkeypatch):
    def fake_get(url, headers=None, timeout=None, **_):
        body = f"{_sha(CLEAN)[5:]}:0\r\n"
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(breached_passwords.httpx, "get", fake_get)
    assert breached_passwords.is_breached(CLEAN) is False


def test_only_prefix_is_sent(mock_range):
    breached_passwords.is_breached(BREACHED)
    url, headers = mock_range[0]
    assert url == f"https://api.pwnedpasswords.com/range/{_sha(BREACHED)[:5]}"
    assert headers.get("Add-Padding") == "true"
    assert _sha(BREACHED)[5:] not in url and BREACHED not in url


def test_network_error_fails_open_and_warns_once(monkeypatch, caplog):
    def boom(*_a, **_k):
        raise httpx.ConnectTimeout("slow")

    monkeypatch.setattr(breached_passwords.httpx, "get", boom)
    with caplog.at_level(logging.WARNING):
        assert breached_passwords.is_breached(BREACHED) is False
        assert breached_passwords.is_breached(BREACHED) is False
    assert len([r for r in caplog.records if "HIBP" in r.getMessage()]) == 1


def test_http_error_status_fails_open(monkeypatch):
    def fake_get(url, headers=None, timeout=None, **_):
        return httpx.Response(503, text="", request=httpx.Request("GET", url))

    monkeypatch.setattr(breached_passwords.httpx, "get", fake_get)
    assert breached_passwords.is_breached(BREACHED) is False


def test_disabled_by_flag(monkeypatch, mock_range):
    monkeypatch.setattr(settings, "hibp_check_enabled", False)
    assert breached_passwords.is_breached(BREACHED) is False
    assert mock_range == []


def test_default_follows_deployment_mode(monkeypatch, mock_range):
    monkeypatch.setattr(settings, "hibp_check_enabled", None)
    monkeypatch.setattr(settings, "deployment_mode", "selfhost")
    assert breached_passwords.is_breached(BREACHED) is False
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    assert breached_passwords.is_breached(BREACHED) is True


# ── endpoint wiring ──────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("auth.router.invalidate_sessions_downstream", lambda *_: None)
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    init_settings(conn)
    app = FastAPI()
    app.dependency_overrides[get_conn] = lambda: conn
    app.include_router(auth_router)
    return TestClient(app, raise_server_exceptions=False), conn


def _body(password: str, email: str = "hibp@example.com") -> dict:
    return {
        "email": email,
        "email_confirm": email,
        "name": "Hibp",
        "password": password,
        "password_confirm": password,
        "tos_accepted": True,
    }


def test_register_rejects_breached(client, mock_range):
    c, _ = client
    r = c.post("/auth/register", json=_body(BREACHED))
    assert r.status_code == 422
    assert "appears in known data breaches" in r.text


def test_register_accepts_clean(client, mock_range):
    c, _ = client
    assert c.post("/auth/register", json=_body(CLEAN)).status_code == 201


def test_change_password_rejects_breached(client, mock_range):
    c, _ = client
    assert c.post("/auth/register", json=_body(CLEAN)).status_code == 201
    from auth.dependencies import get_current_user

    user = c.app.dependency_overrides[get_conn]().execute("SELECT * FROM users").fetchone()
    c.app.dependency_overrides[get_current_user] = lambda: dict(user)
    r = c.put(
        "/auth/me/password",
        json={
            "current_password": CLEAN,
            "new_password": BREACHED,
            "new_password_confirm": BREACHED,
        },
    )
    assert r.status_code == 422
    assert "appears in known data breaches" in r.text


def test_reset_rejects_breached_before_consuming_code(client, mock_range, monkeypatch):
    c, _ = client
    assert c.post("/auth/register", json=_body(CLEAN)).status_code == 201
    consumed: list[int] = []
    monkeypatch.setattr("auth.router.verify_email_otp", lambda *_: consumed.append(1) or True)
    r = c.post(
        "/auth/me/password/reset",
        json={
            "email": "hibp@example.com",
            "code": "123456",
            "new_password": BREACHED,
            "new_password_confirm": BREACHED,
        },
    )
    assert r.status_code == 422
    assert consumed == []  # a rejected password must not burn the one-time code
