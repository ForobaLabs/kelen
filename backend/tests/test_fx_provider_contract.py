"""Task 3.3: Frankfurter contract — timeout/429/malformed/zero-rate/weekend never break totals."""
import sqlite3
from datetime import date

import httpx
import pytest
import respx

import db
import fx_service

LATEST = "https://api.frankfurter.app/latest"
SATURDAY = date(2026, 11, 14)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.init_schema(c)
    return c


def _stale(conn, ccy="GBP", rate=1.15):
    fx_service._upsert_rate(conn, ccy, rate, date(2026, 11, 1))


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, headers={"Retry-After": "30"}),
        httpx.Response(503),
        httpx.Response(200, text="<html>maintenance</html>"),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"rates": None}),
        httpx.Response(200, json={"rates": {"GBP": 0}}),
        httpx.Response(200, json={"rates": {"GBP": "n/a"}}),
        httpx.Response(200, json={"rates": {"USD": 1.25}}),  # currency absent
    ],
)
def test_bad_provider_responses_fall_back_to_last_cached_rate(conn, response):
    respx.get(LATEST).mock(return_value=response)
    _stale(conn)
    assert fx_service.get_rate(conn, "GBP") == 1.15


@respx.mock
def test_timeout_falls_back_to_last_cached_rate(conn):
    respx.get(LATEST).mock(side_effect=httpx.ReadTimeout("slow"))
    _stale(conn)
    assert fx_service.get_rate(conn, "GBP") == 1.15


@respx.mock
def test_zero_rate_with_no_cache_is_unavailable_not_a_crash(conn):
    respx.get(LATEST).mock(return_value=httpx.Response(200, json={"rates": {"GBP": 0}}))
    with pytest.raises(fx_service.FxRateUnavailable):
        fx_service.get_rate(conn, "GBP")


@respx.mock
def test_refresh_never_raises_and_skips_unusable_rates(conn):
    respx.get(LATEST).mock(
        return_value=httpx.Response(200, json={"rates": {"GBP": 0, "USD": 1.25, "XXX": "bad"}})
    )
    fx_service.refresh_latest_rates(conn)
    cached = {r[0]: r[1] for r in conn.execute("SELECT currency, rate_to_eur FROM fx_rates")}
    assert cached == {"USD": pytest.approx(0.8)}


@respx.mock
def test_weekend_date_cached_under_requested_date(conn):
    respx.get(f"https://api.frankfurter.app/{SATURDAY.isoformat()}").mock(
        return_value=httpx.Response(200, json={"date": "2026-11-13", "rates": {"USD": 1.25}})
    )
    assert fx_service.get_rate(conn, "USD", SATURDAY) == pytest.approx(0.8)
    row = conn.execute("SELECT rate_date FROM fx_rates WHERE currency='USD'").fetchone()
    assert row[0] == SATURDAY.isoformat()
