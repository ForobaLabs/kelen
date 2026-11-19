"""Refuse to boot a production container with dev-only settings (Plan AU M-7, AW-2).

Identical copies live in foroba/backend/prod_guard.py, pazuru/backend/app/prod_guard.py
and tsumi/api/prod_guard.py (checked by scripts/check_prod_guard_parity.sh). A no-op
unless ENVIRONMENT=production. Reasons name the variable, never its value.
"""

from __future__ import annotations

from collections.abc import Mapping

# pfh-mobile WebView origins (iOS Capacitor/Ionic schemes) are not https:// but are
# not attacker-hostable either.
_NATIVE_ORIGINS = {"capacitor://localhost", "ionic://localhost"}
_APPS = {"foroba", "pazuru", "tsumi"}
_MIN_SECRET = 32


def _origin_ok(origin: str) -> bool:
    return origin.startswith("https://") or origin in _NATIVE_ORIGINS


def _short(env: Mapping[str, str], name: str) -> bool:
    return len(env.get(name, "")) < _MIN_SECRET


def check_production_config(
    env: Mapping[str, str], *, cookie_secure: bool | None = None, app: str
) -> None:
    if app not in _APPS:
        raise ValueError(f"unknown app {app!r}")
    if env.get("ENVIRONMENT", "").lower() != "production":
        return
    problems: list[str] = []
    if env.get("AUTH_DEV_BYPASS", "false").lower() == "true":
        problems.append("AUTH_DEV_BYPASS=true")
    if _short(env, "INTERNAL_API_TOKEN"):
        problems.append("INTERNAL_API_TOKEN missing or under 32 characters")
    origins = [o.strip() for o in env.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
    if not origins or any(not _origin_ok(o) for o in origins):
        problems.append("ALLOWED_ORIGINS must be explicit https:// (or native) origins")
    if cookie_secure is False:
        problems.append("cookie_secure must be true")
    if env.get("ENABLE_API_DOCS") != "false":
        problems.append("ENABLE_API_DOCS must be exactly 'false'")
    if _short(env, "DB_ENCRYPTION_KEY"):
        problems.append("DB_ENCRYPTION_KEY missing or under 32 characters")
    if app in {"foroba", "pazuru"} and not env.get("FIELD_ENCRYPTION_KEY"):
        problems.append("FIELD_ENCRYPTION_KEY is empty")
    if app == "foroba":
        if env.get("SEED_MOCK_USERS") != "false":
            problems.append("SEED_MOCK_USERS must be exactly 'false'")
        if env.get("DEPLOYMENT_MODE") != "hosted":
            problems.append("DEPLOYMENT_MODE must be 'hosted'")
        for name in ("AI_GATEWAY_TOKEN", "SANDBOX_RUNNER_TOKEN"):
            if _short(env, name):
                problems.append(f"{name} missing or under 32 characters")
        if not env.get("COOKIE_DOMAIN"):
            problems.append("COOKIE_DOMAIN is empty")
        if env.get("BILLING_ENABLED", "false").lower() == "true":
            if env.get("BILLING_PROVIDER") != "paddle":
                problems.append("BILLING_PROVIDER must be 'paddle' when billing is enabled")
            if "sandbox" in env.get("PADDLE_API_BASE", "").lower():
                problems.append("PADDLE_API_BASE points at the Paddle sandbox")
    if problems:
        raise RuntimeError("Unsafe production config: " + "; ".join(problems))
