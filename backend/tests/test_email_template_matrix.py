"""Task 3.3 (Brevo): every transactional template renders in EN/IT/FR, carries the code,
is branded Kelen, and an SMTP outage never raises into the request path."""
from __future__ import annotations

import email as email_lib
import smtplib
from email import policy
from typing import ClassVar

import pytest

import email_service
from config import settings

SENDERS = {
    "otp": lambda to, cc: email_service.send_otp_email(to, "123456", country=cc),
    "verify": lambda to, cc: email_service.send_signup_verify_email(to, "123456", country=cc),
    "email_change": lambda to, cc: email_service.send_email_change_email(to, "123456", country=cc),
    "invite": lambda to, cc: email_service.send_household_invite_email(
        to, "123456", inviter_name="Awa", country=cc
    ),
}
COUNTRIES = {"en": "GB", "it": "IT", "fr": "FR"}


class _Capture:
    sent: ClassVar[list[tuple]] = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *e):
        return False

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def sendmail(self, frm, to, msg):
        _Capture.sent.append((frm, to, msg))


@pytest.fixture(autouse=True)
def _smtp(monkeypatch):
    _Capture.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _Capture)
    monkeypatch.setattr(settings, "smtp_enabled", True)
    monkeypatch.setattr(settings, "smtp_from", "noreply@forobalabs.com")
    monkeypatch.setattr(settings, "smtp_user", "")


def _parse():
    frm, _, raw = _Capture.sent[-1]
    msg = email_lib.message_from_string(raw, policy=policy.default)
    body = "".join(
        part.get_content() for part in msg.walk() if part.get_content_type().startswith("text/")
    )
    return frm, msg["Subject"], body


@pytest.mark.parametrize("kind", SENDERS)
@pytest.mark.parametrize("lang", COUNTRIES)
def test_template_renders_in_every_language(kind, lang):
    SENDERS[kind]("u@example.com", COUNTRIES[lang])
    frm, subject, body = _parse()
    assert frm == "noreply@forobalabs.com"
    assert subject
    assert "{" not in subject and "{" not in body and "}" not in body, "unresolved placeholder"
    assert "123456" in body
    assert "Kelen" in body or "Kelen" in subject
    assert "Foroba" not in subject.replace("Foroba Labs", "")


@pytest.mark.parametrize("kind", SENDERS)
def test_subjects_are_localised(kind):
    subjects = {}
    for lang, cc in COUNTRIES.items():
        SENDERS[kind]("u@example.com", cc)
        subjects[lang] = _parse()[1]
    assert len(set(subjects.values())) == 3, subjects


@pytest.mark.parametrize("exc", [smtplib.SMTPServerDisconnected("down"), OSError("timeout")])
def test_provider_outage_never_raises(monkeypatch, exc):
    class Down(_Capture):
        def sendmail(self, *a):
            raise exc

    monkeypatch.setattr(smtplib, "SMTP", Down)
    for send in SENDERS.values():
        send("u@example.com", "IT")  # must not raise


def test_unknown_country_falls_back_to_english():
    SENDERS["otp"]("u@example.com", "ZZ")
    assert "verification code" in _parse()[1].lower()
