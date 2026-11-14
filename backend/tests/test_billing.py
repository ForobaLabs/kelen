"""Billing foundation (BR-12): signature, event parsing, idempotent tier sync, routes."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import db
from auth import repository as repo
from auth.conn import get_conn
from auth.dependencies import get_current_user
from billing.events import parse_paddle_event
from billing.paddle_signature import SignatureError, verify_signature
from billing.service import apply_event, tier_for
from billing.sweep import sweep_expired
from config import settings
from main import app

SECRET = "pdl_ntfset_test_secret"
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def sign(body: bytes, ts: int, secret: str = SECRET) -> str:
    h1 = hmac.new(secret.encode(), f"{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={h1}"


def payload(
    event_id="evt_1",
    etype="subscription.activated",
    status="active",
    user_id="1",
    sub="sub_1",
    occurred="2026-11-01T10:00:00Z",
    ends="2026-12-01T10:00:00Z",
    cancel=False,
):
    return {
        "event_id": event_id,
        "event_type": etype,
        "occurred_at": occurred,
        "data": {
            "id": sub,
            "status": status,
            "customer_id": "ctm_1",
            "custom_data": {"user_id": user_id},
            "current_billing_period": {"ends_at": ends},
            "scheduled_change": {"action": "cancel", "effective_at": ends} if cancel else None,
        },
    }


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    repo.create_user(
        c, email="b@test.local", name="B", password_hash="x", tier="free", audience="base"
    )
    return c


# --- signature -------------------------------------------------------------
def test_signature_accepts_valid():
    body = b'{"a":1}'
    ts = int(NOW.timestamp())
    verify_signature(sign(body, ts), body, SECRET, now=NOW)


@pytest.mark.parametrize("header", ["", "garbage", "ts=abc;h1=00", "h1=00", "ts=1"])
def test_signature_rejects_malformed(header):
    with pytest.raises(SignatureError):
        verify_signature(header, b"{}", SECRET, now=NOW)


def test_signature_rejects_tampered_body():
    ts = int(NOW.timestamp())
    with pytest.raises(SignatureError):
        verify_signature(sign(b'{"a":1}', ts), b'{"a":2}', SECRET, now=NOW)


def test_signature_rejects_wrong_secret_and_empty_secret():
    body, ts = b"{}", int(NOW.timestamp())
    with pytest.raises(SignatureError):
        verify_signature(sign(body, ts, "other"), body, SECRET, now=NOW)
    with pytest.raises(SignatureError):
        verify_signature(sign(body, ts, ""), body, "", now=NOW)


def test_signature_rejects_replay_outside_tolerance():
    body = b"{}"
    ts = int((NOW - timedelta(minutes=10)).timestamp())
    with pytest.raises(SignatureError):
        verify_signature(sign(body, ts), body, SECRET, now=NOW)


def test_signature_accepts_rotated_second_h1():
    body, ts = b"{}", int(NOW.timestamp())
    good = sign(body, ts).split("h1=")[1]
    verify_signature(f"ts={ts};h1={'0' * 64};h1={good}", body, SECRET, now=NOW)


# --- parsing -----------------------------------------------------------------
def test_parse_subscription_event():
    ev = parse_paddle_event(payload(cancel=True))
    assert (ev.event_id, ev.subscription_id, ev.user_id, ev.status) == (
        "evt_1",
        "sub_1",
        1,
        "active",
    )
    assert ev.cancel_at_period_end is True
    assert ev.period_end == datetime(2026, 12, 1, 10, 0, tzinfo=UTC)


def test_parse_ignores_non_subscription_and_bad_input():
    assert (
        parse_paddle_event({"event_id": "e", "event_type": "transaction.completed", "data": {}})
        is None
    )
    assert parse_paddle_event({}) is None
    assert parse_paddle_event(payload(user_id="abc")).user_id is None
    assert parse_paddle_event(payload(status="weird")).status == "weird"


# --- tier derivation -----------------------------------------------------------
@pytest.mark.parametrize(
    "status,expected",
    [
        ("active", "premium"),
        ("trialing", "premium"),
        ("past_due", "premium"),
        ("paused", "free"),
        ("canceled", "free"),
        ("weird", "free"),
    ],
)
def test_tier_for_status(status, expected):
    assert tier_for(status, NOW, NOW + timedelta(days=1), grace_days=7) == expected


def test_past_due_loses_premium_after_grace():
    assert tier_for("past_due", NOW - timedelta(days=8), NOW, grace_days=7) == "free"


# --- apply_event -----------------------------------------------------------------
def user(conn):
    return repo.get_user_by_email(conn, "b@test.local")


def test_activation_grants_premium_and_is_idempotent(conn):
    ev = parse_paddle_event(payload())
    assert apply_event(conn, ev, now=NOW) == "applied"
    assert user(conn)["tier"] == "premium"
    assert apply_event(conn, ev, now=NOW) == "duplicate"
    assert conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 1


def test_cancellation_downgrades(conn):
    apply_event(conn, parse_paddle_event(payload()), now=NOW)
    ev = parse_paddle_event(
        payload(
            event_id="evt_2",
            etype="subscription.canceled",
            status="canceled",
            occurred="2026-11-02T10:00:00Z",
        )
    )
    assert apply_event(conn, ev, now=NOW) == "applied"
    assert user(conn)["tier"] == "free"


def test_out_of_order_older_event_is_ignored(conn):
    apply_event(
        conn,
        parse_paddle_event(
            payload(
                event_id="evt_2",
                status="canceled",
                etype="subscription.canceled",
                occurred="2026-11-02T10:00:00Z",
            )
        ),
        now=NOW,
    )
    late = parse_paddle_event(payload(event_id="evt_1", occurred="2026-11-01T10:00:00Z"))
    assert apply_event(conn, late, now=NOW) == "stale"
    assert user(conn)["tier"] == "free"


def test_unknown_user_is_recorded_but_changes_nothing(conn):
    assert apply_event(conn, parse_paddle_event(payload(user_id="999")), now=NOW) == "ignored"
    assert apply_event(conn, parse_paddle_event(payload(user_id="999")), now=NOW) == "duplicate"
    assert conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 0


def test_admin_tier_is_never_changed_by_billing(conn):
    conn.execute("UPDATE users SET is_admin=1, tier='premium' WHERE email='b@test.local'")
    conn.commit()
    apply_event(
        conn, parse_paddle_event(payload(status="canceled", etype="subscription.canceled")), now=NOW
    )
    assert user(conn)["tier"] == "premium"


def test_billing_event_stores_no_payload(conn):
    apply_event(conn, parse_paddle_event(payload()), now=NOW)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(billing_events)")]
    assert not {"payload", "body", "email"} & set(cols)


# --- routes -----------------------------------------------------------------------
@pytest.fixture
def client(conn, monkeypatch):
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[get_current_user] = lambda: {
        "id": 1,
        "email": "b@test.local",
        "tier": "free",
        "audience": "base",
        "is_admin": 0,
        "household_id": None,
    }
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "billing_enabled", True)
    yield TestClient(app)
    app.dependency_overrides.clear()


def post_webhook(client, body: dict, header: str | None = None, ts: int | None = None):
    raw = json.dumps(body).encode()
    h = header if header is not None else sign(raw, ts or int(datetime.now(UTC).timestamp()))
    return client.post(
        "/billing/webhook",
        content=raw,
        headers={"Paddle-Signature": h, "Content-Type": "application/json"},
    )


def test_webhook_disabled_returns_503(client, monkeypatch):
    monkeypatch.setattr(settings, "billing_enabled", False)
    assert post_webhook(client, payload()).status_code == 503


def test_webhook_rejects_bad_signature(client, conn):
    assert post_webhook(client, payload(), header="ts=1;h1=00").status_code == 401
    assert user(conn)["tier"] == "free"


def test_webhook_applies_and_dedups(client, conn):
    assert post_webhook(client, payload()).status_code == 200
    assert user(conn)["tier"] == "premium"
    r = post_webhook(client, payload())
    assert r.status_code == 200 and r.json()["result"] == "duplicate"


def test_webhook_ignores_unsupported_event_with_200(client):
    r = post_webhook(client, {"event_id": "e", "event_type": "transaction.completed", "data": {}})
    assert r.status_code == 200 and r.json()["result"] == "ignored"


def test_status_endpoint(client):
    post_webhook(client, payload())
    body = client.get("/billing/status").json()
    assert body["enabled"] is True
    assert body["subscription"]["status"] == "active"
    assert "provider_customer_id" not in body["subscription"]


def test_checkout_uses_provider_and_never_trusts_client_price(client):
    r = client.post("/billing/checkout-session", json={"plan": "premium_monthly"})
    assert r.status_code == 200 and r.json()["url"].startswith("https://billing.invalid/")
    assert client.post("/billing/checkout-session", json={"plan": "../../evil"}).status_code == 422


@pytest.mark.parametrize("plan", ["expert_monthly", "expert_yearly", "premium_yearly"])
def test_checkout_accepts_expert_and_yearly_plans(client, plan):
    r = client.post("/billing/checkout-session", json={"plan": plan})
    assert r.status_code == 200 and r.json()["url"].endswith(f"plan={plan}")


def test_paddle_provider_needs_a_server_side_price_id(monkeypatch):
    from billing.provider import BillingProviderError, PaddleProvider

    monkeypatch.setattr(settings, "paddle_price_ids", "")
    with pytest.raises(BillingProviderError):
        PaddleProvider().create_checkout(user_id=1, email="a@b.c", plan="expert_monthly")
    monkeypatch.setattr(settings, "paddle_price_ids", "not json")
    with pytest.raises(BillingProviderError):
        PaddleProvider().create_checkout(user_id=1, email="a@b.c", plan="expert_monthly")
    monkeypatch.setattr(settings, "paddle_price_ids", '{"expert_monthly": "pri_x"}')
    with pytest.raises(BillingProviderError, match="not implemented"):
        PaddleProvider().create_checkout(user_id=1, email="a@b.c", plan="expert_monthly")
    with pytest.raises(BillingProviderError, match="no price"):
        PaddleProvider().create_checkout(user_id=1, email="a@b.c", plan="premium_monthly")


def test_checkout_disabled_returns_503(client, monkeypatch):
    monkeypatch.setattr(settings, "billing_enabled", False)
    assert (
        client.post("/billing/checkout-session", json={"plan": "premium_monthly"}).status_code
        == 503
    )


# --- multi-subscription tier + past_due sweep -----------------------------------
def test_second_subscription_keeps_premium_when_first_cancels(conn):
    apply_event(conn, parse_paddle_event(payload(sub="sub_1")), now=NOW)
    apply_event(conn, parse_paddle_event(payload(event_id="evt_b", sub="sub_2")), now=NOW)
    cancel = payload(
        event_id="evt_c",
        etype="subscription.canceled",
        status="canceled",
        sub="sub_1",
        occurred="2026-11-02T10:00:00Z",
    )
    apply_event(conn, parse_paddle_event(cancel), now=NOW)
    assert user(conn)["tier"] == "premium"


def test_sweep_downgrades_expired_past_due(conn):
    ev = payload(status="past_due", etype="subscription.past_due", occurred="2026-11-01T10:00:00Z")
    apply_event(conn, parse_paddle_event(ev), now=NOW)
    assert user(conn)["tier"] == "premium"  # inside grace
    assert sweep_expired(conn, now=NOW + timedelta(days=8), grace_days=7) == 1
    assert user(conn)["tier"] == "free"
    assert sweep_expired(conn, now=NOW + timedelta(days=9), grace_days=7) == 0


def test_sweep_leaves_in_grace_and_active_alone(conn):
    apply_event(conn, parse_paddle_event(payload()), now=NOW)
    assert sweep_expired(conn, now=NOW + timedelta(days=30), grace_days=7) == 0
    assert user(conn)["tier"] == "premium"


def test_sweep_skips_admins(conn):
    ev = payload(status="past_due", etype="subscription.past_due")
    apply_event(conn, parse_paddle_event(ev), now=NOW)
    conn.execute("UPDATE users SET is_admin=1, tier='premium'")
    conn.commit()
    sweep_expired(conn, now=NOW + timedelta(days=30), grace_days=7)
    assert user(conn)["tier"] == "premium"


# --- GDPR: erasure and portability -------------------------------------------------
def test_delete_user_erases_subscriptions(conn):
    apply_event(conn, parse_paddle_event(payload()), now=NOW)
    other = repo.create_user(conn, email="o@test.local", name="O", password_hash="x")
    apply_event(
        conn,
        parse_paddle_event(payload(event_id="evt_o", sub="sub_o", user_id=str(other["id"]))),
        now=NOW,
    )
    repo.delete_user(conn, 1)
    rows = conn.execute("SELECT user_id FROM subscriptions").fetchall()
    assert [r["user_id"] for r in rows] == [other["id"]]


def test_account_export_includes_billing(client):
    post_webhook(client, payload())
    # export contacts the other apps over HTTP; unreachable here, so only the foroba section matters
    body = client.get("/auth/account/export").json()
    sub = body["foroba"]["subscriptions"]
    assert sub[0]["status"] == "active"
    assert "provider_customer_id" in sub[0]


# --- Paddle subscription cancellation (Plan C Task 1) ---
import httpx  # noqa: E402

from billing import provider as billing_provider  # noqa: E402
from billing.provider import BillingProviderError, FakeProvider, PaddleProvider  # noqa: E402


def test_fake_provider_cancel_is_noop():
    FakeProvider().cancel_subscription("sub_123")


def test_paddle_cancel_posts_immediately(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.content
        return httpx.Response(200, json={"data": {"status": "canceled"}})

    monkeypatch.setattr(billing_provider.settings, "paddle_api_key", "test-key")
    monkeypatch.setattr(billing_provider.settings, "paddle_api_base", "https://paddle.test")
    PaddleProvider(transport=httpx.MockTransport(handler)).cancel_subscription("sub_123")
    assert seen["url"] == "https://paddle.test/subscriptions/sub_123/cancel"
    assert seen["auth"] == "Bearer test-key"
    assert b'"effective_from":"immediately"' in seen["body"].replace(b" ", b"")


def test_paddle_cancel_error_raises(monkeypatch):
    monkeypatch.setattr(billing_provider.settings, "paddle_api_key", "test-key")
    transport = httpx.MockTransport(lambda r: httpx.Response(500, json={"error": {}}))
    with pytest.raises(BillingProviderError):
        PaddleProvider(transport=transport).cancel_subscription("sub_123")


def test_paddle_cancel_without_key_raises(monkeypatch):
    monkeypatch.setattr(billing_provider.settings, "paddle_api_key", "")
    with pytest.raises(BillingProviderError):
        PaddleProvider().cancel_subscription("sub_123")


# --- Task 2.7: webhook hardening (status codes, replay, ordering, size cap) -----------
def test_bad_signature_is_401_and_changes_nothing(client, conn):
    assert post_webhook(client, payload(), header="ts=1;h1=00").status_code == 401
    assert user(conn)["tier"] == "free"
    assert conn.execute("SELECT COUNT(*) FROM billing_events").fetchone()[0] == 0


def test_missing_signature_header_is_401(client):
    raw = json.dumps(payload()).encode()
    assert client.post("/billing/webhook", content=raw).status_code == 401


def test_stale_timestamp_is_401_even_with_a_valid_hmac(client, conn):
    old = int((datetime.now(UTC) - timedelta(minutes=6)).timestamp())
    raw = json.dumps(payload()).encode()
    r = post_webhook(client, payload(), header=sign(raw, old))
    assert r.status_code == 401
    assert user(conn)["tier"] == "free"


def test_future_timestamp_beyond_tolerance_is_401(client):
    future = int((datetime.now(UTC) + timedelta(minutes=6)).timestamp())
    raw = json.dumps(payload()).encode()
    assert post_webhook(client, payload(), header=sign(raw, future)).status_code == 401


def test_replayed_event_id_changes_state_once(client, conn):
    assert post_webhook(client, payload()).json()["result"] == "applied"
    assert post_webhook(client, payload()).json()["result"] == "duplicate"
    assert conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM billing_events").fetchone()[0] == 1


def test_updated_arriving_before_created_converges_to_latest_state(conn):
    updated = parse_paddle_event(
        payload(
            event_id="evt_u",
            etype="subscription.updated",
            status="canceled",
            occurred="2026-11-02T10:00:00Z",
        )
    )
    created = parse_paddle_event(
        payload(
            event_id="evt_c",
            etype="subscription.created",
            status="active",
            occurred="2026-11-01T10:00:00Z",
        )
    )
    assert apply_event(conn, updated, now=NOW) == "applied"
    assert apply_event(conn, created, now=NOW) == "stale"
    assert user(conn)["tier"] == "free"
    assert (
        conn.execute(
            "SELECT status FROM subscriptions WHERE provider_subscription_id='sub_1'"
        ).fetchone()[0]
        == "canceled"
    )


def test_unknown_customer_is_200_ledgered_and_harmless(client, conn):
    r = post_webhook(client, payload(user_id="424242"))
    assert r.status_code == 200 and r.json()["result"] == "ignored"
    assert conn.execute("SELECT COUNT(*) FROM billing_events").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 0
    assert user(conn)["tier"] == "free"


def test_oversized_body_is_413_before_signature_work(client, conn):
    big = json.dumps({"pad": "x" * (300 * 1024)}).encode()
    r = client.post(
        "/billing/webhook",
        content=big,
        headers={"Paddle-Signature": sign(big, int(datetime.now(UTC).timestamp()))},
    )
    assert r.status_code == 413
    assert user(conn)["tier"] == "free"


def test_body_just_under_the_cap_is_still_processed(client):
    r = post_webhook(client, {**payload(), "pad": "x" * (100 * 1024)})
    assert r.status_code == 200


def test_event_cannot_rebind_a_subscription_to_another_user(conn):
    repo.create_user(
        conn, email="c@test.local", name="C", password_hash="x", tier="free", audience="base"
    )
    other = repo.get_user_by_email(conn, "c@test.local")["id"]
    assert apply_event(conn, parse_paddle_event(payload()), now=NOW) == "applied"
    hijack = parse_paddle_event(
        payload(event_id="evt_h", user_id=str(other), occurred="2026-11-03T10:00:00Z")
    )
    assert apply_event(conn, hijack, now=NOW) == "ignored"
    assert repo.get_user_by_email(conn, "c@test.local")["tier"] == "free"
    owner = conn.execute(
        "SELECT user_id FROM subscriptions WHERE provider_subscription_id='sub_1'"
    ).fetchone()[0]
    assert owner == user(conn)["id"]


def test_downgrade_keeps_data_and_locks_premium_features(conn):
    """Premium -> free hides premium features but deletes nothing; resubscribing restores them."""
    from auth.entitlements import REGISTRY, evaluate

    uid = user(conn)["id"]
    conn.execute(
        "INSERT INTO whatif_scenarios (created_by_user_id, name, horizon_months, params_json) "
        "VALUES (?, 'Raise', 12, '{}')",
        (uid,),
    )
    conn.execute(
        "INSERT INTO monthly_reports (user_id, year_month, trigger, status, narrative_text) "
        "VALUES (?, '2026-10', 'manual', 'done', 'kept')",
        (uid,),
    )
    conn.commit()
    premium_keys = [k for k, v in REGISTRY.items() if v["tier"] == "premium"]
    assert premium_keys

    def reachable(u):  # premium features the user's audience can see
        return [k for k in premium_keys if evaluate(k, u, gating_enabled=True).visible]

    apply_event(conn, parse_paddle_event(payload()), now=NOW)
    assert all(evaluate(k, user(conn), gating_enabled=True).unlocked for k in reachable(user(conn)))

    cancel = payload(
        event_id="evt_2",
        etype="subscription.canceled",
        status="canceled",
        occurred="2026-11-02T10:00:00Z",
    )
    apply_event(conn, parse_paddle_event(cancel), now=NOW)
    free = user(conn)
    assert free["tier"] == "free"
    assert reachable(free)  # still visible (shown as locked, with the upgrade path) ...
    assert not any(evaluate(k, free, gating_enabled=True).unlocked for k in reachable(free))
    # ... and nothing the user created is gone.
    n_scen = conn.execute(
        "SELECT COUNT(*) FROM whatif_scenarios WHERE created_by_user_id=?", (uid,)
    )
    assert n_scen.fetchone()[0] == 1
    n_rep = conn.execute("SELECT narrative_text FROM monthly_reports WHERE user_id=?", (uid,))
    assert n_rep.fetchone()[0] == "kept"

    resub = payload(event_id="evt_3", sub="sub_2", occurred="2026-11-03T10:00:00Z")
    apply_event(conn, parse_paddle_event(resub), now=NOW)
    assert user(conn)["tier"] == "premium"


# --- Task 5.3: full Paddle lifecycle state machine ---------------------------------
def _tier_after(conn, *steps, now=NOW):
    """Apply (type, status, occurred, extra) steps in order; return the final tier."""
    for i, (etype, status, occurred, extra) in enumerate(steps):
        ev = payload(event_id=f"evt_s{i}", etype=etype, status=status, occurred=occurred, **extra)
        apply_event(conn, parse_paddle_event(ev), now=now)
    return user(conn)["tier"]


D1, D2, D3 = "2026-11-01T10:00:00Z", "2026-11-02T10:00:00Z", "2026-11-03T10:00:00Z"


@pytest.mark.parametrize(
    "steps,expected",
    [
        pytest.param([("subscription.created", "trialing", D1, {})], "premium", id="trialing"),
        pytest.param(
            [
                ("subscription.created", "trialing", D1, {}),
                ("subscription.activated", "active", D2, {}),
            ],
            "premium",
            id="trial-to-active",
        ),
        pytest.param(
            [
                ("subscription.activated", "active", D1, {}),
                ("subscription.past_due", "past_due", D2, {}),
            ],
            "premium",
            id="past-due-inside-grace",
        ),
        pytest.param(
            [
                ("subscription.activated", "active", D1, {}),
                ("subscription.past_due", "past_due", D2, {}),
                ("subscription.activated", "active", D3, {}),
            ],
            "premium",
            id="past-due-recovered",
        ),
        pytest.param(
            [
                ("subscription.activated", "active", D1, {}),
                ("subscription.updated", "active", D2, {"cancel": True}),
            ],
            "premium",
            id="scheduled-cancel-keeps-premium-until-period-end",
        ),
        pytest.param(
            [
                ("subscription.activated", "active", D1, {}),
                ("subscription.updated", "active", D2, {"cancel": True}),
                ("subscription.canceled", "canceled", D3, {}),
            ],
            "free",
            id="scheduled-cancel-then-period-end",
        ),
        pytest.param(
            [
                ("subscription.activated", "active", D1, {}),
                ("subscription.paused", "paused", D2, {}),
            ],
            "free",
            id="paused",
        ),
        pytest.param(
            [
                ("subscription.activated", "active", D1, {}),
                ("subscription.paused", "paused", D2, {}),
                ("subscription.resumed", "active", D3, {}),
            ],
            "premium",
            id="resumed",
        ),
        pytest.param(
            [
                ("subscription.activated", "active", D1, {}),
                ("subscription.canceled", "canceled", D2, {}),
                ("subscription.activated", "active", D3, {"sub": "sub_2"}),
            ],
            "premium",
            id="resubscribe-new-subscription",
        ),
        pytest.param(
            [
                ("subscription.activated", "active", D1, {}),
                ("subscription.updated", "active", D2, {}),
            ],
            "premium",
            id="plan-change-expert-to-base-stays-premium",
        ),
    ],
)
def test_lifecycle_tier(conn, steps, expected):
    assert _tier_after(conn, *steps) == expected


def test_refund_reaches_free_via_canceled_event(conn):
    """Paddle refunds cancel the subscription; the state machine must land on free."""
    tier = _tier_after(
        conn,
        ("subscription.activated", "active", D1, {}),
        ("subscription.canceled", "canceled", D2, {}),
    )
    assert tier == "free"
    row = conn.execute("SELECT status FROM subscriptions").fetchone()
    assert row["status"] == "canceled"


def test_past_due_event_arriving_beyond_grace_is_free(conn):
    ev = payload(status="past_due", etype="subscription.past_due", occurred=D1)
    apply_event(conn, parse_paddle_event(ev), now=NOW + timedelta(days=30), grace_days=7)
    assert user(conn)["tier"] == "free"


def test_scheduled_cancel_with_dropped_final_webhook_is_swept(conn):
    """Paddle's closing `canceled` webhook never arrived: the sweep must still end premium."""
    ev = payload(status="active", etype="subscription.updated", cancel=True, ends=D2)
    apply_event(conn, parse_paddle_event(ev), now=NOW)
    assert user(conn)["tier"] == "premium"
    assert sweep_expired(conn, now=datetime(2026, 11, 2, 10, 5, tzinfo=UTC), grace_days=7) == 0
    assert user(conn)["tier"] == "premium"  # one grace day before we drop it
    assert sweep_expired(conn, now=datetime(2026, 11, 4, 12, 0, tzinfo=UTC), grace_days=7) == 1
    assert user(conn)["tier"] == "free"
