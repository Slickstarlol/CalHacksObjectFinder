import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

# backend/ on sys.path so `import recallai` works (pytest.ini also sets pythonpath).
BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def make_object_image(color, size=(120, 160), seed=0, bg=(128, 128, 128)):
    """A coloured, textured 'object' on a grey background (BGR)."""
    rng = np.random.default_rng(seed)
    h, w = size
    img = np.full((h, w, 3), bg, np.uint8)
    obj = np.full((h - 20, w - 20, 3), color, np.int16)
    obj += rng.integers(-18, 18, obj.shape, dtype=np.int16)  # texture/noise
    img[10:-10, 10:-10] = np.clip(obj, 0, 255).astype(np.uint8)
    return img


def register(base, name, category, images, item_id=None):
    item_id = item_id or name.lower().replace(" ", "_")
    folder = Path(base) / item_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "info.json").write_text(
        json.dumps({"id": item_id, "name": name, "category": category}))
    for i, img in enumerate(images, 1):
        cv2.imwrite(str(folder / f"reference_{i}.jpg"), img)
    return item_id


@pytest.fixture
def belongings_dir(tmp_path):
    d = tmp_path / "belongings"
    d.mkdir()
    return d


RED = (40, 40, 200)
BLUE = (200, 60, 30)
GREEN = (50, 170, 60)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_accounts(tmp_path, monkeypatch):
    """Keep account state per test, and point the server's belongings folder
    at this test's tmp dir (tmp_path/"belongings", the same folder the
    `belongings_dir` fixture creates) so registering the first account never
    claims the repository's real data/belongings."""
    from recallai import paths
    from recallai.api import auth, search
    monkeypatch.setattr(search, "BELONGINGS_DIR", tmp_path / "belongings")
    auth.throttle.reset()
    server = sys.modules.get("recallai.api.server")
    if server is not None:
        server.reset_gps()
        server.reset_enroll()
    real = {p: p.read_bytes() for p in Path(paths.BELONGINGS_DIR).glob("*/info.json")}
    yield
    auth.throttle.reset()
    # Guard: no test may claim or edit the repository's real belongings.
    changed = [str(p) for p, data in real.items() if p.read_bytes() != data]
    assert not changed, f"test modified real belongings: {changed}"


PASSWORD = "correct horse battery"


def sign_up(client, username="kent", password=PASSWORD, display_name=None):
    """Register an account through the API; returns the JSON (token, user)."""
    r = client.post("/api/auth/register", json={
        "username": username, "password": password,
        "display_name": display_name or username.title()})
    assert r.status_code == 201, r.text
    return r.json()


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def sign_in(client, username="kent", password=PASSWORD):
    """Register `username` and make every later request from `client` carry
    its token. In a fresh test DB the first account claims all ownerless
    rows and belongings, i.e. everything the test inserted before."""
    data = sign_up(client, username, password)
    client.headers.update(auth_headers(data["token"]))
    return data


def lazy_signed_in_client(app, username="kent"):
    """A TestClient that registers `username` right before its first request,
    so everything the test inserted until then is claimed by that account
    (the first-account migration), whatever order fixtures ran in."""
    from fastapi.testclient import TestClient

    class _Client(TestClient):
        _signed = False

        def request(self, *args, **kwargs):
            if not self._signed:
                self._signed = True
                self.user = sign_in(self, username)["user"]
            return super().request(*args, **kwargs)

    return _Client(app)
