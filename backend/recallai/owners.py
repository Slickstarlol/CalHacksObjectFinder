"""Who owns a row: helpers shared by every query that must stay per user.

Belongings (info.json "owner_id"), sightings, alerts and people each carry
an ``owner_id`` (the ``users.id`` of the account that owns them; NULL/None
means "ownerless": legacy data the first account claims, see
recallai.memory.users.claim_ownerless).

Functions that read user data take ``owner_id=ANY``. ``ANY`` means "do not
filter" and is only for trusted, single-user code paths (CLI tools, the
camera process, the watcher's global sweep, older tests). The web server
always passes the signed-in user's id, so a request can never see another
user's rows, and the id never comes from the model or the request body.
"""

from __future__ import annotations


class _AnyOwner:
    """Sentinel: no owner filter."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self):
        return "ANY_OWNER"

    def __bool__(self):
        return False


ANY = _AnyOwner()


def is_any(owner_id) -> bool:
    return owner_id is ANY


def normalize(owner_id):
    """int user id, None (ownerless) or ANY. Accepts "3" from JSON."""
    if owner_id is ANY or owner_id is None:
        return owner_id
    if isinstance(owner_id, bool):
        raise TypeError("owner_id must be a user id, not a bool")
    return int(owner_id)


def clause(owner_id, column="owner_id"):
    """SQL fragment + params restricting `column` to this owner.

    ANY -> no restriction; None -> ownerless rows only; id -> that user's."""
    if owner_id is ANY:
        return "1=1", []
    if owner_id is None:
        return f"{column} IS NULL", []
    return f"{column} = ?", [normalize(owner_id)]


def matches(row_owner, owner_id) -> bool:
    """Does a row whose owner is `row_owner` belong to `owner_id`?"""
    if owner_id is ANY:
        return True
    if row_owner is None or owner_id is None:
        return row_owner is None and owner_id is None
    try:
        return int(row_owner) == int(owner_id)
    except (TypeError, ValueError):
        return False


def owned(items, owner_id, attr="owner_id"):
    """Filter objects (e.g. Belonging) by their owner attribute."""
    if owner_id is ANY:
        return list(items)
    return [i for i in items if matches(getattr(i, attr, None), owner_id)]


def as_key(owner_id) -> str:
    """Owner as the string SpacetimeDB mirrors ("" = ownerless)."""
    if owner_id is None or owner_id is ANY:
        return ""
    return str(int(owner_id))
