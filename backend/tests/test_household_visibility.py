"""AW-6 / H-18 household case: a household mate sees another member's data only for resource
types that member explicitly shared (default private), per resource type, and loses it on leave."""

from __future__ import annotations

import sqlite3

import pytest

from auth import repository as repo
from db import init_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


def _user(conn, email):
    return repo.create_user(conn, email=email, name=email, password_hash="x")["id"]


def _household(conn):
    a, b = _user(conn, "a@example.com"), _user(conn, "b@example.com")
    stranger = _user(conn, "s@example.com")
    repo.set_household_id(conn, a, "h1")
    repo.set_household_id(conn, b, "h1")
    return a, b, stranger


def test_private_by_default_even_inside_a_household(conn):
    _a, b, _ = _household(conn)
    assert repo.get_visible_owner_ids(conn, b, "accounts") == [b]


def test_sharing_on_grants_only_that_resource_type(conn):
    a, b, _ = _household(conn)
    repo.set_share_preference(conn, a, "accounts", True)
    assert repo.get_visible_owner_ids(conn, b, "accounts") == sorted([a, b])
    assert repo.get_visible_owner_ids(conn, b, "debts") == [b]


def test_sharing_is_one_directional(conn):
    a, _b, _ = _household(conn)
    repo.set_share_preference(conn, a, "accounts", True)
    assert repo.get_visible_owner_ids(conn, a, "accounts") == [a]


def test_non_member_never_sees_shared_data(conn):
    a, _, stranger = _household(conn)
    repo.set_share_preference(conn, a, "accounts", True)
    assert repo.get_visible_owner_ids(conn, stranger, "accounts") == [stranger]


def test_sharing_off_again_and_leave_revoke_access(conn):
    a, b, _ = _household(conn)
    repo.set_share_preference(conn, a, "accounts", True)
    repo.set_share_preference(conn, a, "accounts", False)
    assert repo.get_visible_owner_ids(conn, b, "accounts") == [b]
    repo.set_share_preference(conn, a, "accounts", True)
    repo.set_household_id(conn, b, None)
    assert repo.get_visible_owner_ids(conn, b, "accounts") == [b]
