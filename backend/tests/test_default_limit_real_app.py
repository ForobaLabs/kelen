"""AW-6: the default limit must hold on the REAL app, not only on a toy FastAPI app.

AW-1's test used a bare app. On FastAPI >= 0.14x ``app.routes`` holds ``_IncludedRouter`` objects
(no ``.endpoint``), so slowapi's middleware resolved no handler and silently skipped every route
that is mounted with ``include_router``. This test drives routes of the production app.
"""

import pytest
from fastapi.testclient import TestClient

import main
from rate_limit import DEFAULT_LIMIT, limiter

N = int(DEFAULT_LIMIT.split("/")[0])


@pytest.fixture
def client():
    limiter.reset()
    yield TestClient(main.app, raise_server_exceptions=False)
    limiter.reset()


@pytest.mark.parametrize(
    ("path", "limit"),
    [
        ("/auth/consents", N),  # undecorated, behind include_router -> default limit
        ("/auth/2fa/methods", N),
        ("/api/public/v1/networth", 30),  # own @limiter.limit("30/minute"); 401s must count
    ],
)
def test_included_router_route_is_limited(client, path, limit):
    codes = [client.get(path).status_code for _ in range(limit + 1)]
    assert 429 not in codes[:limit], f"throttled before the limit: {codes[:limit]}"
    assert codes[limit] == 429


def test_health_stays_exempt(client):
    assert all(client.get("/health").status_code != 429 for _ in range(N + 5))
