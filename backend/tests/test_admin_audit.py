"""AW-3 / M-11: every mutating admin route leaves an admin_audit_log row."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import admin_audit
import admin_router
from admin_router import router as admin_router_obj
from auth.conn import get_conn
from auth.dependencies import get_current_user
from db import connect, seed_mock_users
from settings_store import init_settings


@pytest.fixture
def setup(tmp_path, monkeypatch):
    conn = connect(str(tmp_path / "test.db"))
    seed_mock_users(conn)
    init_settings(conn)
    admin = dict(conn.execute("SELECT * FROM users WHERE email='admin@foroba.app'").fetchone())
    other = dict(conn.execute("SELECT * FROM users WHERE email='demo@foroba.app'").fetchone())
    monkeypatch.setattr(admin_router, "_invalidate_downstream_settings_caches", lambda: None)
    app = FastAPI()
    app.include_router(admin_router_obj)
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[get_current_user] = lambda: admin
    admin_router.limiter.reset()
    return TestClient(app), conn, admin, other


def last(conn):
    return admin_audit.list_recent(conn, 1)[0]


def test_settings_update_is_audited(setup):
    tc, conn, admin, _ = setup
    assert (
        tc.patch("/admin/settings/premium_gating_enabled", json={"value": "true"}).status_code
        == 200
    )
    row = last(conn)
    assert (row["action"], row["target"], row["detail"]) == (
        "settings.update",
        "premium_gating_enabled",
        "true",
    )
    assert row["admin_user_id"] == admin["id"]


def test_user_disable_and_enable_are_audited(setup):
    tc, conn, _admin, other = setup
    tc.patch(f"/admin/users/{other['id']}/disabled", json={"disabled": True})
    assert (last(conn)["action"], last(conn)["target"]) == ("user.disable", str(other["id"]))
    tc.patch(f"/admin/users/{other['id']}/disabled", json={"disabled": False})
    assert last(conn)["action"] == "user.enable"


def test_invite_create_and_revoke_are_audited(setup):
    tc, conn, _admin, _ = setup
    created = tc.post("/admin/invite-codes", json={"note": "beta", "max_uses": 1})
    assert created.status_code == 201
    code_id = created.json()["id"]
    row = last(conn)
    assert (row["action"], row["target"]) == ("invite.create", str(code_id))
    assert created.json()["code"] not in repr(row)  # the code itself is a secret
    assert tc.delete(f"/admin/invite-codes/{code_id}").status_code == 204
    assert (last(conn)["action"], last(conn)["target"]) == ("invite.revoke", str(code_id))


def test_failed_mutation_is_not_audited(setup):
    tc, conn, _admin, _ = setup
    tc.patch("/admin/settings/not_a_key", json={"value": "true"})
    assert admin_audit.list_recent(conn) == []
