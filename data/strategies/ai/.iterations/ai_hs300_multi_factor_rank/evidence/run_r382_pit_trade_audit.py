"""R382: rerun the frozen hold-30 trade audit on R381 PIT lineage."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
PREREG = EVIDENCE / "r382_preregistration.json"
OUTPUT = EVIDENCE / "r382_pit_trade_audit.json"
RESULT = EVIDENCE / "r382_result.md"
R381_PREREG = EVIDENCE / "r381_preregistration.json"
R381_INPUT_AUDIT = EVIDENCE / "r381_input_audit.json"
R381_PIT_AUDIT = EVIDENCE / "r381_pit_data_audit.json"
R381_RUNNER = EVIDENCE / "run_r381_pit_data_audit.py"
R381_MEMBERSHIP = ROOT / "data/research/r381_membership/daily_snapshots.parquet"
R381_INDUSTRY = ROOT / "data/research/r381_pit_industry/industry_history.parquet"
R381_EXT_FRAME = ROOT / "data/research/r381_pit_industry/alt_hs300_required_fields.parquet"
R380_AUDIT = EVIDENCE / "r380_trade_audit_v2.json"
R380_RUNNER = EVIDENCE / "run_r380_trade_audit_v2.py"
RECENT_START = date(2025, 11, 5)
TOLERANCE = 1e-9
EXPECTED_TRADE_COUNTS = {"A": 290, "B": 732, "C": 2467, "total": 3489}
DEFAULT_CONCENTRATION_GATES = {
    "industry_trade_label_coverage_min": 0.99,
    "distinct_symbols_min": 80,
    "top1_gross_profit_share_max": 0.25,
    "top3_gross_profit_share_max": 0.55,
    "top5_gross_profit_share_max": 0.75,
    "top_industry_gross_profit_share_max": 0.45,
    "max_single_symbol_loss_to_gross_profit_max": 0.20,
    "recent_positive_symbols_min": 8,
}
sys.path.insert(0, str(ROOT / "backend"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_checksum(path: Path) -> str:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists():
        raise RuntimeError(f"missing checksum sidecar: {sidecar}")
    expected = sidecar.read_text(encoding="ascii").split()[0]
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(f"checksum mismatch: {path.name}")
    return actual


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def parse_day(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def load_report(path: Path = OUTPUT) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def attach_static_industry(trades: list[dict[str, Any]], mapping: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {**trade, "industry_pit": str(mapping.get(str(trade["symbol"])) or "未知")}
        for trade in trades
    ]


def attach_entry_industry(
    trades: list[dict[str, Any]],
    industry: pl.DataFrame,
) -> list[dict[str, Any]]:
    if not trades:
        return []
    required = {"symbol", "effective_date", "industry_pit"}
    if not required <= set(industry.columns):
        raise ValueError(f"industry history missing columns: {sorted(required - set(industry.columns))}")
    left = pl.DataFrame({
        "trade_index": list(range(len(trades))),
        "symbol": [str(trade["symbol"]) for trade in trades],
        "entry_date": [parse_day(trade["entry_date"]) for trade in trades],
    }).sort(["symbol", "entry_date", "trade_index"])
    right = industry.select(
        pl.col("symbol").cast(pl.String),
        pl.col("effective_date").cast(pl.Date),
        pl.col("industry_pit").cast(pl.String),
    ).sort(["symbol", "effective_date"])
    joined = left.join_asof(
        right,
        left_on="entry_date",
        right_on="effective_date",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    )
    labels = {
        int(row["trade_index"]): row["industry_pit"]
        for row in joined.iter_rows(named=True)
        if row["industry_pit"] is not None
    }
    return [
        {**trade, "industry_pit": str(labels.get(index) or "未知")}
        for index, trade in enumerate(trades)
    ]


def aggregate_concentration(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate portfolio-weighted P&L without reusing sleeve-account raw P&L."""
    total_pnl = sum(float(trade.get("portfolio_pnl_amount") or 0.0) for trade in trades)
    gross_profit = sum(max(float(trade.get("portfolio_pnl_amount") or 0.0), 0.0) for trade in trades)
    gross_loss = -sum(min(float(trade.get("portfolio_pnl_amount") or 0.0), 0.0) for trade in trades)
    per_symbol: dict[str, dict[str, Any]] = {}
    per_industry: dict[str, float] = defaultdict(float)
    labeled = 0
    for trade in trades:
        symbol = str(trade["symbol"])
        pnl = float(trade.get("portfolio_pnl_amount") or 0.0)
        row = per_symbol.setdefault(
            symbol,
            {
                "symbol": symbol,
                "name": trade.get("name"),
                "industry_pit": trade.get("industry_pit") or "未知",
                "n_trades": 0,
                "pnl_amount": 0.0,
                "wins": 0,
            },
        )
        row["n_trades"] += 1
        row["pnl_amount"] += pnl
        if pnl > 0:
            row["wins"] += 1
            per_industry[str(trade.get("industry_pit") or "未知")] += pnl
        if trade.get("industry_pit") not in (None, "", "未知"):
            labeled += 1

    symbols = sorted(per_symbol.values(), key=lambda row: row["pnl_amount"], reverse=True)
    positives = [row["pnl_amount"] for row in symbols if row["pnl_amount"] > 0]
    losses = [row["pnl_amount"] for row in symbols if row["pnl_amount"] < 0]
    top1 = positives[0] if positives else 0.0
    top3 = sum(positives[:3])
    top5 = sum(positives[:5])
    net = total_pnl if total_pnl > 0 else 1.0
    industries = sorted(
        ({"industry": key, "gross_profit": value} for key, value in per_industry.items()),
        key=lambda row: row["gross_profit"],
        reverse=True,
    )
    return {
        "n_trades": len(trades),
        "distinct_symbols": len(per_symbol),
        "total_pnl_amount": round(total_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_to_gross": round(total_pnl / gross_profit, 4) if gross_profit else None,
        "top1_symbol": symbols[0] if symbols else None,
        "top3_symbols": symbols[:3],
        "top5_symbols": symbols[:5],
        "worst_symbols": sorted(symbols, key=lambda row: row["pnl_amount"])[:5],
        "top1_net_share": round(top1 / net, 6),
        "top3_net_share": round(top3 / net, 6),
        "top5_net_share": round(top5 / net, 6),
        "top1_gross_share": round(top1 / gross_profit, 6) if gross_profit else None,
        "top3_gross_share": round(top3 / gross_profit, 6) if gross_profit else None,
        "top5_gross_share": round(top5 / gross_profit, 6) if gross_profit else None,
        "max_symbol_loss_abs": round(abs(min(losses)) if losses else 0.0, 2),
        "max_symbol_loss_to_gross": round(abs(min(losses)) / gross_profit, 6) if losses and gross_profit else None,
        "industries": industries,
        "top_industry_gross_share": round(industries[0]["gross_profit"] / gross_profit, 6)
        if industries and gross_profit else None,
        "industry_labeled_trades": labeled,
        "industry_unlabeled_trades": len(trades) - labeled,
        "industry_trade_label_coverage": round(labeled / len(trades), 6) if trades else 0.0,
        "positive_symbol_count": sum(row["pnl_amount"] > 0 for row in symbols),
        "per_symbol": symbols,
    }


def evaluate_concentration_gates(
    result: dict[str, Any],
    gates: dict[str, Any] | None = None,
    *,
    recent_positive_symbols: int | None = None,
) -> dict[str, bool]:
    thresholds = {**DEFAULT_CONCENTRATION_GATES, **(gates or {})}
    recent_count = (
        int(recent_positive_symbols)
        if recent_positive_symbols is not None
        else int(result.get("positive_symbol_count") or 0)
    )
    return {
        "industry_trade_label_coverage": float(result.get("industry_trade_label_coverage") or 0.0)
        >= thresholds["industry_trade_label_coverage_min"],
        "distinct_symbols": int(result.get("distinct_symbols") or 0) >= thresholds["distinct_symbols_min"],
        "top1_gross": result.get("top1_gross_share") is not None
        and result["top1_gross_share"] <= thresholds["top1_gross_profit_share_max"],
        "top3_gross": result.get("top3_gross_share") is not None
        and result["top3_gross_share"] <= thresholds["top3_gross_profit_share_max"],
        "top5_gross": result.get("top5_gross_share") is not None
        and result["top5_gross_share"] <= thresholds["top5_gross_profit_share_max"],
        "top_industry_gross": result.get("top_industry_gross_share") is not None
        and result["top_industry_gross_share"] <= thresholds["top_industry_gross_profit_share_max"],
        "max_symbol_loss": result.get("max_symbol_loss_to_gross") is not None
        and result["max_symbol_loss_to_gross"] <= thresholds["max_single_symbol_loss_to_gross_profit_max"],
        "recent_positive_symbols": recent_count >= thresholds["recent_positive_symbols_min"],
    }


def _curve_checks(current: dict[str, Any], expected: dict[str, Any]) -> dict[str, bool]:
    specs = {
        "center_2x_annual": ("center_2x", "full", "annual"),
        "center_2x_sharpe": ("center_2x", "full", "sharpe"),
        "center_2x_mdd": ("center_2x", "full", "mdd"),
        "center_2x_recent_return": ("center_2x", "recent", "total_return"),
        "center_2x_sealed_return": ("center_2x", "sealed", "total_return"),
        "control_2x_annual": ("control_2x", "full", "annual"),
        "control_2x_sharpe": ("control_2x", "full", "sharpe"),
        "control_2x_mdd": ("control_2x", "full", "mdd"),
        "control_2x_sealed_return": ("control_2x", "sealed", "total_return"),
        "center_3x_annual": ("center_3x", "full", "annual"),
        "center_3x_sharpe": ("center_3x", "full", "sharpe"),
        "center_3x_mdd": ("center_3x", "full", "mdd"),
    }
    return {
        name: math.isclose(
            float(current[portfolio][facet][field]),
            float(expected[portfolio][facet][field]),
            abs_tol=TOLERANCE,
            rel_tol=0.0,
        )
        for name, (portfolio, facet, field) in specs.items()
    }


def reconcile_r381(current: dict[str, Any], r381: dict[str, Any]) -> dict[str, Any]:
    expected = r381["current_portfolio"]
    curve_checks = _curve_checks(current, expected)
    summary = r381["run_summary"]
    c_count = sum(int(summary[f"C_2x_{index}"]["trades"]) for index in range(3))
    counts = {
        "A": int(summary["A_2x"]["trades"]),
        "B": int(summary["B30_2x"]["trades"]),
        "C": c_count,
        "total": int(summary["A_2x"]["trades"]) + int(summary["B30_2x"]["trades"]) + c_count,
    }
    count_checks = {
        "trade_count_A": counts["A"] == EXPECTED_TRADE_COUNTS["A"],
        "trade_count_B": counts["B"] == EXPECTED_TRADE_COUNTS["B"],
        "trade_count_C": counts["C"] == EXPECTED_TRADE_COUNTS["C"],
        "trade_count_total": counts["total"] == EXPECTED_TRADE_COUNTS["total"],
    }
    return {
        "curve_checks": curve_checks,
        "trade_counts_from_r381": counts,
        "trade_count_checks": count_checks,
        "curve_pass": all(curve_checks.values()),
        "trade_count_pass": all(count_checks.values()),
        "all_pass": all(curve_checks.values()) and all(count_checks.values()),
    }


def verify_input_bindings(prereg: dict[str, Any]) -> dict[str, Any]:
    bound = {
        "r381_preregistration": (R381_PREREG, prereg["trigger"]["r381_preregistration_sha256"]),
        "r381_input_audit": (R381_INPUT_AUDIT, prereg["trigger"]["r381_input_audit_sha256"]),
        "r381_pit_data_audit": (R381_PIT_AUDIT, prereg["trigger"]["r381_pit_data_audit_sha256"]),
        "r380_trade_audit": (R380_AUDIT, prereg["trigger"]["r380_trade_audit_sha256"]),
        "membership": (R381_MEMBERSHIP, prereg["input_bindings"]["membership"]["sha256"]),
        "industry": (R381_INDUSTRY, prereg["input_bindings"]["pit_industry"]["sha256"]),
        "extension_frame": (R381_EXT_FRAME, prereg["input_bindings"]["extension_frame"]["sha256"]),
    }
    checks: dict[str, bool] = {}
    actual: dict[str, str] = {}
    for name, (path, expected) in bound.items():
        value = sha256(path)
        actual[name] = value
        checks[name] = value == expected
    runner_hash = sha256(R381_RUNNER)
    engine_hash = sha256(ROOT / "backend/app/backtest/engine.py")
    input_audit = json.loads(R381_INPUT_AUDIT.read_text(encoding="utf-8"))
    pit_audit = json.loads(R381_PIT_AUDIT.read_text(encoding="utf-8"))
    checks["r381_data_gate"] = bool(input_audit.get("data_integrity_pass"))
    checks["r381_portfolio_gate"] = bool(pit_audit.get("all_pass"))
    checks["r381_status"] = pit_audit.get("decision", {}).get("status") == "candidate_for_paper_trading_review"
    checks["engine"] = engine_hash == prereg["input_bindings"]["engine"]["sha256"]
    return {
        "checks": checks,
        "actual_sha256": {**actual, "r381_runner": runner_hash, "engine": engine_hash},
        "all_pass": all(checks.values()),
    }


def _r380_correction(
    r380: dict[str, Any],
    industry_history: pl.DataFrame,
    r380_module: Any,
) -> dict[str, Any]:
    trades = r380["trades"]
    current_mapping = r380_module.industry_map()
    snapshot_trades = attach_static_industry(trades, current_mapping)
    pit_trades = attach_entry_industry(trades, industry_history)
    scaled_snapshot = aggregate_concentration(snapshot_trades)
    scaled_pit = aggregate_concentration(pit_trades)
    recent_pit = aggregate_concentration([
        trade for trade in pit_trades if parse_day(trade["entry_date"]) >= RECENT_START
    ])
    return {
        "reported_pnl_basis": "unscaled_pnl_amount",
        "reported_industry_basis": "current_snapshot",
        "reported": {
            key: r380["concentration_full"].get(key)
            for key in (
                "distinct_symbols",
                "top1_gross_share",
                "top3_gross_share",
                "top5_gross_share",
                "top_industry_gross_share",
                "max_symbol_loss_to_gross",
            )
        },
        "recomputed_scaled_snapshot": {
            key: scaled_snapshot[key]
            for key in (
                "distinct_symbols",
                "top1_gross_share",
                "top3_gross_share",
                "top5_gross_share",
                "top_industry_gross_share",
                "max_symbol_loss_to_gross",
                "industry_trade_label_coverage",
            )
        },
        "recomputed_scaled_pit": {
            key: scaled_pit[key]
            for key in (
                "distinct_symbols",
                "top1_gross_share",
                "top3_gross_share",
                "top5_gross_share",
                "top_industry_gross_share",
                "max_symbol_loss_to_gross",
                "industry_trade_label_coverage",
            )
        },
        "recomputed_scaled_pit_recent_positive_symbols": recent_pit["positive_symbol_count"],
        "reported_all_gates_pass_under_scaled_pit": all(
            evaluate_concentration_gates(
                scaled_pit,
                recent_positive_symbols=recent_pit["positive_symbol_count"],
            ).values()
        ),
    }


def _sleeve_pnl(trades: list[dict[str, Any]]) -> dict[str, float]:
    return {
        sleeve: sum(
            float(trade.get("portfolio_pnl_amount") or 0.0)
            for trade in trades
            if trade.get("sleeve") == sleeve
        )
        for sleeve in ("A", "B", "C")
    }


def build_report() -> dict[str, Any]:
    prereg_hash = verify_checksum(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    input_integrity = verify_input_bindings(prereg)
    if not input_integrity["all_pass"]:
        raise RuntimeError("R382 input integrity gate failed; no portfolio returns were read")
    r381 = json.loads(R381_PIT_AUDIT.read_text(encoding="utf-8"))
    r380 = json.loads(R380_AUDIT.read_text(encoding="utf-8"))
    r381_module = module(R381_RUNNER, "r382_r381")
    r380_module = module(R380_RUNNER, "r382_r380")

    chain = r381_module.run_current_chain(json.loads(R381_INPUT_AUDIT.read_text(encoding="utf-8")))
    outputs = chain["outputs"]
    ctx = {
        "r331": module(EVIDENCE / "run_r331_three_asset_bucket.py", "r382_r331"),
        "r344": module(EVIDENCE / "run_r344_extended_window.py", "r382_r344"),
        "r112": module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r382_r112"),
        "r114": module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r382_r114"),
    }
    current = r381_module.current_portfolio_stats(outputs, ctx)
    reconciliation = reconcile_r381(current, r381)
    engine_identity_match = chain["engine_sha256"] == r381["engine_identity"]["engine_sha256"] and all(
        chain["variant_sha256"][key] == value
        for key, value in r381["engine_identity"]["variant_sha256"].items()
    )

    c2, c_trades_2x = r381_module.combine_c(outputs, "2x")
    bundle_center_2x = r381_module.portfolio_bundle(
        ctx,
        outputs["A_2x"][0],
        outputs["B30_2x"][0],
        c2,
        "2x",
    )
    all_trades = r380_module.all_current_trades(outputs, c_trades_2x)
    scaled = r380_module.scale_trades(all_trades, bundle_center_2x)
    industry_history = pl.read_parquet(R381_INDUSTRY).with_columns(
        pl.col("effective_date").cast(pl.Date),
        pl.col("industry_pit").cast(pl.String),
    )
    pit_trades = attach_entry_industry(scaled, industry_history)

    full = aggregate_concentration(pit_trades)
    recent = aggregate_concentration([
        trade for trade in pit_trades if parse_day(trade["entry_date"]) >= RECENT_START
    ])
    b_full = aggregate_concentration([trade for trade in pit_trades if trade.get("sleeve") == "B"])
    recent_positive = int(recent["positive_symbol_count"])
    concentration_checks = evaluate_concentration_gates(
        full,
        prereg["gates"]["concentration"],
        recent_positive_symbols=recent_positive,
    )
    execution = r380_module.execution_audit(outputs["B30_2x"][2], outputs["B30_2x"][1])
    execution_gates = prereg["gates"]["execution"]
    execution_checks = {
        "hard_entry_miss_rate": execution["hard_entry_miss_rate"] is not None
        and execution["hard_entry_miss_rate"] <= execution_gates["hard_entry_miss_rate_max"],
        "hard_exit_miss_rate": execution["hard_exit_miss_rate"] is not None
        and execution["hard_exit_miss_rate"] <= execution_gates["hard_exit_miss_rate_max"],
        "blocked_exit_trade_share": execution["blocked_exit_trade_share"] is not None
        and execution["blocked_exit_trade_share"] <= execution_gates["blocked_exit_trade_share_max"],
        "max_blocked_exit_days": execution["max_blocked_exit_days"]
        <= execution_gates["max_blocked_exit_days_per_trade_max"],
    }
    capacity = r380_module.capacity_audit(scaled, r380_module.amount_lookup())
    capacity_gates = prereg["gates"]["capacity"]
    capacity_checks = {
        "minimum_capital": capacity["one_hundred_million_gate_passes"],
        "no_missing_daily_amount": len(capacity["missing_daily_amount"])
        <= capacity_gates["missing_daily_amount_count_max"],
    }
    audit_checks = {
        "concentration": all(concentration_checks.values()),
        "execution": all(execution_checks.values()),
        "capacity": all(capacity_checks.values()),
    }
    trade_counts = {
        sleeve: sum(trade.get("sleeve") == sleeve for trade in pit_trades)
        for sleeve in ("A", "B", "C")
    }
    trade_counts["total"] = len(pit_trades)
    trade_count_checks = {
        f"trade_count_{key}": value == EXPECTED_TRADE_COUNTS[key]
        for key, value in trade_counts.items()
    }
    r380_correction = _r380_correction(r380, industry_history, r380_module)
    audit_pass = all(audit_checks.values()) and all(trade_count_checks.values())
    all_pass = reconciliation["all_pass"] and engine_identity_match and audit_pass
    return {
        "round": "R382",
        "classification": prereg["classification"],
        "preregistration_sha256": prereg_hash,
        "input_integrity": input_integrity,
        "input_integrity_pass": input_integrity["all_pass"],
        "engine_identity_match": engine_identity_match,
        "engine_identity": {
            key: chain[key]
            for key in ("membership_sha256", "engine_sha256", "variant_sha256")
        },
        "run_summary": {
            key: {"elapsed_seconds": value[3]["elapsed_seconds"], "trades": len(value[1])}
            for key, value in outputs.items()
        },
        "r381_reconciliation": reconciliation,
        "reconciliation_pass": reconciliation["all_pass"],
        "current_portfolio": current,
        "audit_method": {
            "pnl_basis": "portfolio_pnl_amount",
            "industry_basis": "entry_date_pit_industry",
            "capacity_basis": "portfolio_entry_exit_notional_vs_daily_amount",
            "execution_basis": "current_engine_B30_hard_miss_and_blocked_exit_counters",
        },
        "trade_counts": trade_counts,
        "trade_count_checks": trade_count_checks,
        "concentration_full": {key: value for key, value in full.items() if key != "per_symbol"},
        "concentration_recent": {key: value for key, value in recent.items() if key != "per_symbol"},
        "concentration_b_sleeve": {key: value for key, value in b_full.items() if key != "per_symbol"},
        "recent_positive_symbols": recent_positive,
        "concentration_checks": concentration_checks,
        "execution": execution,
        "execution_checks": execution_checks,
        "capacity": capacity,
        "capacity_checks": capacity_checks,
        "sleeve_pnl": _sleeve_pnl(pit_trades),
        "r380_method_correction": r380_correction,
        "audit_checks": audit_checks,
        "audit_pass": audit_pass,
        "all_pass": all_pass,
        "trades": pit_trades,
        "decision": {
            "status": (
                "candidate_for_paper_trading_review"
                if all_pass
                else "research_only_pit_trade_audit_failed"
            ),
            "input_integrity_pass": input_integrity["all_pass"],
            "engine_identity_match": engine_identity_match,
            "reconciliation_pass": reconciliation["all_pass"],
            "trade_level_audit_pass": audit_pass,
            "no_leverage": True,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }


def markdown(report: dict[str, Any]) -> str:
    current = report["current_portfolio"]
    full = report["concentration_full"]
    recent = report["concentration_recent"]
    execution = report["execution"]
    capacity = report["capacity"]
    correction = report["r380_method_correction"]
    cap100 = next(row for row in capacity["capital_levels"] if row["capital_cny"] == 100_000_000)
    failing_audit = [name for name, passed in report["audit_checks"].items() if not passed]
    failing_trade_counts = [name for name, passed in report["trade_count_checks"].items() if not passed]
    lines = [
        "# R382 · PIT 交易级、执行与容量复核",
        "",
        "R382 不重新选参数，只把 R381 修复后的名单、行业字段和当前引擎交易重新做组合加权审计。",
        "",
        "| 组合 | 年化 | Sharpe | MDD | 近一年 | 封存段 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| hold-30 中心 2× | {current['center_2x']['full']['annual']:.2%} | {current['center_2x']['full']['sharpe']:.3f} | {current['center_2x']['full']['mdd']:.2%} | {current['center_2x']['recent']['total_return']:.2%} | {current['center_2x']['sealed']['total_return']:.2%} |",
        f"| 20 日对照 2× | {current['control_2x']['full']['annual']:.2%} | {current['control_2x']['full']['sharpe']:.3f} | {current['control_2x']['full']['mdd']:.2%} | {current['control_2x']['recent']['total_return']:.2%} | {current['control_2x']['sealed']['total_return']:.2%} |",
        f"| hold-30 中心 3× | {current['center_3x']['full']['annual']:.2%} | {current['center_3x']['full']['sharpe']:.3f} | {current['center_3x']['full']['mdd']:.2%} | — | — |",
        "",
        f"- R381 曲线复现：{sum(report['r381_reconciliation']['curve_checks'].values())}/{len(report['r381_reconciliation']['curve_checks'])} 项通过；交易数量复现：{sum(report['r381_reconciliation']['trade_count_checks'].values())}/{len(report['r381_reconciliation']['trade_count_checks'])} 项通过。",
        f"- 审计交易数：A {report['trade_counts']['A']}、B {report['trade_counts']['B']}、C {report['trade_counts']['C']}，合计 {report['trade_counts']['total']}。",
        f"- 行业标签覆盖率：{full['industry_trade_label_coverage']:.2%}；未标注交易 {full['industry_unlabeled_trades']} 笔。",
        "",
        "| 集中度（组合加权 P&L） | 结果 | 门槛 |",
        "| --- | ---: | ---: |",
        f"| 股票数 | {full['distinct_symbols']} | >=80 |",
        f"| top1 毛利润占比 | {full['top1_gross_share']:.2%} | <=25% |",
        f"| top3 毛利润占比 | {full['top3_gross_share']:.2%} | <=55% |",
        f"| top5 毛利润占比 | {full['top5_gross_share']:.2%} | <=75% |",
        f"| 最大行业毛利润占比 | {full['top_industry_gross_share']:.2%} | <=45% |",
        f"| 最大单票亏损/毛利润 | {full['max_symbol_loss_to_gross']:.2%} | <=20% |",
        f"| 近一年盈利股票数 | {report['recent_positive_symbols']} | >=8 |",
        "",
        "| 执行 | 结果 | 门槛 |",
        "| --- | ---: | ---: |",
        f"| 硬性买入未成交率 | {execution['hard_entry_miss_rate']:.2%} | <=5% |",
        f"| 硬性卖出未成交率 | {execution['hard_exit_miss_rate']:.2%} | <=5% |",
        f"| 有阻塞卖出的交易占比 | {execution['blocked_exit_trade_share']:.2%} | <=5% |",
        f"| 单笔最长阻塞卖出天数 | {execution['max_blocked_exit_days']} | <=10 |",
        "",
        "| 容量（1亿元） | 结果 | 门槛 |",
        "| --- | ---: | ---: |",
        f"| P95 单笔占当日成交额 | {cap100['p95_participation']:.2%} | <=5% |",
        f"| P99 单笔占当日成交额 | {cap100['p99_participation']:.2%} | <=10% |",
        f"| P95 按10%参与率清仓天数 | {cap100['p95_days_to_liquidate_at_10pct']:.2f} | <=1.0 |",
        "",
        "## R380 审计口径更正",
        "",
        "R380 已算出组合权重后的 `portfolio_pnl_amount`，但集中度汇总仍读取原始袖账户 `pnl_amount`；同时行业标签来自当前快照。R382 同时改为组合加权 P&L 和交易入场日 PIT 行业。",
        "",
        "| 口径 | top1 | top3 | top5 | 最大行业 |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| R380 原报告 | {correction['reported']['top1_gross_share']:.2%} | {correction['reported']['top3_gross_share']:.2%} | {correction['reported']['top5_gross_share']:.2%} | {correction['reported']['top_industry_gross_share']:.2%} |",
        f"| R380 交易组合加权 + 当前行业 | {correction['recomputed_scaled_snapshot']['top1_gross_share']:.2%} | {correction['recomputed_scaled_snapshot']['top3_gross_share']:.2%} | {correction['recomputed_scaled_snapshot']['top5_gross_share']:.2%} | {correction['recomputed_scaled_snapshot']['top_industry_gross_share']:.2%} |",
        f"| R380 交易组合加权 + PIT 行业 | {correction['recomputed_scaled_pit']['top1_gross_share']:.2%} | {correction['recomputed_scaled_pit']['top3_gross_share']:.2%} | {correction['recomputed_scaled_pit']['top5_gross_share']:.2%} | {correction['recomputed_scaled_pit']['top_industry_gross_share']:.2%} |",
        "",
        f"- 综合判定：{'通过' if report['all_pass'] else '未通过'}，状态 `{report['decision']['status']}`。",
        f"- 未通过的交易数量项：{failing_trade_counts or '无'}；未通过的交易审计项：{failing_audit or '无'}。",
        f"- 近一年组合加权盈利股票数 {recent['positive_symbol_count']}；近一年最高行业占比 {recent['top_industry_gross_share']:.2%}。",
        "- 无杠杆、不生成纸面目标、订单或生产配置。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    for path in (OUTPUT, OUTPUT.with_name(OUTPUT.name + ".sha256"), RESULT, RESULT.with_name(RESULT.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"R382 output exists: {path}")
    report = build_report()
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha256(OUTPUT)}  {OUTPUT.name}\n",
        encoding="ascii",
    )
    RESULT.write_text(markdown(report), encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha256(RESULT)}  {RESULT.name}\n",
        encoding="ascii",
    )
    print(json.dumps({
        "input_integrity_pass": report["input_integrity_pass"],
        "engine_identity_match": report["engine_identity_match"],
        "reconciliation_pass": report["reconciliation_pass"],
        "trade_counts": report["trade_counts"],
        "audit_checks": report["audit_checks"],
        "all_pass": report["all_pass"],
        "status": report["decision"]["status"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
