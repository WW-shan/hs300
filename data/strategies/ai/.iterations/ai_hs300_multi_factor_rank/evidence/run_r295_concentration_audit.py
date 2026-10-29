#!/usr/bin/env python3
"""Reconcile R257 concentration shares from frozen 3x system trade logs."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
PRE = EVIDENCE / "r295_preregistration.json"
SNAPSHOT = EVIDENCE / "r295_input_snapshot.json"
AMENDMENT = EVIDENCE / "r295_scope_amendment.json"
PANEL = EVIDENCE / "r295_panel_registration.json"
RESULT_JSON = EVIDENCE / "r295_concentration_audit.json"
RESULT_MD = EVIDENCE / "r295_result.md"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    digest = sha(payload)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def verify_inputs(snapshot: dict, prereg: dict) -> list[dict]:
    verified = []
    for row in snapshot["files"]:
        path = ROOT / row["path"]
        stat = path.stat()
        current = {"path": row["path"], "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": sha_file(path)}
        if current != row:
            raise RuntimeError(f"Frozen R295 input changed: {row['path']}")
        verified.append(current)
    if {row["path"]: row["sha256"] for row in verified} != prereg["frozen_inputs"]["source_files_sha256"]:
        raise RuntimeError("Input manifest and preregistration source hashes disagree")
    return verified


def aggregate(rows: list[dict]) -> dict:
    total = sum(float(row["pnl_amount"] or 0.0) for row in rows)
    gross_profit = sum(max(float(row["pnl_amount"] or 0.0), 0.0) for row in rows)
    gross_loss = -sum(min(float(row["pnl_amount"] or 0.0), 0.0) for row in rows)
    per_symbol: dict[str, dict] = {}
    for row in rows:
        symbol = str(row["symbol"])
        item = per_symbol.setdefault(symbol, {"symbol": symbol, "trades": 0, "pnl_amount": 0.0})
        item["trades"] += 1
        item["pnl_amount"] += float(row["pnl_amount"] or 0.0)
    ordered = sorted((row for row in per_symbol.values() if row["pnl_amount"] > 0), key=lambda row: row["pnl_amount"], reverse=True)
    positive_symbol_net = sum(row["pnl_amount"] for row in ordered)
    top = {}
    for count in (1, 3, 5):
        amount = sum(row["pnl_amount"] for row in ordered[:count])
        top[str(count)] = {
            "symbols": [row["symbol"] for row in ordered[:count]],
            "pnl_amount": round(amount, 2),
            "legacy_share_of_portfolio_net": round(amount / (total if total > 0 else 1.0), 6),
            "share_of_positive_symbol_net": round(amount / positive_symbol_net, 6) if positive_symbol_net > 0 else None,
            "share_of_gross_positive_trade_pnl": round(amount / gross_profit, 6) if gross_profit > 0 else None,
            "static_trade_pnl_after_removal": round(total - amount, 2),
        }
    legacy = {f"C{n+1}": False for n in ()}
    legacy = {"C2": total > 0 and top["1"]["legacy_share_of_portfolio_net"] <= 0.25,
              "C3": total > 0 and top["3"]["legacy_share_of_portfolio_net"] <= 0.55,
              "C4": total > 0 and top["5"]["legacy_share_of_portfolio_net"] <= 0.75}
    alternate = {"C2": top["1"]["share_of_positive_symbol_net"] is not None and top["1"]["share_of_positive_symbol_net"] <= 0.25,
                 "C3": top["3"]["share_of_positive_symbol_net"] is not None and top["3"]["share_of_positive_symbol_net"] <= 0.55,
                 "C4": top["5"]["share_of_positive_symbol_net"] is not None and top["5"]["share_of_positive_symbol_net"] <= 0.75}
    return {"trade_count": len(rows), "distinct_symbols": len(per_symbol), "positive_net_symbols": len(ordered),
            "total_trade_pnl": round(total, 2), "gross_positive_trade_pnl": round(gross_profit, 2),
            "gross_losses_abs": round(gross_loss, 2), "positive_symbol_net_pnl": round(positive_symbol_net, 2),
            "net_to_gross": round(total / gross_profit, 6) if gross_profit else None,
            "top_contributors": top, "legacy_gate": legacy, "alternate_gate": alternate}


def main() -> int:
    for path in (RESULT_JSON, RESULT_MD):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R295 result exists; refusing overwrite: {path.name}")
    pre_bytes = PRE.read_bytes()
    prereg_hash = sha(pre_bytes)
    if prereg_hash != PRE.with_name(PRE.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R295 preregistration checksum mismatch")
    prereg = json.loads(pre_bytes)
    snapshot_bytes = SNAPSHOT.read_bytes()
    snapshot_hash = sha(snapshot_bytes)
    if snapshot_hash != prereg["frozen_inputs"]["manifest_sha256"]:
        raise RuntimeError("R295 input snapshot checksum mismatch")
    snapshot = json.loads(snapshot_bytes)
    for path in (AMENDMENT, PANEL):
        if sha_file(path) != path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]:
            raise RuntimeError(f"R295 proof checksum mismatch: {path.name}")
    panel = json.loads(PANEL.read_text(encoding="utf-8"))
    if not panel.get("registration_pass") or panel.get("preregistration_sha256") != prereg_hash:
        raise RuntimeError("R295 panel registration/readback is not valid")

    before = verify_inputs(snapshot, prereg)
    manifest_by_path = {row["path"]: row for row in snapshot["files"]}
    roles = prereg["frozen_inputs"]["trade_files_by_role"]
    trades = {}
    input_manifest = {}
    for role, relative in roles.items():
        if relative not in manifest_by_path:
            raise RuntimeError(f"Trade file not frozen: {relative}")
        rows = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        segment = "2023-05-04~2024-12-31" if role.endswith("discovery") else "2025-01-02~2026-10-16"
        if not isinstance(rows, list) or any(row.get("segment") != segment or "symbol" not in row or "pnl_amount" not in row for row in rows):
            raise RuntimeError(f"Invalid trade rows or segment: {relative}")
        trades[role] = rows
        input_manifest[role] = {"path": relative, "sha256": manifest_by_path[relative]["sha256"], "trade_count": len(rows)}

    samples = {}
    for strategy in ("candidate", "control"):
        for segment in ("discovery", "validation"):
            key = f"{strategy}_{segment}"
            samples[key] = aggregate(trades[key])
        samples[f"{strategy}_combined_additive"] = aggregate(trades[f"{strategy}_discovery"] + trades[f"{strategy}_validation"])

    frozen = json.loads((EVIDENCE / "r257_quality_relative_strength.json").read_text(encoding="utf-8"))
    full = samples["candidate_combined_additive"]
    validation = samples["candidate_validation"]
    frozen_full = frozen["concentration"]["full"]
    frozen_val = frozen["concentration"]["validation"]
    def shares_match(actual: dict, expected: dict) -> bool:
        return all(abs(actual["top_contributors"][str(n)]["legacy_share_of_portfolio_net"] - expected[f"top{n}_net_share"]) <= 0.00000051 for n in (1, 3, 5))
    reconciliation = {
        "full_trade_count": full["trade_count"] == frozen_full["n_trades"],
        "full_total_pnl": full["total_trade_pnl"] == frozen_full["total_pnl_amount"],
        "full_gross_profit": full["gross_positive_trade_pnl"] == frozen_full["gross_profit"],
        "full_gross_loss": full["gross_losses_abs"] == frozen_full["gross_loss"],
        "full_distinct_symbols": full["distinct_symbols"] == frozen_full["distinct_symbols"],
        "full_legacy_shares": shares_match(full, frozen_full),
        "validation_trade_count": validation["trade_count"] == frozen_val["n_trades"],
        "validation_total_pnl": validation["total_trade_pnl"] == frozen_val["total_pnl_amount"],
        "validation_gross_profit": validation["gross_positive_trade_pnl"] == frozen_val["gross_profit"],
        "validation_gross_loss": validation["gross_losses_abs"] == frozen_val["gross_loss"],
        "validation_legacy_shares": shares_match(validation, frozen_val),
    }
    bounded = []
    for strategy in ("candidate", "control"):
        for segment in ("discovery", "validation", "combined_additive"):
            item = samples[f"{strategy}_{segment}"]
            values = [item["top_contributors"][str(n)]["share_of_positive_symbol_net"] for n in (1, 3, 5)]
            bounded.append(item["positive_symbol_net_pnl"] > 0 and all(v is not None and 0 <= v <= 1 for v in values) and values[0] <= values[1] <= values[2])
    checks = {"frozen_result_reconciliation_pass": all(reconciliation.values()),
              "positive_symbol_net_shares_bounded_monotone": all(bounded),
              "all_four_logs_nonempty": all(input_manifest[role]["trade_count"] > 0 for role in roles)}
    after = verify_inputs(snapshot, prereg)
    if before != after:
        raise RuntimeError("Frozen inputs changed while running R295 audit")

    result = {
        "round": "R295", "classification": "posthoc_concentration_metric_reconciliation_not_alpha_confirmation",
        "captured_at_utc": datetime.now(UTC).isoformat(), "candidate_id": panel["candidate"]["id"],
        "candidate_status": panel["candidate"]["status"], "preregistration_sha256": prereg_hash,
        "input_snapshot_sha256": snapshot_hash, "scope_amendment_sha256": sha_file(AMENDMENT),
        "panel_readback_sha256": sha_file(PANEL), "runner_sha256": sha_file(Path(__file__)),
        "input_files": input_manifest, "aggregates": samples, "r257_reconciliation": reconciliation,
        "checks": checks, "original_r257_decision_unchanged": frozen["decision"],
        "production_state": "pause_production",
        "execution_limits": {"system_backtest_service_called": False, "strategy_engine_called": False,
            "providers_called": False, "market_or_extension_data_written": False,
            "strategy_or_backtest_caches_written": False, "trades_or_returns_generated": False,
            "paper_account_or_orders_changed": False, "frozen_inputs_unchanged_after": True,
            "frozen_input_files_verified_before": len(before), "frozen_input_files_verified_after": len(after)},
        "interpretation": [
            "The legacy top-positive-symbol contribution divided by portfolio net P&L can exceed 100% when losses shrink its denominator; it is not a bounded concentration fraction.",
            "Positive-symbol-net shares are bounded but exclude net-negative symbols; they are a descriptive winner concentration metric, not a complete portfolio risk measure.",
            "Gross positive trade P&L and portfolio net P&L denominators answer different questions and are not interchangeable with exposure or risk concentration.",
            "Static removal from archived trade P&L is attribution only; it does not replay ranking, capacity, replacement trades, compounding, or execution.",
            "Prior outcome inspection makes this audit post-hoc. It cannot establish alpha or retroactively accept the original R257 candidate."]}
    result["all_calculation_checks_pass"] = all(checks.values())

    lines = ["# R295 R257 Concentration Metric Audit", "",
        "## Scope", "",
        "Post-hoc arithmetic audit of the four archived 3x R257 candidate/control trade logs. R257 outcomes and preliminary calculations were inspected before this round was frozen, so this is not blind or independent validation. The original preregistration is preserved unchanged and its timing correction is separately registered. No alpha claim follows.", "",
        "## Candidate results", "",
        "| Sample | Trades | Net P&L | Gross wins | Gross losses | Legacy C2/C3/C4 | Positive-symbol-net shares C2/C3/C4 | Static P&L after top 1/3/5 removal |",
        "| --- | ---: | ---: | ---: | ---: | --- | --- | --- |"]
    for label, key in (("Discovery", "candidate_discovery"), ("Validation", "candidate_validation"), ("Combined additive", "candidate_combined_additive")):
        item = samples[key]
        legacy = "/".join("pass" if item["legacy_gate"][f"C{n}"] else "fail" for n in (2, 3, 4))
        shares = "/".join(f"{item['top_contributors'][str(n)]['share_of_positive_symbol_net']:.1%}" for n in (1, 3, 5))
        residual = "/".join(f"{item['top_contributors'][str(n)]['static_trade_pnl_after_removal']:,.0f}" for n in (1, 3, 5))
        lines.append(f"| {label} | {item['trade_count']} | {item['total_trade_pnl']:,.2f} | {item['gross_positive_trade_pnl']:,.2f} | {item['gross_losses_abs']:,.2f} | {legacy} | {shares} | {residual} |")
    lines += ["", "## Matched control", "", "| Sample | Trades | Net P&L | Gross wins | Gross losses | Positive-symbol-net shares C2/C3/C4 |", "| --- | ---: | ---: | ---: | ---: | --- |"]
    for label, key in (("Discovery", "control_discovery"), ("Validation", "control_validation"), ("Combined additive", "control_combined_additive")):
        item = samples[key]
        shares = "/".join(f"{item['top_contributors'][str(n)]['share_of_positive_symbol_net']:.1%}" for n in (1, 3, 5))
        lines.append(f"| {label} | {item['trade_count']} | {item['total_trade_pnl']:,.2f} | {item['gross_positive_trade_pnl']:,.2f} | {item['gross_losses_abs']:,.2f} | {shares} |")
    lines += ["", "## Interpretation", "",
        "R257's top-symbol net contribution divided by total net P&L is denominator-sensitive and can exceed 100%. In this archived candidate, the frozen C2-C4 checks fail under that denominator, while dividing by positive symbol-level net contributions gives bounded descriptive shares and a different classification. This proves metric sensitivity only; it does not prove the alternate limits are appropriate. Positive-contributor share excludes negative symbols, and gross-positive-P&L share is another distinct measure.", "",
        "Static top-symbol removal subtracts archived trade contributions only. It is not a rerun, counterfactual equity curve, or executable portfolio result.", "",
        "R257's original preregistration and decision remain unchanged. This repeated historical sample is not independent; no system backtest or provider was called, no market/factor/strategy data was written, and production remains pause_production.", "",
        "R257 reconciliation: " + json.dumps(reconciliation, sort_keys=True),
        "Calculation checks: " + json.dumps(checks, sort_keys=True), "",
        f"Input manifest SHA-256: {snapshot_hash}",
        f"Scope amendment SHA-256: {result['scope_amendment_sha256']}",
        f"Panel registration/readback SHA-256: {result['panel_readback_sha256']}"]
    result_hash = write_once(RESULT_JSON, (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    report_hash = write_once(RESULT_MD, ("\n".join(lines) + "\n").encode("utf-8"))
    print(json.dumps({"round": "R295", "all_calculation_checks_pass": result["all_calculation_checks_pass"],
        "reconciliation": reconciliation, "candidate_combined": full, "json_sha256": result_hash,
        "report_sha256": report_hash}, ensure_ascii=False, indent=2))
    return 0 if result["all_calculation_checks_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
