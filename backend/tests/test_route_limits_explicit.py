"""AW-1 Step 5: expensive routes carry an explicit, tighter limit than the default."""

import pytest

import admin_router
import ai_proxy_router
import billing.router as billing_router
import main
import monthly_report_router
import whatif_router
from auth import router as auth_router
from rate_limit import limiter

EXPECTED = [
    (auth_router.export_account, "3 per 1 hour"),
    (auth_router.upload_avatar, "10 per 1 hour"),
    (monthly_report_router.generate, "6 per 1 hour"),
    (monthly_report_router.retry, "6 per 1 hour"),
    (whatif_router.simulate, "30 per 1 minute"),
    (ai_proxy_router.test_byok, "10 per 1 hour"),
    (billing_router.checkout_session, "10 per 1 hour"),
    (main.markets_overview, "30 per 1 minute"),
    (admin_router.admin_patch_setting, "30 per 1 minute"),
    (admin_router.admin_set_account_disabled, "30 per 1 minute"),
    (admin_router.admin_create_invite_code, "30 per 1 minute"),
    (admin_router.admin_revoke_invite_code, "30 per 1 minute"),
    (admin_router.admin_import_backup, "30 per 1 minute"),
    (admin_router.admin_export_suite, "30 per 1 minute"),
    (admin_router.admin_import_suite, "30 per 1 minute"),
]


@pytest.mark.parametrize("func,expected", EXPECTED, ids=[f.__name__ for f, _ in EXPECTED])
def test_route_has_explicit_limit(func, expected):
    name = f"{func.__module__}.{func.__name__}"
    limits = limiter._route_limits.get(name)
    assert limits, f"{name} has no explicit @limiter.limit"
    assert [str(lim.limit) for lim in limits] == [expected]
