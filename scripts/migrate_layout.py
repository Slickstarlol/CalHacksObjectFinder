"""Move local data from the old flat layout into data/.

Before the reorganisation, RecallAI kept its runtime data at the repository
root. Now it lives in data/:

    memory.db, memory.db-wal, memory.db-shm  ->  data/
    snapshots/                               ->  data/snapshots/
    belongings/ (untracked files, e.g. embeddings_*.npz or items you
                 registered but never committed) -> data/belongings/

.env and .spacetime_token stay at the repository root.

Safe to run more than once: it never overwrites an existing file (it reports
the conflict and leaves both copies), and prints every step.
Stop the server and the camera first so memory.db is not in use.

    python scripts/migrate_layout.py            # do it
    python scripts/migrate_layout.py --dry-run  # only show what would happen
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# memory.db goes last: if a move fails half-way, a re-run still sees that
# data/memory.db is missing and finishes the job instead of stranding the WAL.
DB_FILES = ("memory.db-wal", "memory.db-shm", "memory.db")
KEEP_AT_ROOT = (".env", ".spacetime_token")


def _move(src, dst, dry_run, log):
    """Move one file; never overwrite. Returns True if moved (or would be)."""
    if dst.exists():
        log(f"  skip  {src}  ({dst} already exists; left both copies)")
        return False
    log(f"  move  {src}  ->  {dst}")
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    return True


def _merge_tree(src_dir, dst_dir, dry_run, log):
    """Move every file under src_dir to the same place under dst_dir, then
    remove the source folders that ended up empty."""
    moved = skipped = 0
    for src in sorted(p for p in src_dir.rglob("*") if p.is_file()):
        if _move(src, dst_dir / src.relative_to(src_dir), dry_run, log):
            moved += 1
        else:
            skipped += 1
    if not dry_run:
        for d in sorted((p for p in src_dir.rglob("*") if p.is_dir()),
                        key=lambda p: len(p.parts), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()
        if not any(src_dir.iterdir()):
            src_dir.rmdir()
            log(f"  removed empty {src_dir}")
    return moved, skipped


def migrate(root=REPO_ROOT, dry_run=False, log=print):
    """Returns {"moved": n, "skipped": n}."""
    root = Path(root)
    data = root / "data"
    moved = skipped = 0
    log(f"RecallAI layout migration in {root}" + ("  (dry run)" if dry_run else ""))

    # 1. SQLite: the three files belong together, so move all or none.
    present = [name for name in DB_FILES if (root / name).is_file()]
    if present:
        if (data / "memory.db").exists():
            log(f"  skip  {', '.join(present)}  (data/memory.db already exists; "
                "left the root copy alone - keep whichever you need)")
            skipped += len(present)
        else:
            for name in present:
                moved += _move(root / name, data / name, dry_run, log)

    # 2. Camera snapshots and 3. untracked belongings files.
    for name in ("snapshots", "belongings"):
        if (root / name).is_dir():
            m, s = _merge_tree(root / name, data / name, dry_run, log)
            moved += m
            skipped += s

    for name in KEEP_AT_ROOT:
        if (root / name).is_file():
            log(f"  keep  {root / name}  (stays at the repository root)")

    if not moved and not skipped:
        log("Nothing to migrate: the layout is already up to date.")
    else:
        log(f"Done: {moved} moved, {skipped} skipped." if not dry_run
            else f"Would move {moved}, skip {skipped}.")
    return {"moved": moved, "skipped": skipped}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Move root-level RecallAI data into data/.")
    ap.add_argument("--root", type=Path, default=REPO_ROOT,
                    help="repository root (default: the one this script is in)")
    ap.add_argument("--dry-run", action="store_true", help="only print what would happen")
    args = ap.parse_args(argv)
    try:
        migrate(args.root, args.dry_run)
    except OSError as e:
        raise SystemExit(f"Could not move a file: {e}\n"
                         "Stop the server/camera (they keep memory.db open) and run again.")


if __name__ == "__main__":
    main()
