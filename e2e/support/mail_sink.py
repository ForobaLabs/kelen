"""Local SMTP sink for the e2e suite (replaces Mailpit; no Docker needed).

SMTP (STARTTLS, self-signed cert) on SINK_SMTP_PORT, HTTP inspection API on SINK_HTTP_PORT:
  GET    /messages            -> JSON list [{to, from, subject, body}]
  GET    /messages?to=<addr>  -> filtered
  DELETE /messages            -> clear
  DELETE /<app>/account/erase -> 204, recorded (stands in for Naforo/Yiriba erasure in the delete-account cascade)
  GET    /erasures            -> JSON list of <app> names erased, in call order

Run with ~/.pfh/venvs/e2e/bin/python (aiosmtpd 1.4.6).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from email import message_from_bytes
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import ssl
import time

from aiosmtpd.controller import Controller

SMTP_PORT = int(os.environ.get("SINK_SMTP_PORT", "2525"))
HTTP_PORT = int(os.environ.get("SINK_HTTP_PORT", "2580"))

MESSAGES: list[dict] = []
ERASURES: list[str] = []
LOCK = threading.Lock()


class Handler:
    async def handle_DATA(self, server, session, envelope):  # noqa: N802
        msg = message_from_bytes(envelope.content, policy=default)
        body_part = msg.get_body(preferencelist=("plain", "html"))
        body = body_part.get_content() if body_part else ""
        with LOCK:
            MESSAGES.append(
                {
                    "to": ", ".join(envelope.rcpt_tos),
                    "from": envelope.mail_from,
                    "subject": str(msg["Subject"] or ""),
                    "body": body,
                    "ts": time.time(),
                }
            )
        return "250 OK"


class Api(BaseHTTPRequestHandler):
    def _send(self, code: int, payload) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/erasures":
            with LOCK:
                return self._send(200, list(ERASURES))
        if url.path != "/messages":
            return self._send(404, {"error": "not found"})
        to = parse_qs(url.query).get("to", [None])[0]
        with LOCK:
            out = [m for m in MESSAGES if to is None or to.lower() in m["to"].lower()]
        self._send(200, out)

    def do_DELETE(self):  # noqa: N802
        path = urlparse(self.path).path
        if path.endswith("/account/erase"):
            with LOCK:
                ERASURES.append(path.split("/")[1])
            self.send_response(204)
            self.end_headers()
            return
        with LOCK:
            MESSAGES.clear()
            ERASURES.clear()
        self._send(200, {"ok": True})

    def log_message(self, *args):  # silence
        pass


def _self_signed_context() -> ssl.SSLContext:
    d = tempfile.mkdtemp(prefix="mailsink-")
    key, crt = os.path.join(d, "k.pem"), os.path.join(d, "c.pem")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
         "-out", crt, "-days", "2", "-subj", "/CN=localhost"],
        check=True, capture_output=True,
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)
    return ctx


def main() -> None:
    ctrl = Controller(
        Handler(), hostname="127.0.0.1", port=SMTP_PORT,
        tls_context=_self_signed_context(), require_starttls=False,
    )
    ctrl.start()
    print(f"mail sink: smtp={SMTP_PORT} http={HTTP_PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), Api).serve_forever()


if __name__ == "__main__":
    main()
