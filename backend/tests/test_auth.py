"""Accounts: register / login / logout / me, validation, sessions, throttling."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from recallai.api import auth
from recallai.memory import db as dbmod
from recallai.memory import users
from conftest import PASSWORD, auth_headers, sign_up


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "m.db")
    dbmod.init_db()
    from recallai.api import server
    return TestClient(server.app)


# ------------------------------------------------------------ happy path
def test_register_signs_in_and_me_returns_the_user(client):
    data = sign_up(client, "Kent", display_name="  Kent   Harsono ")
    assert data["token_type"] == "bearer" and len(data["token"]) >= 40
    assert data["user"]["username"] == "kent"           # stored lowercase
    assert data["user"]["display_name"] == "Kent Harsono"
    assert "password_hash" not in data["user"]
    me = client.get("/api/auth/me", headers=auth_headers(data["token"]))
    assert me.status_code == 200
    assert me.json()["username"] == "kent" and me.json()["id"] == data["user"]["id"]
    assert me.json()["session_expires"] == data["expires_at"]


def test_display_name_defaults_to_username(client):
    r = client.post("/api/auth/register", json={"username": "maya", "password": PASSWORD})
    assert r.status_code == 201 and r.json()["user"]["display_name"] == "maya"


def test_login_logout_cycle(client):
    sign_up(client, "kent")
    r = client.post("/api/auth/login", json={"username": "KENT ", "password": PASSWORD})
    assert r.status_code == 200
    token = r.json()["token"]
    h = auth_headers(token)
    assert client.get("/api/auth/me", headers=h).status_code == 200
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    # revoked: the same token no longer works, anywhere
    assert client.get("/api/auth/me", headers=h).status_code == 401
    assert client.get("/api/belongings", headers=h).status_code == 401
    assert client.post("/api/auth/logout", headers=h).status_code == 401


def test_each_login_is_its_own_session(client):
    first = sign_up(client, "kent")["token"]
    second = client.post("/api/auth/login",
                         json={"username": "kent", "password": PASSWORD}).json()["token"]
    assert first != second
    client.post("/api/auth/logout", headers=auth_headers(second))
    assert client.get("/api/auth/me", headers=auth_headers(first)).status_code == 200


# ------------------------------------------------------------ failures
def test_wrong_password_and_unknown_user_look_the_same(client):
    sign_up(client, "kent")
    bad = client.post("/api/auth/login", json={"username": "kent", "password": "nope-nope-nope"})
    ghost = client.post("/api/auth/login", json={"username": "ghost", "password": PASSWORD})
    assert bad.status_code == ghost.status_code == 401
    assert bad.json() == ghost.json() == {"detail": "Incorrect username or password."}
    assert bad.headers["www-authenticate"] == "Bearer"


def test_duplicate_username_case_insensitive(client):
    sign_up(client, "kent")
    r = client.post("/api/auth/register", json={"username": "KeNt", "password": PASSWORD})
    assert r.status_code == 409
    assert r.json()["detail"]["field"] == "username"
    assert "taken" in r.json()["detail"]["message"]


@pytest.mark.parametrize("username,password,field", [
    ("ab", PASSWORD, "username"),                 # too short
    ("a" * 33, PASSWORD, "username"),             # too long
    ("kent harsono", PASSWORD, "username"),       # space
    ("kent!", PASSWORD, "username"),              # symbol
    ("kënt", PASSWORD, "username"),               # non-ascii
    ("kent", "short", "password"),                # < 8 chars
    ("kent", "", "password"),
])
def test_registration_validation(client, username, password, field):
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == field
    assert users.count_users() == 0


def test_valid_username_characters(client):
    for name in ("abc", "a.b-c_d", "k3nt", "x" * 32):
        assert client.post("/api/auth/register",
                           json={"username": name, "password": PASSWORD}).status_code == 201


@pytest.mark.parametrize("header", [None, "Bearer", "Bearer not-a-real-token", "Basic abc"])
def test_bad_or_missing_token_is_401(client, header):
    sign_up(client, "kent")
    h = {"Authorization": header} if header else {}
    r = client.get("/api/auth/me", headers=h)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


PROTECTED = [
    ("get", "/api/auth/me"), ("post", "/api/auth/logout"),
    ("get", "/api/belongings"), ("get", "/api/belongings/bag1/last-seen"),
    ("get", "/api/objects"), ("get", "/api/sightings?q=bag"),
    ("get", "/api/sightings/recent"), ("get", "/api/sightings/latest?q=bag"),
    ("get", "/api/people"), ("post", "/api/people"), ("put", "/api/people/1"),
    ("delete", "/api/people/1"), ("post", "/api/ask"), ("get", "/api/alerts"),
    ("post", "/api/alerts/1/dismiss"), ("get", "/api/live"),
    ("get", "/api/live/frame.jpg"), ("get", "/api/live/stream"),
    ("get", "/api/enroll"), ("post", "/api/enroll/start"), ("post", "/api/enroll/cancel"),
    ("post", "/gps"), ("get", "/gps/latest"), ("get", "/snapshots/a.jpg"),
]


@pytest.mark.parametrize("method,url", PROTECTED)
def test_user_data_endpoints_need_a_token(client, method, url):
    body = {"json": {"name": "x", "question": "x", "latitude": 1, "longitude": 1,
                     "accuracy": 1}} if method in ("post", "put") else {}
    r = getattr(client, method)(url, **body)
    assert r.status_code == 401, (method, url, r.status_code)


def test_public_pages_stay_public(client):
    for url in ("/", "/search", "/auth.js"):
        assert client.get(url).status_code == 200
    assert "RecallAuth" in client.get("/auth.js").text


# ------------------------------------------------------------ sessions
def test_expired_session_is_rejected(client):
    user = sign_up(client, "kent")["user"]
    long_ago = datetime.now(timezone.utc) - timedelta(days=31)
    token, expires = users.create_session(user["id"], now=long_ago)
    assert datetime.fromisoformat(expires) < datetime.now(timezone.utc)
    assert users.user_for_token(token) is None
    r = client.get("/api/auth/me", headers=auth_headers(token))
    assert r.status_code == 401 and "expired" in r.json()["detail"]
    fresh, _ = users.create_session(user["id"])
    assert users.user_for_token(fresh)["username"] == "kent"
    # expires exactly SESSION_DAYS after creation
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _, exp = users.create_session(user["id"], now=now)
    assert datetime.fromisoformat(exp) == now + timedelta(days=users.SESSION_DAYS)
    assert users.prune_sessions() >= 1


def test_passwords_and_tokens_are_not_stored_in_clear(client):
    data = sign_up(client, "kent")
    with dbmod.connect() as db:
        row = db.execute("SELECT * FROM users").fetchone()
        sessions = [dict(r) for r in db.execute("SELECT * FROM sessions")]
    assert PASSWORD not in row["password_hash"]
    algo, n, r, p, salt, digest = row["password_hash"].split("$")
    assert algo == "scrypt" and int(n) >= 2 ** 14 and salt and digest
    assert all(data["token"] not in str(s.values()) for s in sessions)
    assert users.verify_password(PASSWORD, row["password_hash"])
    assert not users.verify_password(PASSWORD + "x", row["password_hash"])
    assert not users.verify_password(PASSWORD, "garbage")


def test_salts_differ_per_user():
    assert users.hash_password("same password") != users.hash_password("same password")


# ------------------------------------------------------------ throttling
def test_repeated_failures_lock_the_username_briefly(client, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(auth, "throttle",
                        auth.LoginThrottle(limit=3, window=60, lockout=30,
                                           clock=lambda: clock[0]))
    sign_up(client, "kent")
    for _ in range(3):
        assert client.post("/api/auth/login", json={
            "username": "kent", "password": "wrong-password"}).status_code == 401
    locked = client.post("/api/auth/login", json={"username": "kent", "password": PASSWORD})
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0
    # an unknown username locks the same way: no hint about which names exist
    for _ in range(3):
        client.post("/api/auth/login", json={"username": "ghost", "password": "x" * 8})
    assert client.post("/api/auth/login", json={
        "username": "ghost", "password": "x" * 8}).status_code == 429
    # other users are unaffected
    sign_up(client, "maya")
    assert client.post("/api/auth/login", json={
        "username": "maya", "password": PASSWORD}).status_code == 200
    clock[0] += 31
    assert client.post("/api/auth/login", json={
        "username": "kent", "password": PASSWORD}).status_code == 200


def test_success_resets_the_failure_count(client, monkeypatch):
    monkeypatch.setattr(auth, "throttle", auth.LoginThrottle(limit=3))
    sign_up(client, "kent")
    for _ in range(2):
        client.post("/api/auth/login", json={"username": "kent", "password": "wrong-pass"})
    client.post("/api/auth/login", json={"username": "kent", "password": PASSWORD})
    for _ in range(2):
        client.post("/api/auth/login", json={"username": "kent", "password": "wrong-pass"})
    assert client.post("/api/auth/login", json={
        "username": "kent", "password": PASSWORD}).status_code == 200


# ------------------------------------------------------------ transport
def test_media_routes_accept_token_query_param(client):
    token = sign_up(client, "kent")["token"]
    # camera offline -> 404, i.e. past the auth check
    assert client.get(f"/api/live/frame.jpg?token={token}").status_code == 404
    assert client.get("/api/live/frame.jpg?token=nope").status_code == 401
    # ordinary JSON APIs do NOT accept the query param
    assert client.get(f"/api/belongings?token={token}").status_code == 401


def test_cors_allows_authorization_header_from_both_dashboards(client):
    for origin in ("http://localhost:5173", "http://127.0.0.1:5174"):
        pre = client.options("/api/auth/me", headers={
            "Origin": origin, "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization"})
        assert pre.status_code == 200
        assert pre.headers["access-control-allow-origin"] == origin
        assert "authorization" in pre.headers["access-control-allow-headers"].lower()
    evil = client.options("/api/auth/me", headers={
        "Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in evil.headers
