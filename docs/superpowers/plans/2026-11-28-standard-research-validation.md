# Standard Research Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add mandatory GBM stress and parameter Monte Carlo validation with immutable charts to all future research.

**Architecture:** Extend the existing synthetic GBM executor to accept frozen strategy score matrices, add a shared standard-validation receipt module, enforce the receipt in the research contract/auditor, then run the final R550 strategy through the new suite and publish the first parameter Monte Carlo charts.

**Tech Stack:** Python 3.11+, Polars, NumPy, PIL, FastAPI project backtest engine, pytest.

**Spec:** `docs/superpowers/specs/2026-11-28-standard-research-validation-design.md`

## Global Constraints

- No leverage; maximum exposure must remain at or below 100%.
- T+1 open matching remains mandatory for stock strategy conclusions.
- 2x cost is the baseline, 3x is stress, 5x is warning-only.
- Recent/test observations never participate in parameter selection.
- Every published result and chart is immutable, SHA-256 sidecar-bound, and ledger-bound.
- Existing research runners without the new receipt remain grandfathered; new or changed project-formal runners require the suite.

---

### Task 1: Fixed-score GBM adapter

**Files:**
- Modify: `backend/app/backtest/synthetic_gbm.py`
- Test: `backend/tests/backtest/test_synthetic_gbm.py`

**Interfaces:**
- Produces: `run_fixed_score_path(spec, scores, score_name="fixed_strategy", ...) -> dict`
- Produces: `generate_synthetic_market(..., symbols: Sequence[str] | None = None, industries: Mapping[str, str] | None = None) -> SyntheticMarket`

- [x] Write failing tests for custom score fields, T+1 entry, identical same-path benchmark, and 2x/3x/5x monotonic cost.
- [x] Run focused tests and confirm failure.
- [x] Implement the fixed-score adapter without changing default R-GBM behavior.
- [x] Run focused tests.

### Task 2: Standard validation receipt and plots

**Files:**
- Create: `backend/app/factor_research/standard_validation.py`
- Test: `backend/tests/factor_research/test_standard_validation.py`

**Interfaces:**
- Produces: `build_standard_validation_receipt(gbm, parameter_mc, artifacts) -> dict`
- Produces: `check_standard_validation_constraints(receipt) -> dict`
- Produces: `plot_gbm_distribution(summary, path) -> Path`
- Produces: `plot_parameter_paths(points, path) -> Path`
- Produces: `plot_parameter_pareto(points, path) -> Path`
- Produces: `plot_parameter_bootstrap(distributions, path) -> Path`

- [x] Write failing tests for receipt status, missing artifacts, leverage violation, and recent-based parameter selection.
- [x] Run tests and confirm failure.
- [x] Implement receipt validation and PIL charts.
- [x] Run tests.

### Task 3: Standard parameter MC selector

**Files:**
- Create: `backend/app/factor_research/standard_parameter_mc.py`
- Test: `backend/tests/factor_research/test_standard_parameter_mc.py`

**Interfaces:**
- Produces: `select_standard_parameter(points, constraints) -> dict`
- Produces: `build_parameter_artifacts(result, output_directory) -> list[dict]`

- [x] Write failing tests proving recent results are ignored and validation confirmation is required.
- [x] Implement train-only filter, validation confirmation, plateau/tie-break, diagnostics, and charts.
- [x] Run tests.

### Task 4: Research contract enforcement

**Files:**
- Modify: `backend/app/factor_research/research_contract.py`
- Modify: `scripts/audit_research_contract.py`
- Test: `backend/tests/test_research_contract.py`
- Test: `backend/tests/test_research_conformance_contract.py`

**Interfaces:**
- Consumes: `check_standard_validation_constraints(receipt)`.
- Produces: new `project_formal` result requirement for `metrics.standard_validation`.

- [x] Write failing tests for missing receipt and invalid project_formal receipt.
- [x] Implement contract validation with explicit legacy grandfather boundary.
- [x] Add static audit requirement for new runners calling `run_fixed_score_path` or the shared suite.
- [x] Run focused tests and contract audit.

### Task 5: Publish attachments atomically

**Files:**
- Modify: `backend/app/factor_research/research_contract.py`
- Test: `backend/tests/test_research_contract.py`

**Interfaces:**
- Extend `publish_research_artifacts(..., attachments: Sequence[tuple[str, bytes]] = ())`.
- Each attachment gets a `.sha256` and is included in ledger evidence.

- [x] Write failing tests for attachment write and hash binding.
- [x] Implement atomic attachment publication.
- [x] Run tests.

### Task 6: R550 strategy-level GBM stress

**Files:**
- Create: `scripts/research_r551_r550_gbm_stress.py`
- Create: `backend/tests/test_r551_r550_gbm_stress.py`
- Publish: `data/factor_research/research/r551_r550_gbm_stress_preregistration.json`
- Publish: `data/factor_research/research/r551_r550_gbm_stress_result.json`
- Publish: `data/factor_research/research/r551_r550_gbm_stress_result.md`
- Publish: `data/factor_research/research/r551_r550_gbm_stress_gbm_distribution.png`
- Publish: `data/factor_research/research/r551_r550_gbm_stress_gbm_constraints.png`

**Interfaces:**
- Consumes: R550 frozen score panel, GBM fixed-score adapter, standard-validation plots.
- Research type: `synthetic_market_stress`.

- [x] Write failing contract and matrix-alignment tests.
- [x] Implement synthetic score matrix export for the R550 fixed universe.
- [x] Run 100 paths across 4 scenarios and 2 betas.
- [x] Publish JSON/Markdown/PNG and sidecars.
- [x] Report strategy vs same-path equal-weight, MDD, turnover, and cost sensitivity.

### Task 7: R550 parameter Monte Carlo

**Files:**
- Create: `scripts/research_r552_cash_safety_parameter_mc.py`
- Test: `backend/tests/test_r552_cash_safety_parameter_mc.py`
- Publish: parameter MC JSON/Markdown and three PNG charts.

**Interfaces:**
- Grid: positions `8/10/12`, hold `40/60/80`, vol target `10%/12%/14%`; stop fixed at `-12%`.
- Uses train selection and validation confirmation; recent/test disclosure only.

- [x] Write failing tests for grid completeness, recent exclusion, and constraints.
- [x] Run all parameter points through the project chain.
- [x] Select the constrained parameter with the standard selector.
- [x] Publish charts and evidence.

### Task 8: Documentation and final verification

**Files:**
- Modify: `docs/research-standard.md`
- Modify: `CONTRIBUTING.md`
- Modify: `docs/superpowers/specs/2026-11-24-research-contract-design.md`

- [x] Document the two standard suites and mandatory receipt.
- [x] Run Ruff, focused tests, full backend tests, frontend build, all research audits, contract audit.
- [x] Run GBM and parameter MC reproducibility checks.
- [x] Commit with DCO, push, and verify CI/Docker.
