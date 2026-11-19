"""admin_audit_log — append-only trail of admin actions (AW-3, M-11). Mirrors db.init_schema.

Revision ID: 0031_admin_audit_log
Revises: 0030_email_otp_attempts
"""

from __future__ import annotations

from alembic import op

revision = "0031_admin_audit_log"
down_revision = "0030_email_otp_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            target TEXT,
            detail TEXT,
            ip TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_log (created_at)")


def downgrade() -> None:
    raise NotImplementedError("forward-only — see spec Non-goals")
