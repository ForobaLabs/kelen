"""Usage ledger (metadata only, never prompt or reply text) and usage endpoints."""

import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

import db
from auth import Caller, require_caller
from config import get_settings

router = APIRouter(prefix="/v1", tags=["usage"])


def now() -> datetime:
    return datetime.now(UTC)


def record(
    conn,
    caller: Caller,
    app: str,
    feature: str,
    task: str,
    provider: str,
    model: str,
    neurons: int,
    latency_ms: int,
    outcome: str,
    at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO ai_usage(user_id, tier, app, feature, task, provider, model, neurons, "
        "latency_ms, outcome, at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            caller.user_id,
            caller.tier,
            app,
            feature,
            task,
            provider,
            model,
            neurons,
            latency_ms,
            outcome,
            at or now().isoformat(),
        ),
    )
    conn.commit()


def month_bounds(month: str) -> tuple[str, str]:
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
    end = (start + timedelta(days=32)).replace(day=1)
    return start.isoformat(), end.isoformat()


def month_key(moment: datetime) -> str:
    return moment.strftime("%Y-%m")


def neurons_between(conn, start: str, end: str, user_id: int | None = None) -> int:
    sql = "SELECT COALESCE(SUM(neurons),0) AS n FROM ai_usage WHERE at >= ? AND at < ?"
    args: list = [start, end]
    if user_id is not None:
        sql += " AND user_id=?"
        args.append(user_id)
    return conn.execute(sql, args).fetchone()["n"]


def user_month_neurons(conn, user_id: int, moment: datetime) -> int:
    return neurons_between(conn, *month_bounds(month_key(moment)), user_id=user_id)


def suite_month_neurons(conn, moment: datetime) -> int:
    return neurons_between(conn, *month_bounds(month_key(moment)))


def suite_day_neurons(conn, moment: datetime) -> int:
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return neurons_between(conn, start.isoformat(), (start + timedelta(days=1)).isoformat())


CLOUD_PROVIDERS = ("cloudflare", "gemini")  # BYOK providers are the user's own money


def eur(neurons: int) -> float:
    return round(neurons * get_settings().eur_per_1k_neurons / 1000, 6)


def budget(conn, moment: datetime) -> dict:
    """Suite-wide monthly spend vs the cap (all users, synthetic traffic included)."""
    cap = get_settings().monthly_eur_cap
    used = eur(suite_month_neurons(conn, moment))
    return {
        "cap_eur": cap,
        "used_eur": used,
        "percent": int(used * 100 / cap) if cap > 0 else 100,
        "alert": cap > 0 and used >= 0.8 * cap,
    }


def user_day_messages(conn, user_id: int, moment: datetime, *, local: bool) -> int:
    """Messages the user sent today (UTC): local calls, or successful Kelen-paid cloud calls."""
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if local:
        cond, args = "provider='local' AND outcome='local'", []
    else:
        marks = ",".join("?" * len(CLOUD_PROVIDERS))
        cond, args = f"outcome='ok' AND provider IN ({marks})", list(CLOUD_PROVIDERS)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM ai_usage WHERE user_id=? AND at >= ? AND at < ? AND {cond}",
        (user_id, start.isoformat(), (start + timedelta(days=1)).isoformat(), *args),
    ).fetchone()
    return row["n"]


def allowance_for(tier: str) -> int:
    s = get_settings()
    return s.allowance_premium_neurons if tier == "premium" else s.allowance_free_neurons


@router.get("/usage")
def my_usage(caller: Caller = Depends(require_caller), conn=Depends(db.get_conn)):  # noqa: B008
    allowance = allowance_for(caller.tier)
    used = user_month_neurons(conn, caller.user_id, now())
    unlimited = caller.user_id in get_settings().test_allowlist
    percent = 0 if unlimited or allowance <= 0 else min(100, round(used * 100 / allowance))
    return {
        "tier": caller.tier,
        "allowance": allowance,
        "used": used,
        "percent": percent,
        "unlimited": unlimited,
    }


@router.get("/admin/usage")
def admin_usage(
    month: str | None = Query(default=None),
    caller: Caller = Depends(require_caller),  # noqa: B008
    conn=Depends(db.get_conn),  # noqa: B008
):
    if not caller.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    month = month or month_key(now())
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")
    start, end = month_bounds(month)
    synthetic = sorted(get_settings().synthetic_user_ids)
    where = "at >= ? AND at < ? AND outcome != 'breaker'"
    if synthetic:
        where += f" AND user_id NOT IN ({','.join(str(int(i)) for i in synthetic)})"

    def rows(select: str, group: str):
        return [
            dict(r)
            for r in conn.execute(
                f"SELECT {select}, COALESCE(SUM(neurons),0) AS neurons, COUNT(*) AS requests "
                f"FROM ai_usage WHERE {where} GROUP BY {group} ORDER BY neurons DESC",
                (start, end),
            )
        ]

    totals = conn.execute(
        f"SELECT COALESCE(SUM(neurons),0) AS neurons, COUNT(*) AS requests, "
        f"COUNT(DISTINCT user_id) AS users FROM ai_usage WHERE {where}",
        (start, end),
    ).fetchone()
    by_day = [
        {**dict(r), "cost_eur": eur(r["neurons"])}
        for r in conn.execute(
            f"SELECT substr(at,1,10) AS day, COUNT(*) AS requests, "
            f"COALESCE(SUM(neurons),0) AS neurons FROM ai_usage WHERE {where} "
            f"GROUP BY day ORDER BY day",
            (start, end),
        )
    ]
    return {
        "month": month,
        "totals": dict(totals),
        "cost_eur": eur(totals["neurons"]),
        "budget": budget(conn, datetime.fromisoformat(start)),
        "by_day": by_day,
        "by_user": rows("user_id, MAX(tier) AS tier", "user_id"),
        "by_app_feature": rows("app, feature", "app, feature"),
        "by_model": rows("model", "model"),
        "outcomes": {
            r["outcome"]: r["n"]
            for r in conn.execute(
                f"SELECT outcome, COUNT(*) AS n FROM ai_usage WHERE {where} GROUP BY outcome",
                (start, end),
            )
        },
    }
