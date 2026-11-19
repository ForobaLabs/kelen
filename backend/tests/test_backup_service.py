"""Tests for backup_service.py (TI-A, foroba#151)."""

from __future__ import annotations

import io
import sqlite3
import tarfile

import pytest

import backup_service


def _real_db_bytes(tmp_path) -> bytes:
    path = tmp_path / f"real-{len(list(tmp_path.glob('real-*.db')))}.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO users DEFAULT VALUES")
    conn.commit()
    conn.close()
    return path.read_bytes()


@pytest.fixture
def paths(tmp_path, monkeypatch):
    db_path = tmp_path / "foroba_identity.db"
    db_path.write_bytes(_real_db_bytes(tmp_path))
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    priv = keys_dir / "jwt_private.pem"
    priv.write_bytes(b"fake-private-key")
    pub = keys_dir / "jwt_public.pem"
    pub.write_bytes(b"fake-public-key")

    monkeypatch.setattr(backup_service.settings, "auth_db_path", str(db_path))
    monkeypatch.setattr(backup_service.settings, "jwt_private_key_path", str(priv))
    monkeypatch.setattr(backup_service.settings, "jwt_public_key_path", str(pub))
    monkeypatch.setattr(backup_service.settings, "db_encryption_key", "")
    return db_path, priv, pub


@pytest.fixture
def make_archive():
    def _make(members: dict[str, bytes]) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, payload in members.items():
                info = tarfile.TarInfo(name=name)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
        return buf.getvalue()

    return _make


def test_export_archive_excludes_private_key_by_default(paths):
    archive = backup_service.export_archive()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        names = set(tar.getnames())
    assert names == {"foroba_identity.db", "jwt_public.pem"}


def test_export_archive_include_keys_adds_private_key(paths):
    archive = backup_service.export_archive(include_keys=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        names = set(tar.getnames())
    assert names == {"foroba_identity.db", "jwt_private.pem", "jwt_public.pem"}


def test_import_archive_restores_files(paths):
    db_path, priv, _pub = paths
    original = db_path.read_bytes()
    archive = backup_service.export_archive(include_keys=True)
    db_path.write_bytes(b"corrupted")
    priv.write_bytes(b"corrupted")

    result = backup_service.import_archive(archive)

    assert set(result["restored_files"]) == {
        "foroba_identity.db",
        "jwt_private.pem",
        "jwt_public.pem",
    }
    assert db_path.read_bytes() == original
    assert priv.read_bytes() == b"fake-private-key"


def test_import_never_overwrites_keys_unless_present(paths, make_archive, tmp_path):
    _db_path, priv, _pub = paths
    result = backup_service.import_archive(
        make_archive({"foroba_identity.db": _real_db_bytes(tmp_path)})
    )
    assert result["restored_files"] == ["foroba_identity.db"]
    assert priv.read_bytes() == b"fake-private-key"


def test_import_archive_rejects_unexpected_filename():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="../etc/passwd")
        payload = b"malicious"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    with pytest.raises(ValueError, match="Unexpected file"):
        backup_service.import_archive(buf.getvalue())


def test_import_rejects_oversized_archive(monkeypatch):
    monkeypatch.setattr(backup_service, "MAX_ARCHIVE_BYTES", 10)
    with pytest.raises(ValueError, match="too large"):
        backup_service.import_archive(b"x" * 11)


def test_import_rejects_oversized_member(monkeypatch, paths, make_archive):
    monkeypatch.setattr(backup_service, "MAX_MEMBER_BYTES", 5)
    with pytest.raises(ValueError, match="too large"):
        backup_service.import_archive(make_archive({"foroba_identity.db": b"0" * 6}))


def test_import_rejects_oversized_key_member(paths, make_archive):
    with pytest.raises(ValueError, match="too large"):
        backup_service.import_archive(make_archive({"jwt_private.pem": b"0" * (64 * 1024 + 1)}))


def test_import_rejects_non_database_bytes(paths, make_archive):
    with pytest.raises(ValueError, match="not a valid"):
        backup_service.import_archive(make_archive({"foroba_identity.db": b"garbage"}))


def test_import_rejects_database_without_users_table(paths, make_archive, tmp_path):
    path = tmp_path / "other.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE unrelated (id INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="not a valid"):
        backup_service.import_archive(make_archive({"foroba_identity.db": path.read_bytes()}))


def test_failed_validation_leaves_live_files_untouched(paths, make_archive):
    db_path, priv, _pub = paths
    before = db_path.read_bytes()
    with pytest.raises(ValueError):
        backup_service.import_archive(
            make_archive({"foroba_identity.db": b"garbage", "jwt_private.pem": b"evil"})
        )
    assert db_path.read_bytes() == before and priv.read_bytes() == b"fake-private-key"


def test_import_rejects_symlink_member(paths):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="jwt_private.pem")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(ValueError, match="non-file"):
        backup_service.import_archive(buf.getvalue())


def test_import_rejects_duplicate_member(paths, tmp_path):
    db = _real_db_bytes(tmp_path)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for _ in range(2):
            info = tarfile.TarInfo(name="foroba_identity.db")
            info.size = len(db)
            tar.addfile(info, io.BytesIO(db))
    with pytest.raises(ValueError, match="Duplicate"):
        backup_service.import_archive(buf.getvalue())


def test_import_rejects_garbage_gzip(paths):
    with pytest.raises(ValueError, match="not a valid tar"):
        backup_service.import_archive(b"not-a-tarball")
