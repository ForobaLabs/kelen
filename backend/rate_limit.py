"""Shared slowapi Limiter instance.

Split out from main.py so auth/router.py and twofa/router.py can apply
per-endpoint rate limits without a circular import (main.py imports those
routers).
"""

from __future__ import annotations

import os

import jwt
from fastapi import Request, Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

DEFAULT_LIMIT = os.getenv("RATE_LIMIT_DEFAULT", "120/minute")


def client_key(request: Request) -> str:
    """Rate-limit identity: ``u:<user id>`` when the session token verifies, else
    ``ip:<remote address>`` (AW-1 / H-1).

    The token is the ``suite_session`` cookie or a Bearer header. It is verified (RS256
    signature + expiry) so a forged or expired cookie can never mint a fresh bucket: it
    falls back to the caller's IP bucket. No DB access; the ``sub`` claim is enough.
    """
    token = None
    authorization = request.headers.get("authorization", "")
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() == "bearer" and credential.strip():
        token = credential.strip()
    token = token or request.cookies.get("suite_session")
    if token:
        # Imported here: auth.* imports this module's limiter (circular otherwise).
        from auth import keys
        from auth.security import decode_token
        from config import settings

        try:
            _, public_key, _ = keys.get_keypair(settings)
            claims = decode_token(token, public_key)
            return f"u:{int(claims['sub'])}"
        except (jwt.PyJWTError, ValueError, KeyError):
            pass
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=client_key, default_limits=[DEFAULT_LIMIT])


async def enforce_default_limit(request: Request) -> None:
    """App-wide dependency enforcing rate limits on every matched route (AW-6).

    slowapi's ``SlowAPIMiddleware`` looks the handler up in ``app.routes``; on current FastAPI
    that list holds ``_IncludedRouter`` objects without ``.endpoint``, so every route mounted via
    ``include_router`` silently skipped the default limit. A dependency runs after routing, where
    ``request.scope["route"]`` is the real route, and before route-level auth dependencies, so
    rejected (401/403) attempts count too. Routes with their own ``@limiter.limit`` are checked
    here against that limit (the decorator then sees ``_rate_limiting_complete`` and skips its
    own check: one hit per request); ``@limiter.exempt`` routes are skipped.
    """
    handler = getattr(request.scope.get("route"), "endpoint", None)
    if handler is None:
        return
    name = f"{handler.__module__}.{handler.__name__}"
    if name in limiter._exempt_routes:
        return
    own = name in limiter._route_limits or name in limiter._dynamic_route_limits
    limiter._check_request_limit(request, handler, not own)
    request.state._rate_limiting_complete = True


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """429 handler that also sends `Retry-After` (seconds until the window resets).

    slowapi only injects it when `headers_enabled=True`, which in turn requires every
    endpoint to accept a `response` param; computing it here keeps endpoints untouched.
    """
    response = _rate_limit_exceeded_handler(request, exc)
    response.headers["Retry-After"] = str(int(exc.limit.limit.get_expiry()))
    return response


# Kept as an alias: existing routes import this name (foroba#176, A9).
session_rate_limit_key = client_key
