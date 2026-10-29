"""Freeze R287 system-mask diagnostic inputs without inspecting gate values."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl

EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[5]
DATA = ROOT / "data"
AS_OF = date(2026, 10, 14)
LOAD_START = AS_OF - timedelta(days=120)
PRE = EVIDENCE / "r287_preregistration.json"
SNAPSHOT = EVIDENCE / "r287_input_snapshot.json"
CONTROL_ID = "ai_hs300_macro_gate_quality"
CANDIDATE_ID = "ai_hs300_macro_gate_quality_r275_remove_term"


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def save_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    digest = sha_file(path)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def required_file(relative: str) -> Path:
    path = ROOT / relative
    if not path.is_file():
        raise RuntimeError(f"required R287 source is missing: {relative}")
    return path


def inventory(paths: list[Path]) -> dict:
    ordered = sorted(paths, key=lambda path: path.relative_to(ROOT).as_posix())
    digest = hashlib.sha256()
    for path in ordered:
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha_file(path).encode("ascii"))
        digest.update(b"\n")
    return {
        "file_count": len(ordered),
        "total_size_bytes": sum(path.stat().st_size for path in ordered),
        "inventory_sha256": digest.hexdigest(),
    }


def main() -> int:
    for path in (PRE, SNAPSHOT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R287 freeze artifact exists; refusing overwrite: {path.name}")

    member_path = required_file("data/research/r78_daily_membership/daily_snapshots.parquet")
    member_frame = pl.read_parquet(member_path)
    if not {"query_date", "symbol"}.issubset(member_frame.columns):
        raise RuntimeError("R78 daily membership schema is incomplete")
    symbols = (
        member_frame.filter(pl.col("query_date").cast(pl.Date) == AS_OF)
        .select(pl.col("symbol").cast(pl.String)).unique().sort("symbol")
        .get_column("symbol").to_list()
    )
    if len(symbols) != 300:
        raise RuntimeError(f"R78 membership on {AS_OF} has {len(symbols)} rows, expected 300")

    enriched_parts = [
        p for p in (DATA / "kline_daily_enriched").glob("date=*/part.parquet")
        if LOAD_START.isoformat() <= p.parent.name.removeprefix("date=") <= AS_OF.isoformat()
    ]
    # The system ext-factor loader scans the full configured time-series, then
    # joins exact (symbol, date) keys. Freeze every partition it can read.
    ext_parts = sorted((DATA / "ext_data/alt_hs300/timeseries").glob("date=*/part.parquet"))
    financial_parts = sorted((DATA / "financials").glob("*/part.parquet"))
    if not enriched_parts or not ext_parts or len(financial_parts) != 5:
        raise RuntimeError("R287 local enriched, ext-data, or financial PIT partitions are incomplete")

    fixed_sources = [
        "backend/app/backtest/strategy.py",
        "backend/app/backtest/engine.py",
        "backend/app/backtest/fundamentals.py",
        "backend/app/strategy/engine.py",
        "backend/app/strategy/scoring.py",
        "backend/app/indicators/pipeline.py",
        "backend/app/factors/ext_factors.py",
        "backend/app/factors/store.py",
        "backend/app/factors/registry.py",
        "backend/app/tickflow/repository.py",
        "scripts/backtest_r112_dual_sleeve_risk_parity.py",
        "data/strategies/ai/ai_hs300_macro_gate_quality.py",
        f"data/strategies/ai/{CANDIDATE_ID}.py",
        "data/user_data/custom_factors/uf_quality_value_defensive.json",
        "data/user_data/custom_factors/uf_low_vol.json",
        "data/user_data/custom_factors/uf_cash_quality.json",
        "data/ext_data/alt_hs300/config.json",
        "data/instruments/instruments.parquet",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r275_preregistration_v3d.json",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r275_single_gate_ablation_v3d.json",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r286_preregistration.json",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r286_gate_exposure_audit_v2.json",
    ]
    source_hashes = {relative: sha_file(required_file(relative)) for relative in fixed_sources}
    source_hashes[member_path.relative_to(ROOT).as_posix()] = sha_file(member_path)
    for path in financial_parts:
        source_hashes[path.relative_to(ROOT).as_posix()] = sha_file(path)
    for path in enriched_parts + ext_parts:
        source_hashes[path.relative_to(ROOT).as_posix()] = sha_file(path)

    symbols_hash = sha_bytes("\n".join(symbols).encode("utf-8"))
    inventories = {
        "enriched_warmup": inventory(enriched_parts),
        "alt_hs300_all_source_partitions": inventory(ext_parts),
        "financial_tables": inventory(financial_parts),
    }
    snapshot = {
        "round": "R287",
        "classification": "pre_registration_source_inventory_no_gate_values_read",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of": AS_OF.isoformat(),
        "history_load_start": LOAD_START.isoformat(),
        "membership": {
            "path": member_path.relative_to(ROOT).as_posix(),
            "query_date": AS_OF.isoformat(),
            "symbol_count": len(symbols),
            "symbols": symbols,
            "symbols_sha256": symbols_hash,
        },
        "source_sha256": source_hashes,
        "partition_inventories": inventories,
        "read_scope": "R78 membership identities and input file bytes/metadata only; no macro gate values, feature masks, scores, returns, trades, or equity curves read.",
    }
    snapshot_hash = save_once(
        SNAPSHOT,
        (json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )

    prereg = {
        "round": "R287",
        "classification": "single_date_system_backtest_candidate_mask_and_score_assembly_diagnostic_no_backtest_no_returns",
        "registered_at_utc": datetime.now(timezone.utc).isoformat(),
        "question": "On one frozen historical R78 HS300 date, how many member rows reach the actual system backtest candidate mask after its configured basic filter and R275 strategy filter, and how many additional rows become eligible when only the frozen term-spread gate is removed? Can the system assemble the same four R275 score inputs on those rows?",
        "hypothesis": "The term-spread gate may reduce the system-eligible pool on the selected date; measure only after the actual system filters and historical membership are intersected.",
        "date_selection": "2026-10-14 was selected before gate-value inspection because the R78 exact historical membership snapshot and local enriched/ext-data partitions cover this date; selection is independent of candidate returns.",
        "population": {
            "membership_source": "data/research/r78_daily_membership/daily_snapshots.parquet; exact query_date membership, matching the R112 historical-membership loader",
            "as_of": AS_OF.isoformat(),
            "member_count": len(symbols),
            "member_symbols_sha256": symbols_hash,
            "control": CONTROL_ID,
            "candidate": CANDIDATE_ID,
            "control_filter": "MA60 and holder non-null; ERP >= 5.5; credit spread >= -0.8; term spread >= 0.3",
            "candidate_filter": "same MA60, holder, ERP, credit, scores and weights; remove only term spread >= 0.3",
        },
        "system_path": {
            "service": "StrategyBacktestService -> StrategyDependencyResolver.resolve -> BacktestEngine.load_panel_for_backtest -> _build_candidate_filter_mask -> StrategyEngine._basic_filter_expr -> _hs300_panel_row_mask -> _apply_score",
            "backend": "polars_expr",
            "load_window": [LOAD_START.isoformat(), AS_OF.isoformat()],
            "warmup": "max(120 calendar days, int(max(feature_plan.warmup_bars, 1) * 1.6)); no row after AS_OF loaded",
            "score_weights": {
                "ext_alt_hs300_holder_top10_free_float_share": 0.35,
                "uf_quality_value_defensive": 0.25,
                "uf_low_vol": 0.20,
                "uf_cash_quality": 0.20,
            },
            "basic_filter_config": {
                "price_min": 3,
                "price_max": 500,
                "market_cap_min": 1000000000.0,
                "amount_min": 20000000.0,
                "exclude_st": True,
                "exclude_new_days": 60,
            },
            "semantics_and_limitations": [
                "System _basic_filter_expr does not implement exclude_new_days; report it as configured but not executed.",
                "market_cap_min reads current instruments total_shares by symbol, not historical PIT shares; this leg has point-in-time bias.",
                "Financial custom score factors use system backtest PIT snapshot and announcement-date gating; source revision vintage remains a separate limitation.",
                "ext factors join historical time-series by (symbol,date), with include_snapshot=False; supplier publication time and revision vintage are unavailable.",
                "META limit=30 belongs to screener ranking and is not applied in this backtest mask diagnostic; no screener is run.",
                "Backtest mask describes candidate eligibility before matcher max_positions=10, tradability, and order selection; no execution is simulated.",
            ],
        },
        "locked_metrics": [
            "Control and candidate: loaded AS_OF rows, basic-filter passes, strategy-filter passes, historical membership passes, final eligible rows and symbol identities.",
            "Exact candidate-minus-control and control-minus-candidate identities; candidate must be a superset when only one predicate is removed.",
            "For each of the four frozen scoring columns: schema presence and non-null counts over member rows and final eligible rows; system score non-null rows.",
            "Reconcile eligible mask as basic AND strategy filter AND membership; report configured-but-not-executed exclude_new_days separately.",
        ],
        "execution_rules": [
            "Validate preregistration and every fixed-source and read-partition SHA-256 before loading feature values.",
            "Use exact R78 membership plus local enriched, ext-data, financial, instrument and registered-factor inputs in the snapshot.",
            "Use the real dependency resolver, historical panel loader, basic-filter expression, strategy filter mask, membership mask and score materializer.",
            "Do not call StrategyBacktestService.run, BacktestEngine.run, matcher/simulation, screener routes, providers or future-date data paths.",
            "Read price history only as required by the system loader for MA60 and configured basic filters; do not inspect or calculate returns, P&L, trades, benchmark or equity curves.",
            "Write only R287 evidence and one pending panel candidate; do not write market data, factor caches, strategy-result caches, backtest caches, paper accounts or orders.",
            "Keep panel candidate pending, strategy research_only, and production pause_production.",
            "If exact system feature assembly or integrity checks fail, mark not_reproducible; do not substitute a manual approximation.",
        ],
        "source_sha256": source_hashes,
        "partition_inventories": inventories,
        "input_snapshot": {
            "path": SNAPSHOT.relative_to(ROOT).as_posix(),
            "sha256": snapshot_hash,
        },
        "panel_candidate": {
            "kind": "strategy",
            "name": "R287 系统回测候选掩码诊断（期限门禁消融）",
            "source_id": CANDIDATE_ID,
            "strategy_id": CANDIDATE_ID,
            "start": AS_OF.isoformat(),
            "end": AS_OF.isoformat(),
            "status": "pending",
        },
        "registration_script_sha256": sha_file(EVIDENCE / "r287_register_panel_once.py"),
        "audit_script_sha256": sha_file(EVIDENCE / "r287_system_mask_audit.py"),
        "freeze_script_sha256": sha_file(Path(__file__).resolve()),
        "production_status": "pause_production",
    }
    prereg_hash = save_once(
        PRE,
        (json.dumps(prereg, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(json.dumps({
        "round": "R287",
        "snapshot_sha256": snapshot_hash,
        "preregistration_sha256": prereg_hash,
        "membership_count": len(symbols),
        "enriched_partitions": inventories["enriched_warmup"]["file_count"],
        "ext_partitions": inventories["alt_hs300_window"]["file_count"],
        "financial_tables": inventories["financial_tables"]["file_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
