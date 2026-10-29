from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
RESULT = EVIDENCE / "r307_system_backtest_result_attempt7.json"
TRADES = EVIDENCE / "r307_system_trade_records_attempt7.json"
OUTPUT = EVIDENCE / "r307_final_integrity_audit_final.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise FileExistsError("R307 final audit already exists; refusing overwrite")

    result = json.loads(RESULT.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    trades = json.loads(TRADES.read_text(encoding="utf-8"))
    assert result["round"] == "R307"
    assert result["decision"] == "REJECT_frozen_statistical_gates_not_all_passed"
    assert result["gates"]["G2_validation"] is True
    assert sum(value for key, value in result["gates"].items() if key != "all_pass") == 1
    assert result["execution_integrity"]["system_backtest_errors"] == {}
    assert result["execution_integrity"]["candidate_signal_parity_pass"] is True
    assert result["execution_integrity"]["system_trade_signal_integrity_pass"] is True
    assert result["execution_integrity"]["all_completed_trades_match_open_t1_to_open_t6"] is True
    assert result["input_hashes_stable_after_execution"] is True
    assert result["execution_code_hashes_stable_after_execution"] is True
    assert result["panel_registration_pre_execution"]["panel_status_fallback_used"] is True
    assert result["panel_registration_pre_execution"]["stored_registration_pending_and_unique"] is True
    assert trades["attempt"] == "attempt7"

    trade_counts = {key: len(value) for key, value in trades["system_runs"].items()}
    for strategy_id, count in trade_counts.items():
        expected = result["candidate_signal_audits"][strategy_id]["system_trade_signal_count"]
        assert count == expected
        assert count == result["system_backtests"][strategy_id]["stats"]["n_trades"]
    assert trade_counts == {
        "r307_gap_retention_bottom": 2526,
        "r307_gap_retention_top": 2524,
    }

    files = []
    patterns = ("r307*", "run_r307*", "register_r307*", "audit_r307*", "finalize_r307*")
    paths = sorted({path for pattern in patterns for path in EVIDENCE.glob(pattern) if path.is_file()})
    for path in paths:
        if path.name.endswith(".sha256") or path == OUTPUT:
            continue
        sidecar = path.with_name(path.name + ".sha256")
        if not sidecar.is_file():
            raise FileNotFoundError(f"R307 evidence has no SHA-256 sidecar: {path.name}")
        expected_sha = sidecar.read_text(encoding="ascii").split()[0]
        actual_sha = sha256(path)
        if actual_sha != expected_sha:
            raise ValueError(f"R307 evidence checksum mismatch: {path.name}")
        files.append({"path": path.relative_to(ROOT).as_posix(), "size_bytes": path.stat().st_size, "sha256": actual_sha})

    audit = {
        "round": "R307",
        "classification": "final_integrity_audit_final_no_new_panel_request_or_backtest",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "final_result": RESULT.relative_to(ROOT).as_posix(),
        "result_sha256": sha256(RESULT),
        "trade_records": TRADES.relative_to(ROOT).as_posix(),
        "trade_records_sha256": sha256(TRADES),
        "trade_counts": trade_counts,
        "input_hashes_stable_after_execution": result["input_hashes_stable_after_execution"],
        "execution_code_hashes_stable_after_execution": result["execution_code_hashes_stable_after_execution"],
        "candidate_signal_parity_pass": result["execution_integrity"]["candidate_signal_parity_pass"],
        "fill_timing_violation_count": result["execution_integrity"]["timing_violation_count"],
        "statistical_gates": result["gates"],
        "decision": result["decision"],
        "panel_status_is_live": False,
        "panel_proof_mode": result["panel_registration_pre_execution"]["capture_provenance"]["mode"],
        "evidence_file_count_excluding_this_audit": len(files),
        "evidence_files": files,
    }
    raw = (json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({"audit": OUTPUT.relative_to(ROOT).as_posix(), "evidence_file_count": len(files), "decision": audit["decision"], "sha256": hashlib.sha256(raw).hexdigest()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
