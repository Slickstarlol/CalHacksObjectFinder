"""Repository layout: recallai.paths and scripts/migrate_layout.py."""

import importlib
import importlib.util
import io
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def load_migrate():
    spec = importlib.util.spec_from_file_location(
        "migrate_layout", REPO / "scripts" / "migrate_layout.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def old_repo(tmp_path):
    """A checkout after `git pull`: tracked belongings already moved to
    data/belongings, untracked runtime data still at the root."""
    root = tmp_path / "repo"
    (root / "data" / "belongings" / "bag1").mkdir(parents=True)
    (root / "data" / "belongings" / "bag1" / "info.json").write_text('{"id": "bag1"}')
    with sqlite3.connect(root / "memory.db") as db:
        db.execute("CREATE TABLE sightings (id INTEGER PRIMARY KEY, image_path TEXT)")
        db.execute("INSERT INTO sightings (image_path) VALUES ('snapshots/1.jpg')")
    (root / "memory.db-wal").write_bytes(b"wal")
    (root / "memory.db-shm").write_bytes(b"shm")
    (root / "snapshots" / "crops").mkdir(parents=True)
    (root / "snapshots" / "1.jpg").write_bytes(b"frame")
    (root / "snapshots" / "crops" / "1.jpg").write_bytes(b"crop")
    (root / "belongings" / "bag1").mkdir(parents=True)
    (root / "belongings" / "bag1" / "embeddings_dino.npz").write_bytes(b"emb")
    (root / "belongings" / "keys9").mkdir()
    (root / "belongings" / "keys9" / "reference_1.jpg").write_bytes(b"ref")
    (root / ".env").write_text("XAI_API_KEY=x\n")
    (root / ".spacetime_token").write_text("tok")
    return root


def test_migrate_moves_root_data_into_data_dir(old_repo):
    out = []
    result = load_migrate().migrate(old_repo, log=out.append)
    data = old_repo / "data"

    assert result == {"moved": 7, "skipped": 0}
    for name in ("memory.db", "memory.db-wal", "memory.db-shm"):
        assert (data / name).is_file() and not (old_repo / name).exists()
    with sqlite3.connect(data / "memory.db") as db:   # stored paths unchanged
        assert db.execute("SELECT image_path FROM sightings").fetchall() == [("snapshots/1.jpg",)]
    assert (data / "snapshots" / "1.jpg").read_bytes() == b"frame"
    assert (data / "snapshots" / "crops" / "1.jpg").read_bytes() == b"crop"
    assert (data / "belongings" / "bag1" / "embeddings_dino.npz").read_bytes() == b"emb"
    assert (data / "belongings" / "bag1" / "info.json").read_text() == '{"id": "bag1"}'
    assert (data / "belongings" / "keys9" / "reference_1.jpg").read_bytes() == b"ref"
    assert not (old_repo / "snapshots").exists()
    assert not (old_repo / "belongings").exists()
    # secrets stay at the root
    assert (old_repo / ".env").read_text() == "XAI_API_KEY=x\n"
    assert (old_repo / ".spacetime_token").read_text() == "tok"
    assert any("keep" in line and ".env" in line for line in out)


def test_migrate_is_idempotent(old_repo):
    mig = load_migrate()
    mig.migrate(old_repo, log=lambda *_: None)
    out = []
    assert mig.migrate(old_repo, log=out.append) == {"moved": 0, "skipped": 0}
    assert any("Nothing to migrate" in line for line in out)


def test_migrate_never_overwrites(old_repo):
    data = old_repo / "data"
    (data / "snapshots").mkdir(parents=True)
    (data / "snapshots" / "1.jpg").write_bytes(b"newer")
    (data / "memory.db").write_bytes(b"newer db")

    out = []
    result = load_migrate().migrate(old_repo, log=out.append)

    assert (data / "memory.db").read_bytes() == b"newer db"
    assert (data / "snapshots" / "1.jpg").read_bytes() == b"newer"
    # conflicting files stay where they were, for the user to decide
    assert (old_repo / "memory.db").is_file()
    assert (old_repo / "memory.db-wal").is_file()
    assert (old_repo / "snapshots" / "1.jpg").read_bytes() == b"frame"
    # everything else still moved
    assert (data / "snapshots" / "crops" / "1.jpg").is_file()
    assert result["skipped"] == 4 and result["moved"] == 3
    assert any("skip" in line for line in out)


def test_migrate_dry_run_changes_nothing(old_repo):
    before = sorted(p.relative_to(old_repo) for p in old_repo.rglob("*"))
    out = []
    result = load_migrate().migrate(old_repo, dry_run=True, log=out.append)
    assert result == {"moved": 7, "skipped": 0}
    assert sorted(p.relative_to(old_repo) for p in old_repo.rglob("*")) == before
    assert any("move" in line for line in out)


def test_migrate_cli(old_repo, capsys):
    load_migrate().main(["--root", str(old_repo)])
    assert (old_repo / "data" / "memory.db").is_file()
    assert "Done: 7 moved, 0 skipped." in capsys.readouterr().out


# ---------------------------------------------------------------------------
# recallai.paths
# ---------------------------------------------------------------------------
def test_default_layout_points_into_the_repo():
    from recallai import paths
    assert paths.ROOT == REPO
    assert paths.DATA_DIR == REPO / "data"
    assert paths.DB_PATH == REPO / "data" / "memory.db"
    assert paths.SNAPSHOT_DIR == REPO / "data" / "snapshots"
    assert paths.BELONGINGS_DIR == REPO / "data" / "belongings"
    assert paths.YOLO_PATH == REPO / "models" / "yolo11n.pt"
    assert paths.YOLO_PATH.is_file()
    assert (paths.STATIC_DIR / "search.html").is_file()
    assert (paths.STATIC_DIR / "gps.html").is_file()
    assert paths.WEB_DIST == REPO / "frontend" / "live-dashboard" / "dist"
    assert paths.ENV_FILE == REPO / ".env"
    assert paths.SPACETIME_TOKEN == REPO / ".spacetime_token"


def test_modules_share_the_paths():
    from recallai import paths
    from recallai.ai import llm
    from recallai.memory import db, temporal_memory
    from recallai.sync import spacetime_bridge
    from recallai.vision import recognition, recorder, register
    assert db.DB_PATH == paths.DB_PATH
    assert recorder.SNAPSHOT_DIR == paths.SNAPSHOT_DIR
    assert recognition.BELONGINGS_DIR == register.BELONGINGS_DIR == paths.BELONGINGS_DIR
    assert temporal_memory.BASE_DIR == llm.DATA_DIR == paths.DATA_DIR
    assert llm.ENV_FILE == paths.ENV_FILE
    assert spacetime_bridge.TOKEN_FILE == paths.SPACETIME_TOKEN


def test_env_vars_override(monkeypatch, tmp_path):
    from recallai import paths
    monkeypatch.setenv("RECALLAI_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("RECALLAI_YOLO_PATH", str(tmp_path / "y.pt"))
    monkeypatch.setenv("RECALLAI_ENV_FILE", str(tmp_path / "secrets.env"))
    try:
        p = importlib.reload(paths)
        assert p.DATA_DIR == tmp_path / "d"
        assert p.DB_PATH == tmp_path / "d" / "memory.db"
        assert p.SNAPSHOT_DIR == tmp_path / "d" / "snapshots"
        assert p.BELONGINGS_DIR == tmp_path / "d" / "belongings"
        assert p.YOLO_PATH == tmp_path / "y.pt"
        assert p.ENV_FILE == tmp_path / "secrets.env"
    finally:
        monkeypatch.undo()
        importlib.reload(paths)
    assert paths.DATA_DIR == REPO / "data"


def test_legacy_layout_hint(old_repo):
    from recallai import paths
    assert paths.legacy_files(old_repo, old_repo / "data" / "memory.db") == \
        ["memory.db", "snapshots/", "belongings/"]
    load_migrate().migrate(old_repo, log=lambda *_: None)
    assert paths.legacy_files(old_repo, old_repo / "data" / "memory.db") == []


def test_legacy_hint_is_one_line(monkeypatch):
    from recallai import paths
    monkeypatch.setattr(paths, "legacy_files", lambda: ["memory.db"])
    buf = io.StringIO()
    assert paths.warn_legacy_layout(buf) == ["memory.db"]
    msg = buf.getvalue()
    assert msg.count("\n") == 1 and "scripts/migrate_layout.py" in msg
