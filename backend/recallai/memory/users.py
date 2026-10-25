"""RecallAI accounts: users, password hashing, sign-in sessions, ownership.

Stored in memory.db next to the sightings (tables `users` and `sessions`,
created by db.init_db).

Passwords: hashlib.scrypt (stdlib) with a random 16-byte salt per user,
compared with hmac.compare_digest. Stored as "scrypt$n$r$p$salt$hash"
(base64), so the cost can be raised later without breaking old hashes.

Sessions: a random `secrets.token_urlsafe(32)` bearer token is returned to
the client once; only its SHA-256 is stored. Sessions expire after
SESSION_DAYS and are revoked on sign-out.

Ownership migration: rows written before accounts existed have
owner_id NULL (and belongings have no "owner_id" in info.json). The FIRST
account ever registered claims all of that ownerless data, in the same
transaction that creates it. Nothing is ever deleted.

CLI:
    python -m recallai.memory.users list
    python -m recallai.memory.users claim <username>   # hand ownerless data to a user
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from recallai.memory import db as dbmod

USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
MIN_PASSWORD = 8
MAX_PASSWORD = 256          # scrypt work is per byte; cap untrusted input
MAX_DISPLAY_NAME = 64
SESSION_DAYS = 30

# scrypt cost: 2**14 * 8 * 128 B = 16 MB of memory, ~50 ms per hash.
SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_LEN = 2 ** 14, 8, 1, 32
SCRYPT_MAXMEM = 64 * 1024 * 1024

OWNED_TABLES = ("sightings", "alerts", "people")


class AccountError(ValueError):
    """A registration/sign-in problem the user can fix. `field` names the
    form field it belongs to (or None for the whole form)."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


def utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def normalize_username(username):
    return (username or "").strip().lower()


def validate_username(username):
    """Error message for a username, or None if it's fine."""
    name = normalize_username(username)
    if not 3 <= len(name) <= 32:
        return "Username must be 3-32 characters."
    if not USERNAME_RE.match(name):
        return ("Username can only use lowercase letters, numbers, "
                "dots, dashes and underscores.")
    return None


def validate_password(password):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    if len(password) > MAX_PASSWORD:
        return f"Password must be at most {MAX_PASSWORD} characters."
    return None


def clean_display_name(display_name, username):
    name = " ".join((display_name or "").split())
    if len(name) > MAX_DISPLAY_NAME:
        raise AccountError(f"Display name must be at most {MAX_DISPLAY_NAME} characters.",
                           "display_name")
    return name or username


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N,
                            r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_LEN,
                            maxmem=SCRYPT_MAXMEM)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password, stored):
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected),
                                maxmem=SCRYPT_MAXMEM)
    except (ValueError, TypeError, AttributeError):
        return False
    return hmac.compare_digest(actual, expected)


# Verified against when the username doesn't exist, so a wrong username
# takes as long as a wrong password (no timing hint that a user exists).
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def public_user(row):
    """The fields safe to send to a browser."""
    if row is None:
        return None
    return {"id": row["id"], "username": row["username"],
            "display_name": row["display_name"], "created_at": row["created_at"]}


def count_users(db_path=None):
    with dbmod.connect(db_path) as db:
        return db.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def get_user(user_id, db_path=None):
    with dbmod.connect(db_path) as db:
        return public_user(db.execute("SELECT * FROM users WHERE id=?",
                                      (user_id,)).fetchone())


def find_user(username, db_path=None):
    with dbmod.connect(db_path) as db:
        return public_user(db.execute("SELECT * FROM users WHERE username=?",
                                      (normalize_username(username),)).fetchone())


def list_users(db_path=None):
    with dbmod.connect(db_path) as db:
        return [public_user(r) for r in db.execute("SELECT * FROM users ORDER BY id")]


def create_user(username, password, display_name="", db_path=None, belongings_dir=None):
    """Create an account. Returns (user, claimed) where `claimed` counts the
    ownerless rows/belongings this account took over (only the first account
    ever registered claims anything). Raises AccountError."""
    name = normalize_username(username)
    problem = validate_username(name)
    if problem:
        raise AccountError(problem, "username")
    problem = validate_password(password)
    if problem:
        raise AccountError(problem, "password")
    display = clean_display_name(display_name, name)
    pw_hash = hash_password(password)
    now = utcnow().isoformat()

    db = dbmod.connect(db_path)
    try:
        # BEGIN IMMEDIATE: "am I the first account?" and the insert happen
        # atomically, so two simultaneous sign-ups can't both claim the data.
        db.isolation_level = None
        db.execute("BEGIN IMMEDIATE")
        try:
            first = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            try:
                cur = db.execute(
                    "INSERT INTO users (username, display_name, password_hash, created_at) "
                    "VALUES (?,?,?,?)", (name, display, pw_hash, now))
            except sqlite3.IntegrityError:
                raise AccountError("That username is taken.", "username") from None
            user_id = cur.lastrowid
            claimed = _claim_rows(db, user_id) if first else {}
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    finally:
        db.close()
    if first:
        claimed["belongings"] = claim_belongings(user_id, belongings_dir)
    return public_user(row), claimed


def authenticate(username, password, db_path=None):
    """The user for a correct username + password, else None. Always runs one
    scrypt, whether or not the username exists."""
    name = normalize_username(username)
    if not isinstance(password, str) or len(password) > MAX_PASSWORD:
        password = ""
    with dbmod.connect(db_path) as db:
        row = db.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
    ok = verify_password(password, row["password_hash"] if row else _DUMMY_HASH)
    return public_user(row) if (row is not None and ok) else None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id, db_path=None, days=SESSION_DAYS, now=None):
    """New bearer token for a user. Returns (token, expires_at_iso)."""
    now = now or utcnow()
    token = secrets.token_urlsafe(32)
    expires = (now + timedelta(days=days)).isoformat()
    with dbmod.connect(db_path) as db:
        db.execute("INSERT INTO sessions (user_id, token_hash, created_at, expires_at) "
                   "VALUES (?,?,?,?)", (user_id, _token_hash(token), now.isoformat(), expires))
        db.commit()
    return token, expires


def user_for_token(token, db_path=None, now=None):
    """The signed-in user for a bearer token, or None if it is unknown,
    expired or revoked."""
    if not token or len(token) > 200:
        return None
    now = now or utcnow()
    with dbmod.connect(db_path) as db:
        row = db.execute(
            "SELECT u.*, s.expires_at AS session_expires FROM sessions s "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.revoked_at IS NULL",
            (_token_hash(token),)).fetchone()
    if row is None:
        return None
    try:
        expires = datetime.fromisoformat(row["session_expires"])
    except (TypeError, ValueError):
        return None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= now:
        return None
    user = public_user(row)
    user["session_expires"] = row["session_expires"]
    return user


def revoke_session(token, db_path=None, now=None):
    """Sign a token out. Returns True if a live session was revoked."""
    if not token:
        return False
    now = now or utcnow()
    with dbmod.connect(db_path) as db:
        cur = db.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=? "
                         "AND revoked_at IS NULL", (now.isoformat(), _token_hash(token)))
        db.commit()
    return cur.rowcount > 0


def prune_sessions(db_path=None, now=None):
    """Delete expired/revoked sessions (housekeeping; not required)."""
    now = (now or utcnow()).isoformat()
    with dbmod.connect(db_path) as db:
        cur = db.execute("DELETE FROM sessions WHERE revoked_at IS NOT NULL "
                         "OR expires_at <= ?", (now,))
        db.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------
def _claim_rows(db, user_id):
    counts = {}
    for table in OWNED_TABLES:
        cur = db.execute(f"UPDATE {table} SET owner_id=? WHERE owner_id IS NULL", (user_id,))
        counts[table] = cur.rowcount
    return counts


def claim_belongings(user_id, belongings_dir=None):
    """Give every ownerless belonging folder to user_id. Returns the count."""
    from recallai.vision.recognition import BELONGINGS_DIR, load_belongings, set_owner
    base = Path(belongings_dir or BELONGINGS_DIR)
    n = 0
    for item in load_belongings(base):
        if item.owner_id is None and set_owner(item.folder, user_id):
            n += 1
    return n


def claim_ownerless(user_id, db_path=None, belongings_dir=None):
    """Hand all ownerless rows and belongings to user_id (idempotent)."""
    with dbmod.connect(db_path) as db:
        counts = _claim_rows(db, user_id)
        db.commit()
    counts["belongings"] = claim_belongings(user_id, belongings_dir)
    return counts


def resolve_cli_owner(username=None, db_path=None):
    """Owner for command-line tools (register.py):
    --user given -> that account (error if unknown); exactly one account ->
    it; no accounts -> None (ownerless, claimed by the first account);
    several accounts -> error asking for --user."""
    if username:
        user = find_user(username, db_path)
        if user is None:
            raise SystemExit(f"No account named '{normalize_username(username)}'. "
                             "Create it in the dashboard first.")
        return user["id"]
    users = list_users(db_path)
    if not users:
        return None
    if len(users) == 1:
        return users[0]["id"]
    names = ", ".join(u["username"] for u in users)
    raise SystemExit(f"Several accounts exist ({names}); pass --user <username>.")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="RecallAI accounts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list accounts")
    claim = sub.add_parser("claim", help="give all ownerless data to an account")
    claim.add_argument("username")
    args = ap.parse_args(argv)
    dbmod.init_db()
    if args.cmd == "list":
        users = list_users()
        if not users:
            print("No accounts yet. Open the dashboard and create one.")
        for u in users:
            print(f"{u['id']:>4}  {u['username']:<32} {u['display_name']}")
        return 0
    user = find_user(args.username)
    if user is None:
        print(f"No account named '{args.username}'.")
        return 1
    counts = claim_ownerless(user["id"])
    print(f"Gave ownerless data to {user['username']}: "
          + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
