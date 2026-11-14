"""Tier allowance, suite ceilings, per-user rate limit and the Cloudflare quota breaker."""

from collections import defaultdict, deque
from datetime import datetime, timedelta

import usage
from auth import Caller
from config import get_settings

_hits: dict[int, deque] = defaultdict(deque)
_breaker_until: datetime | None = None


def reset() -> None:
    global _breaker_until
    _hits.clear()
    _breaker_until = None


def next_midnight(moment: datetime) -> datetime:
    return (moment + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def trip_breaker(until: datetime, conn=None) -> None:
    """Open the breaker until `until`; persisted so a restart does not reopen it early."""
    global _breaker_until
    _breaker_until = until
    if conn is not None:
        conn.execute(
            "INSERT INTO ai_usage(user_id, outcome, task, neurons, at) VALUES (0,'breaker',?,0,?)",
            (until.isoformat(), usage.now().isoformat()),
        )
        conn.commit()


def load_breaker(conn) -> None:
    global _breaker_until
    row = conn.execute(
        "SELECT task FROM ai_usage WHERE outcome='breaker' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row:
        until = datetime.fromisoformat(row["task"])
        if until > usage.now():
            _breaker_until = until


def breaker_open() -> bool:
    return _breaker_until is not None and usage.now() < _breaker_until


def _rate_limited(user_id: int) -> bool:
    moment = usage.now()
    hits = _hits[user_id]
    while hits and (moment - hits[0]).total_seconds() >= 60:
        hits.popleft()
    if len(hits) >= get_settings().rate_limit_per_min:
        return True
    hits.append(moment)
    return False


def check_detail(conn, caller: Caller, mode: str) -> tuple[str | None, str | None]:
    """(notice, reason). reason is rate|breaker|suite|allowance when a notice is returned."""
    s = get_settings()
    if _rate_limited(caller.user_id):
        return "cloud_unavailable", "rate"
    moment = usage.now()
    unlimited = caller.user_id in s.test_allowlist
    if mode == "local":
        if (
            not unlimited
            and caller.tier != "premium"
            and usage.user_day_messages(conn, caller.user_id, moment, local=True)
            >= s.daily_free_local_messages
        ):
            return "plan_quota_exhausted", "daily"
        return None, None
    if mode != "cloud":
        return None, None  # BYOK: the user's own key, never metered
    if breaker_open():
        return "cloud_unavailable", "breaker"
    if usage.budget(conn, moment)["percent"] >= 100:
        return "cloud_unavailable", "budget"
    if (
        usage.suite_month_neurons(conn, moment) >= s.cf_monthly_neurons
        or usage.suite_day_neurons(conn, moment) >= s.cf_daily_soft_neurons
    ):
        return "cloud_unavailable", "suite"
    if unlimited:
        return None, None
    cap = s.daily_premium_cloud_messages if caller.tier == "premium" else s.daily_free_cloud_messages
    if cap <= 0:
        return "plan_quota_exhausted", "tier"
    if usage.user_day_messages(conn, caller.user_id, moment, local=False) >= cap:
        return "plan_quota_exhausted", "daily"
    if usage.user_month_neurons(conn, caller.user_id, moment) >= usage.allowance_for(caller.tier):
        return "plan_quota_exhausted", "allowance"
    return None, None


def check(conn, caller: Caller, mode: str) -> str | None:
    return check_detail(conn, caller, mode)[0]
