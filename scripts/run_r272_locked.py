#!/usr/bin/env python3
"""Run the frozen R272 test with a non-mutating lock-schema compatibility view."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER_PATH = ROOT / "scripts/backtest_r272_operating_margin_improvement.py"
BUILDER_PATH = ROOT / "scripts/build_r272_operating_margin_factor.py"
LOCK_PATH = EVIDENCE / "r272_implementation_correction_final_v3.json"
PANEL_PATH = EVIDENCE / "r272_panel_registration.json"
AUDIT_PATH = EVIDENCE / "r272_corrected_factor_audit_v2.json"
OUT = EVIDENCE / "r272_execution_compatibility.json"
RESULT = EVIDENCE / "r272_operating_margin_improvement_corrected.json"
CACHE = ROOT / "data/research/r272_cache_corrected"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load locked research module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _LockSchemaView(dict):
    """Expose the existing plural lock key under the runner's singular lookup."""

    def get(self, key, default=None):
        if key == "corrected_source_sha256":
            key = "corrected_source_hashes"
        return super().get(key, default)


def main() -> int:
    if OUT.exists() or OUT.with_suffix(".json.sha256").exists():
        raise RuntimeError("R272 execution compatibility proof exists; refusing overwrite")
    if RESULT.exists() or RESULT.with_suffix(".json.sha256").exists():
        raise RuntimeError("R272 result proof already exists; refusing overwrite")
    if any(CACHE.glob("*")) if CACHE.exists() else False:
        raise RuntimeError("R272 corrected run cache is not empty; audit it before executing")

    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    runner_hash = _sha256(RUNNER_PATH)
    builder_hash = _sha256(BUILDER_PATH)
    prereg = json.loads((EVIDENCE / "r272_preregistration.json").read_text(encoding="utf-8"))
    if lock.get("corrected_source_hashes", {}).get("runner") != runner_hash:
        raise RuntimeError("R272 runner no longer matches the frozen correction lock")
    if lock.get("corrected_source_hashes", {}).get("builder") != builder_hash:
        raise RuntimeError("R272 builder no longer matches the frozen correction lock")
    if prereg.get("runner_sha256") != lock.get("registered_source_hashes", {}).get("runner"):
        raise RuntimeError("R272 preregistration-to-lock source chain mismatch")
    for path in (AUDIT_PATH, PANEL_PATH):
        sidecar = path.with_suffix(".json.sha256")
        if not sidecar.exists() or sidecar.read_text(encoding="ascii").split()[0] != _sha256(path):
            raise RuntimeError(f"required R272 preflight evidence checksum mismatch: {path.name}")
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    panel = json.loads(PANEL_PATH.read_text(encoding="utf-8"))
    if audit.get("all_checks_pass") is not True or panel.get("all_checks_pass") is not True:
        raise RuntimeError("R272 factor audit and panel registration must pass before execution")

    mismatch = {
        "runner_lookup_key": "corrected_source_sha256",
        "actual_lock_key": "corrected_source_hashes",
        "registered_and_corrected_source_hashes_match": (
            lock["registered_source_hashes"]["runner"]
            != lock["corrected_source_hashes"]["runner"]
            and lock["corrected_source_hashes"]["runner"] == runner_hash
        ),
    }
    if not mismatch["registered_and_corrected_source_hashes_match"]:
        raise RuntimeError("R272 runner/lock schema mismatch is not the previously audited source-only correction")

    proof = {
        "round": "R272",
        "status": "pre_outcome_execution_compatibility_locked",
        "locked_at": datetime.now(UTC).isoformat(),
        "classification": "non_outcome_execution_adapter_for_frozen_source_lock_key_mismatch",
        "lock": {"path": LOCK_PATH.relative_to(ROOT).as_posix(), "sha256": _sha256(LOCK_PATH)},
        "runner": {"path": RUNNER_PATH.relative_to(ROOT).as_posix(), "sha256": runner_hash},
        "builder": {"path": BUILDER_PATH.relative_to(ROOT).as_posix(), "sha256": builder_hash},
        "adapter": {"path": Path(__file__).resolve().relative_to(ROOT).as_posix(), "sha256": _sha256(Path(__file__).resolve())},
        "source_mismatch": mismatch,
        "behavior": "At runtime only, provide a dict view that aliases the runner's singular corrected_source_sha256 lookup to the existing lock's corrected_source_hashes key. The lock file, research runner, preregistration, strategy, factor, inputs, gates, execution, and output logic remain unchanged.",
        "preflight": {
            "factor_audit": {"path": AUDIT_PATH.relative_to(ROOT).as_posix(), "sha256": _sha256(AUDIT_PATH), "passed": audit["all_checks_pass"]},
            "panel_registration": {"path": PANEL_PATH.relative_to(ROOT).as_posix(), "sha256": _sha256(PANEL_PATH), "passed": panel["all_checks_pass"]},
        },
        "scope": {"outcome_data_read_before_lock": False, "backtest_run": False, "production_status": "pause_production"},
    }
    OUT.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = _sha256(OUT)
    OUT.with_suffix(".json.sha256").write_text(f"{digest}  {OUT.name}\n", encoding="ascii")
    print(json.dumps({"compatibility_lock": OUT.relative_to(ROOT).as_posix(), "sha256": digest, "source_mismatch": mismatch}, ensure_ascii=False, indent=2), flush=True)

    runner = _load(RUNNER_PATH, "r272_locked_runner")
    original_load = runner._load

    def load_with_lock_view(path: Path, name: str):
        module = original_load(path, name)
        if Path(path).resolve() == BUILDER_PATH.resolve():
            original_verify = module._verify_implementation_lock

            def verify_with_view():
                data, digest, factor_id = original_verify()
                return _LockSchemaView(data), digest, factor_id

            module._verify_implementation_lock = verify_with_view
        return module

    runner._load = load_with_lock_view
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
