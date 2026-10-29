"""Freeze inputs for the R288 multi-date system-mask diagnostic."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[5]
DATA = ROOT / "data"
START = date(2021, 1, 4)
END = date(2026, 10, 14)
LOAD_START = START - timedelta(days=120)
PRE = EVIDENCE / "r288_preregistration.json"
SNAPSHOT = EVIDENCE / "r288_input_snapshot.json"
CONTROL_ID = "ai_hs300_macro_gate_quality"
CANDIDATE_ID = "ai_hs300_macro_gate_quality_r275_remove_term"
ROUND_INDEX = EVIDENCE / "r288_round_index.md"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return digest(path.read_bytes())


def save_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    value = sha_file(path)
    with path.with_name(path.name + ".sha256").open(
        "x", encoding="ascii", newline="\n"
    ) as stream:
        stream.write(f"{value}  {path.name}\n")
    return value


def required(relative: str) -> Path:
    path = ROOT / relative
    if not path.is_file():
        raise RuntimeError(f"required frozen source is missing: {relative}")
    return path


def inventory(paths: list[Path]) -> dict:
    ordered = sorted(paths, key=lambda path: path.relative_to(ROOT).as_posix())
    listing = []
    combined = hashlib.sha256()
    for path in ordered:
        relative = path.relative_to(ROOT).as_posix()
        file_hash = sha_file(path)
        listing.append({"path": relative, "sha256": file_hash, "size_bytes": path.stat().st_size})
        combined.update(relative.encode("utf-8"))
        combined.update(b"\0")
        combined.update(file_hash.encode("ascii"))
        combined.update(b"\n")
    return {
        "file_count": len(ordered),
        "total_size_bytes": sum(item["size_bytes"] for item in listing),
        "inventory_sha256": combined.hexdigest(),
        "files": listing,
    }


def main() -> int:
    for path in (PRE, SNAPSHOT, ROUND_INDEX):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R288 create-once artifact exists: {path.name}")

    membership_path = required("data/research/r78_daily_membership/daily_snapshots.parquet")
    membership = pl.read_parquet(membership_path, columns=["query_date", "symbol"]).with_columns(
        pl.col("query_date").cast(pl.Date), pl.col("symbol").cast(pl.String)
    ).filter(pl.col("query_date").is_between(START, END)).unique(
        subset=["query_date", "symbol"]
    ).sort(["query_date", "symbol"])
    trading_dates = membership.get_column("query_date").unique().sort().to_list()
    if len(trading_dates) != 1398 or trading_dates[0] != START or trading_dates[-1] != END:
        raise RuntimeError(
            f"unexpected frozen R78 calendar: {len(trading_dates)} dates, "
            f"{trading_dates[0] if trading_dates else None}..{trading_dates[-1] if trading_dates else None}"
        )
    if membership.height != 419400:
        raise RuntimeError(f"unexpected R78 member-date rows: {membership.height}")
    union_symbols = membership.get_column("symbol").unique().sort().to_list()
    canonical_membership = "".join(
        f"{day.isoformat()}\t{symbol}\n" for day, symbol in membership.iter_rows()
    ).encode("utf-8")

    enriched = sorted(
        path for path in (DATA / "kline_daily_enriched").glob("date=*/part.parquet")
        if LOAD_START.isoformat() <= path.parent.name.removeprefix("date=") <= END.isoformat()
    )
    ext = sorted((DATA / "ext_data/alt_hs300/timeseries").glob("date=*/part.parquet"))
    financial = sorted((DATA / "financials").glob("*/part.parquet"))
    enriched_dates = {path.parent.name.removeprefix("date=") for path in enriched}
    ext_dates = {path.parent.name.removeprefix("date=") for path in ext}
    expected_dates = {day.isoformat() for day in trading_dates}
    if expected_dates - enriched_dates or expected_dates - ext_dates:
        raise RuntimeError("frozen daily membership dates are not fully covered by local data partitions")
    if len(enriched) < 1200 or len(ext) < 2000 or len(financial) != 5:
        raise RuntimeError("R288 local enriched, ext-data, or PIT financial partitions are incomplete")

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
        f"data/strategies/ai/{CONTROL_ID}.py",
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
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r287_preregistration.json",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r287_system_mask_audit.json",
    ]
    source_hashes = {relative: sha_file(required(relative)) for relative in fixed_sources}
    source_hashes[membership_path.relative_to(ROOT).as_posix()] = sha_file(membership_path)
    enriched_inventory = inventory(enriched)
    ext_inventory = inventory(ext)
    financial_inventory = inventory(financial)
    for listing in (enriched_inventory["files"], ext_inventory["files"], financial_inventory["files"]):
        for item in listing:
            source_hashes[item["path"]] = item["sha256"]

    frozen_sources = {
        "freeze_script_sha256": sha_file(Path(__file__).resolve()),
        "registration_script_sha256": sha_file(EVIDENCE / "r288_register_panel_once.py"),
        "audit_script_sha256": sha_file(EVIDENCE / "r288_system_mask_audit.py"),
    }
    snapshot = {
        "round": "R288",
        "classification": "pre_registration_source_inventory_no_outcome_values_read",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "system_load_window": {"start": LOAD_START.isoformat(), "end": END.isoformat()},
        "population": {
            "membership_path": membership_path.relative_to(ROOT).as_posix(),
            "membership_row_count": membership.height,
            "membership_date_count": len(trading_dates),
            "membership_date_sha256": digest("\n".join(day.isoformat() for day in trading_dates).encode()),
            "membership_symbol_union_count": len(union_symbols),
            "membership_symbol_union_sha256": digest("\n".join(union_symbols).encode()),
            "daily_membership_key_sha256": digest(canonical_membership),
        },
        "partition_inventories": {
            "enriched_load_window": {k: v for k, v in enriched_inventory.items() if k != "files"},
            "alt_hs300_all_source_partitions": {k: v for k, v in ext_inventory.items() if k != "files"},
            "financial_tables": {k: v for k, v in financial_inventory.items() if k != "files"},
        },
        "source_sha256": source_hashes,
        "read_scope": "R78 membership identities, source bytes and partition metadata only; no gate values, feature masks, scores, returns, trades, or equity curves read.",
    }
    snapshot_hash = save_once(
        SNAPSHOT, (json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    )

    prereg = {
        "round": "R288",
        "classification": "full_available_window_system_candidate_mask_and_score_coverage_diagnostic_no_returns",
        "registered_at_utc": datetime.now(UTC).isoformat(),
        "question": "Across all frozen daily R78 HS300 membership dates from 2021-01-04 through 2026-10-14, how many member-date rows pass the actual system backtest candidate mask under R275, and how many more pass when only the frozen term-spread condition is removed?",
        "hypothesis": "The term-spread gate removes some system-eligible member-date rows in dates where the frozen threshold fails; the size and persistence of that release must be measured using the actual system feature loader, masks, and historical membership.",
        "window": {
            "start": START.isoformat(), "end": END.isoformat(),
            "basis": "all 1,398 R78 daily membership dates with matching local enriched and alt_hs300 partitions; fixed before feature values were loaded",
        },
        "population": {
            "membership_source": "R112 historical membership loader over data/research/r78_daily_membership/daily_snapshots.parquet; exact daily query_date membership",
            "membership_rows": membership.height,
            "date_count": len(trading_dates),
            "symbol_union_count": len(union_symbols),
            "control": CONTROL_ID,
            "candidate": CANDIDATE_ID,
            "unit": "historical member-date rows, with unique trading-date summaries reported separately",
        },
        "frozen_filter_comparison": {
            "control": "System R275 source with MA60 and holder non-null, ERP >= 5.5, credit spread >= -0.8, and term spread >= 0.3.",
            "candidate": "The registered R275 single-gate ablation: same basic filter, MA60/holder/ERP/credit rules and scoring weights; term spread condition removed.",
            "threshold_source": "Frozen R275 preregistration and strategy source hashes in source_sha256. No thresholds or weights are tuned.",
        },
        "system_path": {
            "service": "StrategyDependencyResolver.resolve -> BacktestEngine.load_panel_for_backtest -> StrategyEngine._basic_filter_expr -> StrategyBacktestService._build_candidate_filter_mask -> _hs300_panel_row_mask -> StrategyBacktestService._apply_score",
            "backend": "polars_expr",
            "system_load_window": [LOAD_START.isoformat(), END.isoformat()],
            "warmup_days": 120,
            "symbols_loaded": "union of all exact R78 members in the frozen formal window; equivalent after intersection with the exact historical-membership mask",
            "basic_filter_config": {
                "price_min": 3, "price_max": 500, "market_cap_min": 1000000000.0,
                "amount_min": 20000000.0, "exclude_st": True, "exclude_new_days": 60,
            },
            "scoring_weights": {
                "ext_alt_hs300_holder_top10_free_float_share": 0.35,
                "uf_quality_value_defensive": 0.25, "uf_low_vol": 0.20, "uf_cash_quality": 0.20,
            },
            "known_semantics_and_limitations": [
                "System _basic_filter_expr does not implement exclude_new_days; report it as configured but not executed.",
                "market_cap_min uses current instrument total_shares by symbol, not historical PIT shares.",
                "Historical ext factors join exact (symbol,date) records; supplier publication times and revision vintages are unavailable.",
                "Financial custom factors use the system backtest PIT announcement-date gate, but source revision vintages are not preserved.",
                "This candidate mask is before ranking limit, matcher max_positions, tradability, fills and costs; none are simulated in R288.",
            ],
        },
        "locked_metrics": [
            "Per strategy and per date: loaded rows, historical member rows, basic-filter passes within members, strategy-filter passes within members, final eligible member-date rows, and exact eligible symbol identities.",
            "Candidate-minus-control and control-minus-candidate member-date identities; a one-gate removal must produce a candidate superset.",
            "Aggregate and daily release counts, active release dates, release fraction of control-blocked eligible universe, and per-date maxima/median.",
            "For each of four scoring inputs: presence and non-null member, control-eligible, candidate-eligible and gate-released row counts; system aggregate-score non-null counts.",
            "Reconcile eligible mask as basic AND strategy-filter AND historical membership for every formal date.",
        ],
        "execution_rules": [
            "Verify this preregistration, its sidecar, every frozen source hash and each partition inventory before reading feature values.",
            "Use the real dependency resolver, system historical panel loader, system basic filter, strategy filter masks, historical membership mask, scoring materializer and score method.",
            "Do not call StrategyBacktestService.run, BacktestEngine.run, matcher/simulation, screener routes, providers, or future-date paths.",
            "Do not inspect or calculate returns, P&L, trades, benchmark or equity curves.",
            "Write only R288 evidence and one pending panel candidate; do not write market data, factor caches, strategy caches, backtest caches, paper accounts or orders.",
            "Keep the candidate pending, R275 research_only, and production pause_production.",
            "Abort on source mismatch, unavailable system columns, mismatched masks, or failed accounting identities; do not substitute manual filter logic.",
        ],
        "input_snapshot": {"path": SNAPSHOT.relative_to(ROOT).as_posix(), "sha256": snapshot_hash},
        "partition_inventories": snapshot["partition_inventories"],
        "source_sha256": source_hashes,
        "panel_candidate": {
            "kind": "strategy",
            "name": "R288 多日系统候选掩码诊断（R275期限利差消融）",
            "source_id": CANDIDATE_ID, "strategy_id": CANDIDATE_ID,
            "start": START.isoformat(), "end": END.isoformat(), "status": "pending",
        },
        "registration_script_sha256": frozen_sources["registration_script_sha256"],
        "audit_script_sha256": frozen_sources["audit_script_sha256"],
        "freeze_script_sha256": frozen_sources["freeze_script_sha256"],
        "production_status": "pause_production",
    }
    prereg_hash = save_once(
        PRE, (json.dumps(prereg, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    )
    ledger_entry = f"""\n+## R288 多日系统候选掩码诊断（预注册；{datetime.now(UTC).date().isoformat()}）

- 问题：在 R78 全部逐日历史成分日上，用真实系统回测数据装载、基础过滤、策略过滤和历史成分掩码，比较 R275 与仅移除期限利差门禁后的候选股票日数量；逐日对账四项评分输入覆盖。
- 样本：{len(trading_dates):,} 个交易日，{membership.height:,} 个成分股日，{len(union_symbols)} 只历史成分并集，时间窗 {START} 至 {END}；使用系统 `polars_expr` 面板管线，不读收益、不撮合。
- 预先声明限制：系统新股排除配置未由 `_basic_filter_expr` 执行；市值过滤读取当前 instruments 股本；外部宏观发布时间/修订版本不可核验。候选维持 `research_only`、面板 `pending`、生产 `pause_production`。
- 证据：预注册 SHA-256 `{prereg_hash}`；输入快照 SHA-256 `{snapshot_hash}`；登记成功后追加 POST/读回和审计证据索引。
"""
    with (EVIDENCE.parent / "LEDGER.md").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(ledger_entry)
    print(json.dumps({
        "round": "R288", "membership_dates": len(trading_dates),
        "membership_rows": membership.height, "symbol_union": len(union_symbols),
        "enriched_partitions": enriched_inventory["file_count"],
        "ext_partitions": ext_inventory["file_count"],
        "financial_tables": financial_inventory["file_count"],
        "snapshot_sha256": snapshot_hash, "preregistration_sha256": prereg_hash,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
