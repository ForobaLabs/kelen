"""Regression tests for foroba#170: concurrent authenticated requests
intermittently returning 401/500 because `auth/conn.py::get_conn()` shared
one sqlite3.Connection across threadpool worker threads (or, in an
interim fix, raced on Alembic's shared migration state when several
threads created their first connection at the same time).
"""

import concurrent.futures
import importlib
import threading

import pytest


@pytest.fixture
def conn_module(tmp_path, monkeypatch):
    """Reload auth.conn against an isolated, real (file-backed) db path so
    each test starts with fresh thread-local/lock state."""
    db_path = str(tmp_path / "test.db")
    import config

    monkeypatch.setattr(config.settings, "auth_db_path", db_path, raising=False)

    import auth.conn as conn_mod

    importlib.reload(conn_mod)
    yield conn_mod
    importlib.reload(conn_mod)


def test_get_conn_gives_each_thread_its_own_connection(conn_module):
    ids: dict[int, int] = {}
    # Keep every connection object alive for the duration of the test: comparing
    # by id() alone is unsafe if a thread's connection is garbage-collected
    # before the comparison, since CPython can then reuse the same memory
    # address for a different object and produce a false id() collision.
    keepalive: list[object] = []

    def worker():
        conn = conn_module.get_thread_conn()
        keepalive.append(conn)
        ids[threading.get_ident()] = id(conn)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ids) == 5
    assert len(set(ids.values())) == 5, "each thread must get its own connection object"


def test_get_conn_same_thread_reuses_connection(conn_module):
    first = conn_module.get_thread_conn()
    second = conn_module.get_thread_conn()
    assert first is second


def test_concurrent_first_connection_creation_does_not_race(conn_module):
    """Many threads creating their connection for the very first time at
    once must not crash (this is what reproduced the Alembic
    `KeyError: 'script'` race before `_connect_lock` was added)."""
    errors: list[BaseException] = []

    def worker():
        try:
            conn = conn_module.get_thread_conn()
            conn.execute("SELECT 1").fetchone()
        except BaseException as exc:
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(lambda _: worker(), range(16)))

    assert not errors, f"concurrent first-connection creation raised: {errors}"


# ── Per-request exclusive connections (Phase 6 load test finding) ──────────────
# FastAPI runs a sync `Depends` and the sync endpoint on *different* threadpool
# threads, so a thread-local connection handed out by the dependency was used by
# two request threads at once ("no more rows available", "cannot commit - no
# transaction is active", 500s under concurrent logins). The dependency must hand
# out a connection no other in-flight request holds.


def test_dependency_hands_out_exclusive_connections(conn_module):
    first, second = conn_module.get_conn(), conn_module.get_conn()
    c1, c2 = next(first), next(second)
    assert c1 is not c2, "two in-flight requests must never share a connection"
    first.close()  # request finished -> connection returns to the pool
    third = conn_module.get_conn()
    assert next(third) is c1, "released connections are reused, not leaked"
    second.close()
    third.close()


def test_dependency_rolls_back_uncommitted_work_on_release(conn_module):
    gen = conn_module.get_conn()
    conn = next(gen)
    conn.execute("CREATE TABLE IF NOT EXISTS _t (x INTEGER)")
    conn.commit()
    conn.execute("INSERT INTO _t VALUES (1)")
    gen.close()
    again = next(conn_module.get_conn())
    assert again.execute("SELECT COUNT(*) FROM _t").fetchone()[0] == 0


def test_concurrent_requests_never_share_a_connection(conn_module):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    in_flight: set[int] = set()
    guard = threading.Lock()
    clashes: list[int] = []

    @app.get("/x")
    def x(conn=Depends(conn_module.get_conn)):
        with guard:
            if id(conn) in in_flight:
                clashes.append(id(conn))
            in_flight.add(id(conn))
        try:
            conn.execute("SELECT 1").fetchone()
            threading.Event().wait(0.02)
        finally:
            with guard:
                in_flight.discard(id(conn))
        return {"ok": True}

    client = TestClient(app)
    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as ex:
        codes = list(ex.map(lambda _: client.get("/x").status_code, range(96)))
    assert set(codes) == {200}
    assert not clashes
