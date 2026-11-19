"""Step-up re-authentication for destructive admin endpoints (AW-3, C-2).

The caller must prove both factors again (password + a fresh TOTP code) even though they hold
a valid session: a stolen cookie alone must not be enough to exfiltrate or overwrite the
identity database.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from pydantic import BaseModel

from auth.security import check_password
from twofa.totp import verify_code


class ReauthBody(BaseModel):
    password: str
    totp_code: str


def verify_reauth(user: dict, body: ReauthBody) -> None:
    if not user.get("totp_enabled"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Enable TOTP to use this endpoint")
    secret = user.get("totp_secret")
    if (
        not check_password(body.password, user)
        or not secret
        or not verify_code(secret, body.totp_code)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Re-authentication failed")
