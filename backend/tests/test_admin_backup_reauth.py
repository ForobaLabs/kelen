"""AW-3 / C-2: backup endpoints need admin + password + TOTP re-auth, ship ciphertext only,
are rate limited and leave an admin audit trail."""

from __future__ import annotations

import io
import tarfile

import pyotp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import admin_audit
import admin_router
import backup_service
from admin_router import router as admin_router_obj
from auth import repository as repo
from auth.conn import get_conn
from auth.dependencies import get_current_user
from backup_orchestrator import decrypt_archive
from db import connect, seed_mock_users
from settings_store import init_settings
from twofa import totp as totp_mod

PASSPHRASE = "correct horse battery staple"
ADMIN_PASSWORD = "Admin1234!"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    conn = connect(str(tmp_path / "test.db"))
    seed_mock_users(conn)
    init_settings(conn)
    admin_id = conn.execute("SELECT id FROM users WHERE email='admin@foroba.app'").fetchone()[0]
    secret = pyotp.random_base32()
    repo.set_totp_secret(conn, admin_id, secret)
    repo.enable_totp(conn, admin_id)
    admin = repo.get_user_by_id(conn, admin_id)
    regular = repo.get_user_by_id(
        conn, conn.execute("SELECT id FROM users WHERE email='demo@foroba.app'").fetchone()[0]
    )

    # a real (tiny) archive source so export has something to bundle
    db_file = tmp_path / "foroba_identity.db"
    db_file.write_bytes(b"SQLite format 3\x00fake")
    priv = tmp_path / "jwt_private.pem"
    priv.write_bytes(b"private")
    pub = tmp_path / "jwt_public.pem"
    pub.write_bytes(b"public")
    monkeypatch.setattr(backup_service.settings, "auth_db_path", str(db_file))
    monkeypatch.setattr(backup_service.settings, "jwt_private_key_path", str(priv))
    monkeypatch.setattr(backup_service.settings, "jwt_public_key_path", str(pub))

    app = FastAPI()
    app.include_router(admin_router_obj)
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[get_current_user] = lambda: admin
    admin_router.limiter.reset()
    return TestClient(app), conn, admin, regular, secret


def fresh_code(secret: str) -> str:
    totp_mod._last_step.clear()  # each call is a new TOTP use; avoid same-window replay lock
    return pyotp.TOTP(secret).now()


def body(secret, **over):
    base = {
        "password": ADMIN_PASSWORD,
        "totp_code": fresh_code(secret),
        "passphrase": PASSPHRASE,
    }
    base.update(over)
    return base


def test_export_get_is_gone(setup):
    tc, *_ = setup
    assert tc.get("/admin/backup/export").status_code == 405


def test_export_requires_admin(setup):
    tc, _c, _a, regular, secret = setup
    tc.app.dependency_overrides[get_current_user] = lambda: regular
    assert tc.post("/admin/backup/export", json=body(secret)).status_code == 403


def test_export_wrong_password_401(setup):
    tc, _c, _a, _r, secret = setup
    resp = tc.post("/admin/backup/export", json=body(secret, password="nope-nope"))
    assert resp.status_code == 401


def test_export_wrong_totp_401(setup):
    tc, _c, _a, _r, secret = setup
    resp = tc.post("/admin/backup/export", json=body(secret, totp_code="000000"))
    assert resp.status_code == 401


def test_export_admin_without_totp_403(setup):
    tc, conn, admin, _r, secret = setup
    repo.disable_totp(conn, admin["id"])
    tc.app.dependency_overrides[get_current_user] = lambda: repo.get_user_by_id(conn, admin["id"])
    resp = tc.post("/admin/backup/export", json=body(secret))
    assert resp.status_code == 403


def test_export_short_passphrase_422(setup):
    tc, _c, _a, _r, secret = setup
    resp = tc.post("/admin/backup/export", json=body(secret, passphrase="short"))
    assert resp.status_code == 422


def test_export_returns_ciphertext(setup):
    tc, _c, _a, _r, secret = setup
    resp = tc.post("/admin/backup/export", json=body(secret))
    assert resp.status_code == 200
    assert not resp.content.startswith(b"\x1f\x8b")  # not a bare gzip
    plain = decrypt_archive(resp.content, PASSPHRASE)
    names = tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz").getnames()
    assert "foroba_identity.db" in names and "jwt_private.pem" not in names


def test_export_include_keys_adds_private_key(setup):
    tc, _c, _a, _r, secret = setup
    resp = tc.post("/admin/backup/export", json=body(secret, include_keys=True))
    plain = decrypt_archive(resp.content, PASSPHRASE)
    names = tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz").getnames()
    assert "jwt_private.pem" in names


def test_every_backup_call_is_audited(setup):
    tc, conn, admin, _r, secret = setup
    tc.post("/admin/backup/export", json=body(secret))
    rows = admin_audit.list_recent(conn)
    assert rows[0]["action"] == "backup.export" and rows[0]["admin_user_id"] == admin["id"]


def test_failed_reauth_is_audited(setup):
    tc, conn, _a, _r, secret = setup
    tc.post("/admin/backup/export", json=body(secret, password="nope-nope"))
    assert admin_audit.list_recent(conn)[0]["action"] == "backup.export.denied"


def test_audit_never_stores_passphrase_or_password(setup):
    tc, conn, _a, _r, secret = setup
    tc.post("/admin/backup/export", json=body(secret))
    blob = repr(admin_audit.list_recent(conn))
    assert PASSPHRASE not in blob and ADMIN_PASSWORD not in blob


def test_export_is_rate_limited_3_per_hour(setup):
    tc, _c, _a, _r, secret = setup
    codes = [tc.post("/admin/backup/export", json=body(secret)).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_export_suite_requires_reauth(setup, monkeypatch):
    tc, _c, _a, _r, secret = setup

    async def fake(passphrase, cookie, stepup=None):
        return b"enc"

    monkeypatch.setattr(admin_router.backup_orchestrator, "export_suite", fake)
    bad = tc.post("/admin/backup/export-suite", json=body(secret, password="nope-nope"))
    assert bad.status_code == 401
    ok = tc.post("/admin/backup/export-suite", json=body(secret))
    assert ok.status_code == 200 and ok.content == b"enc"


def test_import_suite_requires_reauth(setup, monkeypatch):
    tc, _c, _a, _r, secret = setup

    async def fake(data, passphrase, cookie, stepup=None):
        return {"foroba": {}}

    monkeypatch.setattr(admin_router.backup_orchestrator, "import_suite", fake)
    files = {"file": ("b.pfhbak", b"x", "application/octet-stream")}
    form = dict(body(secret))
    bad = tc.post("/admin/backup/import-suite", data={**form, "password": "nope-nope"}, files=files)
    assert bad.status_code == 401
    form["totp_code"] = fresh_code(secret)
    ok = tc.post("/admin/backup/import-suite", data=form, files=files)
    assert ok.status_code == 200


def test_import_over_cap_is_413(setup, monkeypatch):
    tc, _c, _a, _r, secret = setup
    monkeypatch.setattr(admin_router.backup_service, "MAX_ARCHIVE_BYTES", 10)
    files = {"file": ("b.tar.gz", b"x" * 64, "application/gzip")}
    resp = tc.post("/admin/backup/import", data=body(secret), files=files)
    assert resp.status_code == 413


def test_audit_log_endpoint_admin_only(setup):
    tc, _c, _a, regular, secret = setup
    tc.post("/admin/backup/export", json=body(secret))
    assert tc.get("/admin/audit-log").json()[0]["action"] == "backup.export"
    tc.app.dependency_overrides[get_current_user] = lambda: regular
    assert tc.get("/admin/audit-log").status_code == 403


def test_import_suite_wrong_passphrase_is_400(setup, monkeypatch):
    tc, _c, _a, _r, secret = setup

    async def fake(data, passphrase, cookie, stepup=None):
        raise admin_router.backup_orchestrator.InvalidPassphrase("bad pw")

    monkeypatch.setattr(admin_router.backup_orchestrator, "import_suite", fake)
    files = {"file": ("b.pfhbak", b"x", "application/octet-stream")}
    resp = tc.post("/admin/backup/import-suite", data=body(secret), files=files)
    assert resp.status_code == 400


def test_export_suite_without_stepup_is_409(setup):
    tc, _c, _a, _r, secret = setup
    resp = tc.post("/admin/backup/export-suite", json=body(secret))
    assert resp.status_code == 409
    assert "step-up" in resp.json()["detail"].lower()
