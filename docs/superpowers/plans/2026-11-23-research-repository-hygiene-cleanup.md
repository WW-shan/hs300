# Research Repository Hygiene Cleanup Plan

**Goal:** Inventory all prior research, normalize provenance artifacts, remove only reproducible local caches, and leave a machine-checkable audit trail.

## Non-negotiable preservation rules

- Never delete tracked source, tests, plans, preregistrations, result JSON/Markdown, sidecars, factor definitions, factor-set versions, ledgers, frozen research inputs, or raw financial/market data.
- Never rewrite result payloads or preregistrations to make them appear compliant.
- Treat a missing sidecar as a provenance defect; add the sidecar from the existing bytes and record the hash.
- Treat a missing plan or nonstandard historical payload as an explicit legacy gap, not as a reason to fabricate history.
- Local caches may be deleted only when they are ignored, reproducible, and not the sole copy of research evidence.

## Phase 1: inventory

1. Enumerate tracked and untracked research runners, plans, preregistrations, outputs, sidecars, ledgers, factor sets and local data roots.
2. Record repository disk usage and the largest reproducible directories.
3. Detect sidecar format drift, missing sidecars, duplicate result bodies, duplicate research identifiers and decisions that incorrectly imply promotion.

## Phase 2: provenance normalization

1. Normalize every research sidecar to `sha256  basename` in the artifact directory.
2. Add sidecars for tracked research JSON/Markdown that lack one, including parameter subdirectories.
3. Re-verify every sidecar against its target bytes.
4. Add a regression test that checks sidecar presence and digest equality across the research tree.
5. Produce a generated inventory report with each research artifact's round, status, preregistration, runner, result, sidecars and plan link when present.

## Phase 3: reproducible local cleanup

1. Delete `data/.backtest_matrix_cache` only after recording its size.
2. Delete Python/test/lint caches and build artifacts only.
3. Preserve `data/research`, `data/factor_research`, raw data, frozen bundles, `backend/.venv` and `frontend/node_modules`.
4. Re-run focused research and HS300 tests; report unrelated baseline failures separately.

## Phase 4: verification

- `git diff --check`
- research sidecar audit
- focused backend tests
- frontend build after reinstalling dependencies if required
- final disk usage comparison
- commit and push only provenance/tooling changes, not local cache deletions
