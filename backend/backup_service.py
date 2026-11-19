"""Admin-only backup/restore of Foroba's own critical files (TI-A, foroba#151).

Exports the SQLite identity DB (+ the RSA JWT keypair on request) as a tar.gz. Import
validates the archive only contains the exact expected filenames (whitelist —
also the path-traversal guard), takes a timestamped safety copy of whatever is
currently on disk, then atomically replaces each file.
"""

from __future__ import annotations

import io
import os
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from config import settings

_DB_ARCNAME = "foroba_identity.db"
_PRIVATE_KEY_ARCNAME = "jwt_private.pem"
_PUBLIC_KEY_ARCNAME = "jwt_public.pem"


def _file_map() -> dict[str, Path]:
    return {
        _DB_ARCNAME: Path(settings.auth_db_path).resolve(),
        _PRIVATE_KEY_ARCNAME: Path(settings.jwt_private_key_path).resolve(),
        _PUBLIC_KEY_ARCNAME: Path(settings.jwt_public_key_path).resolve(),
    }


MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_MEMBER_BYTES = 1024 * 1024 * 1024
MAX_KEY_BYTES = 64 * 1024


def export_archive(*, include_keys: bool = False) -> bytes:
    """tar.gz of the identity DB. The JWT signing keys are included only on request
    (`include_keys`): a routine export must not carry the private key (AW-3, C-2)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for arcname, path in _file_map().items():
            if arcname == _PRIVATE_KEY_ARCNAME and not include_keys:
                continue
            if path.is_file():
                tar.add(path, arcname=arcname)
    return buf.getvalue()


def _validate_db(content: bytes) -> None:
    """Refuse anything that is not a readable Foroba identity DB before it replaces the live one.

    Opens the staged bytes exactly as the app opens the live DB (SQLCipher when
    `db_encryption_key` is set) so random bytes or a wrong-key file are caught pre-swap.
    """
    key = settings.db_encryption_key
    fd, name = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        if key:
            import sqlcipher3.dbapi2 as sqlcipher

            conn = sqlcipher.connect(name)
            conn.execute("PRAGMA key = '" + key.replace("'", "''") + "'")
        else:
            conn = sqlite3.connect(name)
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()
            conn.execute("SELECT 1 FROM users LIMIT 1").fetchall()
        finally:
            conn.close()
    except Exception as exc:  # sqlite3/sqlcipher raise DatabaseError subclasses
        raise ValueError(
            f"Archive {_DB_ARCNAME} is not a valid Foroba database for this installation"
        ) from exc
    finally:
        os.unlink(name)
    if not ok or ok[0] != "ok":
        raise ValueError(f"Archive {_DB_ARCNAME} is not a valid Foroba database")


def import_archive(data: bytes) -> dict:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Archive too large")
    file_map = _file_map()
    extracted: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar:  # lazy: an oversized header aborts before its data is read
                if member.name not in file_map:
                    raise ValueError(f"Unexpected file in archive: {member.name!r}")
                if not member.isfile():
                    raise ValueError(f"Unexpected non-file entry: {member.name!r}")
                if member.name in extracted:
                    raise ValueError(f"Duplicate entry in archive: {member.name!r}")
                cap = MAX_MEMBER_BYTES if member.name == _DB_ARCNAME else MAX_KEY_BYTES
                if member.size > cap:
                    raise ValueError(f"Archive member too large: {member.name!r}")
                extracted[member.name] = tar.extractfile(member).read(member.size)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ValueError("Archive is not a valid tar.gz") from exc

    if _DB_ARCNAME in extracted:
        _validate_db(extracted[_DB_ARCNAME])

    restored: list[str] = []
    timestamp = int(time.time())
    for arcname, content in extracted.items():
        target = file_map[arcname]
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            backup_path = target.with_name(f"{target.name}.pre-restore-{timestamp}.bak")
            backup_path.write_bytes(target.read_bytes())
        tmp_path = target.with_name(target.name + ".tmp")
        tmp_path.write_bytes(content)
        os.replace(tmp_path, target)
        restored.append(arcname)
    return {"restored_files": restored, "restart_required": True}
