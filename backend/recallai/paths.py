"""Every filesystem path RecallAI uses, in one place.

Layout (relative to the repository root):
    data/memory.db          SQLite (git-ignored)
    data/snapshots/         camera frames + crops (git-ignored)
    data/belongings/        your registered items and reference photos
    models/yolo11n.pt       YOLO weights
    .env, .spacetime_token  secrets, stay at the repository root

The sightings table stores image paths relative to DATA_DIR
("snapshots/123.jpg", "snapshots/crops/123.jpg"), so SNAPSHOT_DIR is always
DATA_DIR/snapshots. Override with environment variables:
    RECALLAI_ROOT, RECALLAI_DATA_DIR, RECALLAI_DB_PATH, RECALLAI_BELONGINGS_DIR,
    RECALLAI_MODELS_DIR, RECALLAI_YOLO_PATH, RECALLAI_STATIC_DIR,
    RECALLAI_WEB_DIST, RECALLAI_ENV_FILE, RECALLAI_SPACETIME_TOKEN
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent          # backend/recallai


def _env_path(name, default):
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser().resolve() if value else Path(default)


ROOT = _env_path("RECALLAI_ROOT", PACKAGE_DIR.parent.parent)

DATA_DIR = _env_path("RECALLAI_DATA_DIR", ROOT / "data")
DB_PATH = _env_path("RECALLAI_DB_PATH", DATA_DIR / "memory.db")
SNAPSHOT_DIR = DATA_DIR / "snapshots"      # must match the stored "snapshots/..." paths
BELONGINGS_DIR = _env_path("RECALLAI_BELONGINGS_DIR", DATA_DIR / "belongings")

MODELS_DIR = _env_path("RECALLAI_MODELS_DIR", ROOT / "models")
YOLO_PATH = _env_path("RECALLAI_YOLO_PATH", MODELS_DIR / "yolo11n.pt")

STATIC_DIR = _env_path("RECALLAI_STATIC_DIR", PACKAGE_DIR / "api" / "static")
WEB_DIST = _env_path("RECALLAI_WEB_DIST", ROOT / "frontend" / "live-dashboard" / "dist")

ENV_FILE = _env_path("RECALLAI_ENV_FILE", ROOT / ".env")
SPACETIME_TOKEN = _env_path("RECALLAI_SPACETIME_TOKEN", ROOT / ".spacetime_token")

MIGRATE_HINT = "python scripts/migrate_layout.py"


def legacy_files(root=None, db_path=None):
    """Data left at the repository root by the old flat layout that the
    current layout would not see. Empty when there is nothing to migrate."""
    root = Path(root or ROOT)
    db_path = Path(db_path or DB_PATH)
    found = []
    if (root / "memory.db").is_file() and not db_path.exists():
        found.append("memory.db")
    for name in ("snapshots", "belongings"):
        folder = root / name
        if folder.is_dir() and any(p.is_file() for p in folder.rglob("*")):
            found.append(name + "/")
    return found


def warn_legacy_layout(stream=None):
    """One-line hint when old root-level data would otherwise be ignored."""
    found = legacy_files()
    if found:
        print(f"RecallAI: found {', '.join(found)} at the repository root (old layout); "
              f"run `{MIGRATE_HINT}` to move it into data/.",
              file=stream or sys.stderr)
    return found


warn_legacy_layout()
