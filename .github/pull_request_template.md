## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

- [ ] Ran the checks for the parts I touched (`.\scripts\dev.ps1 test`, or the per-part commands in docs/WORKFLOW.md)
- [ ] Changed `spacetimedb/src/index.ts`? Regenerated `frontend/live-dashboard/src/module_bindings/` and updated docs/SPACETIME_CONTRACT.md
- [ ] Changed the SQLite schema? `init_db` still upgrades an existing `memory.db`, with a test
- [ ] New file paths go through `backend/recallai/paths.py`
- [ ] No secrets or local data committed (`.env`, `.spacetime_token`, `data/memory.db*`, `data/snapshots/`)
- [ ] Screenshot attached for UI changes
