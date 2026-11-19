"""Breached-password check via Have I Been Pwned range API (k-anonymity).

Only the first 5 hex characters of the SHA-1 ever leave the server. Fails open: a network
error or non-200 answer never blocks a sign-up or password change (a warning is logged once).
Enabled by default when DEPLOYMENT_MODE=hosted; override with HIBP_CHECK_ENABLED=true|false.
"""

from __future__ import annotations

import hashlib
import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

_RANGE_URL = "https://api.pwnedpasswords.com/range/"
BREACHED_MESSAGE = "This password appears in known data breaches. Choose a different one."
_warned = False


def _enabled() -> bool:
    flag = settings.hibp_check_enabled
    return flag if flag is not None else settings.deployment_mode == "hosted"


def is_breached(password: str, *, timeout: float = 1.5) -> bool:
    global _warned
    if not _enabled():
        return False
    digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    try:
        resp = httpx.get(_RANGE_URL + prefix, headers={"Add-Padding": "true"}, timeout=timeout)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        if not _warned:
            _warned = True
            logger.warning("HIBP check unavailable, failing open: %s", type(exc).__name__)
        return False
    for line in resp.text.splitlines():
        candidate, _, count = line.strip().partition(":")
        if candidate == suffix and count.strip() not in ("", "0"):
            return True
    return False
