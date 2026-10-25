#!/usr/bin/env bash
# RecallAI dev helper (macOS / Linux / Git Bash). Same commands as dev.ps1:
#   scripts/dev.sh setup | server | camera [args] | register [args] | ask "question"
#                  check-grok | sync-once | dashboard | live-dashboard
#                  spacetime | publish | generate | migrate | test | test-fast
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Makes `import recallai` work even without `pip install -e backend`.
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
PY="${PYTHON:-python3}"
cmd="${1:-help}"
shift || true

at() { (cd "$ROOT/$1" && shift && "$@"); }

case "$cmd" in
  setup)
    at . "$PY" -m pip install -r backend/requirements.txt
    at . "$PY" -m pip install -e backend
    for d in spacetimedb frontend/live-dashboard frontend/dashboard; do at "$d" npm install; done
    at . "$PY" scripts/migrate_layout.py ;;
  server)         at . "$PY" -m uvicorn recallai.api.server:app --host 0.0.0.0 --port 8000 "$@" ;;
  camera)         at . "$PY" -m recallai.vision.webcam "$@" ;;
  register)       at . "$PY" -m recallai.vision.register "$@" ;;
  ask)            at . "$PY" -m recallai.ai.agent "$@" ;;
  check-grok)     at . "$PY" -m recallai.ai.check_grok "$@" ;;
  sync-once)      at . "$PY" -m recallai.sync.spacetime_bridge --once ;;
  dashboard)      at frontend/dashboard npm run dev ;;
  live-dashboard) at frontend/live-dashboard npm run dev ;;
  spacetime)      at . spacetime start ;;
  publish)        at spacetimedb spacetime publish --server local recallai "$@" ;;
  generate)       at frontend/live-dashboard npm run spacetime:generate ;;
  migrate)        at . "$PY" scripts/migrate_layout.py "$@" ;;
  test-fast)      at . "$PY" -m pytest -q -m "not slow" ;;
  test)
    at . "$PY" -m pytest -q
    at spacetimedb npx tsc --noEmit -p .
    at spacetimedb npx vitest run
    at frontend/live-dashboard npm run build
    at frontend/live-dashboard npx vitest run
    at frontend/dashboard npm run build
    at frontend/dashboard npm run lint ;;
  *)
    sed -n '2,5p' "$0"
    [ "$cmd" = help ] || { echo "Unknown command: $cmd"; exit 1; } ;;
esac
