"""Append-only audit trail of administrator actions (AW-3, M-11).

Never record secrets: `detail` carries only non-sensitive values (e.g. a feature-flag value).
"""

from __future__ import annotations

import sqlite3


def record(
    conn: sqlite3.Connection,
    admin_id: int,
    action: str,
    *,
    target: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO admin_audit_log (admin_user_id, action, target, detail, ip)"
        " VALUES (?, ?, ?, ?, ?)",
        (admin_id, action, target, detail, ip),
    )
    conn.commit()


def list_recent(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT id, admin_user_id, action, target, detail, ip, created_at"
        " FROM admin_audit_log ORDER BY id DESC LIMIT ?",
        (max(1, min(limit, 500)),),
    ).fetchall()
    return [dict(r) for r in rows]
