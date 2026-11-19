"""Plan AU M-7 + AW-2: production startup guard (v2)."""

import pytest

from prod_guard import check_production_config

GOOD = {
    "ENVIRONMENT": "production",
    "INTERNAL_API_TOKEN": "x" * 32,
    "ALLOWED_ORIGINS": "https://legacy.example.org",
    "ENABLE_API_DOCS": "false",
    "SEED_MOCK_USERS": "false",
    "DEPLOYMENT_MODE": "hosted",
    "DB_ENCRYPTION_KEY": "d" * 32,
    "FIELD_ENCRYPTION_KEY": "f" * 44,
    "AI_GATEWAY_TOKEN": "a" * 32,
    "SANDBOX_RUNNER_TOKEN": "s" * 32,
    "COOKIE_DOMAIN": ".example.org",
}


def good_env():
    return dict(GOOD)


def test_noop_outside_production():
    check_production_config({"AUTH_DEV_BYPASS": "true", "ENABLE_API_DOCS": "true"}, app="foroba")


def test_good_production_config_passes():
    check_production_config(GOOD, cookie_secure=True, app="foroba")


def test_native_origins_are_allowed():
    origins = "https://a.b,capacitor://localhost,ionic://localhost"
    check_production_config({**GOOD, "ALLOWED_ORIGINS": origins}, cookie_secure=True, app="foroba")


@pytest.mark.parametrize(
    "override",
    [
        {"AUTH_DEV_BYPASS": "true"},
        {"INTERNAL_API_TOKEN": ""},
        {"ALLOWED_ORIGINS": ""},
        {"ALLOWED_ORIGINS": "*"},
        {"ALLOWED_ORIGINS": "http://legacy.example.org"},
        {"ALLOWED_ORIGINS": "https://a.b,http://203.0.113.10:8080"},
        {"ALLOWED_ORIGINS": "http://localhost.evil.example"},
    ],
)
def test_bad_production_config_refuses_to_boot(override):
    with pytest.raises(RuntimeError):
        check_production_config({**GOOD, **override}, cookie_secure=True, app="foroba")


def test_insecure_cookie_refuses_to_boot():
    with pytest.raises(RuntimeError, match="cookie_secure"):
        check_production_config(GOOD, cookie_secure=False, app="foroba")


@pytest.mark.parametrize(
    "var,bad",
    [
        ("ENABLE_API_DOCS", "true"),
        ("ENABLE_API_DOCS", ""),
        ("SEED_MOCK_USERS", "true"),
        ("DEPLOYMENT_MODE", "selfhost"),
        ("DB_ENCRYPTION_KEY", "short"),
        ("FIELD_ENCRYPTION_KEY", ""),
        ("AI_GATEWAY_TOKEN", ""),
        ("SANDBOX_RUNNER_TOKEN", "x" * 10),
        ("COOKIE_DOMAIN", ""),
    ],
)
def test_guard_refuses(var, bad):
    env = good_env() | {var: bad}
    with pytest.raises(RuntimeError) as exc:
        check_production_config(env, cookie_secure=True, app="foroba")
    assert var in str(exc.value)
    assert not bad or bad not in str(exc.value)


def test_loopback_origin_refused_in_production():
    env = good_env() | {"ALLOWED_ORIGINS": "https://app.forobalabs.com,http://localhost:5175"}
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        check_production_config(env, cookie_secure=True, app="foroba")


def test_billing_fake_provider_refused():
    env = good_env() | {"BILLING_ENABLED": "true", "BILLING_PROVIDER": "fake"}
    with pytest.raises(RuntimeError, match="BILLING_PROVIDER"):
        check_production_config(env, cookie_secure=True, app="foroba")


def test_billing_sandbox_api_refused():
    env = good_env() | {
        "BILLING_ENABLED": "true",
        "BILLING_PROVIDER": "paddle",
        "PADDLE_API_BASE": "https://sandbox-api.paddle.com",
    }
    with pytest.raises(RuntimeError, match="PADDLE_API_BASE"):
        check_production_config(env, cookie_secure=True, app="foroba")


def test_all_reasons_reported_together():
    env = good_env() | {"ENABLE_API_DOCS": "true", "SEED_MOCK_USERS": "true"}
    with pytest.raises(RuntimeError) as exc:
        check_production_config(env, cookie_secure=True, app="foroba")
    assert "ENABLE_API_DOCS" in str(exc.value) and "SEED_MOCK_USERS" in str(exc.value)


def test_foroba_only_checks_skipped_for_tsumi():
    env = good_env() | {"SEED_MOCK_USERS": "true", "AI_GATEWAY_TOKEN": "", "COOKIE_DOMAIN": ""}
    check_production_config(env, cookie_secure=True, app="tsumi")


def test_field_key_required_for_pazuru_but_not_tsumi():
    env = good_env() | {"FIELD_ENCRYPTION_KEY": ""}
    with pytest.raises(RuntimeError, match="FIELD_ENCRYPTION_KEY"):
        check_production_config(env, cookie_secure=True, app="pazuru")
    check_production_config(env, cookie_secure=True, app="tsumi")


def test_unknown_app_name_is_rejected():
    with pytest.raises(ValueError):
        check_production_config(good_env(), app="nope")
