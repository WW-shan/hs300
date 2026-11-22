#!/usr/bin/env python3
"""R504: recent relative-failure attribution for frozen R499."""

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

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r504_r499_recent_relative_failure_attribution_preregistration.json"
R499 = E / "r499_weak_state_value_quality_tilt_2026-11-22.json"
R503 = E / "r503_r499_rolling_year_regime_confirmation_2026-11-22.json"
OUTPUT = E / "r504_r499_recent_relative_failure_attribution_2026-11-22.json"
RESULT = E / "r504_r499_recent_relative_failure_attribution_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R504 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def concentration_rows(trades: list[dict[str, Any]], removals: list[int]) -> dict[str, Any]:
    pnls = sorted((float(item.get("pnl_amount") or 0.0) for item in trades), reverse=True)
    total = sum(pnls)
    output = {
        "trade_count": len(pnls),
        "positive_trades": sum(value > 0 for value in pnls),
        "negative_trades": sum(value < 0 for value in pnls),
        "total_pnl": round(total, 6),
        "top_1_pnl": round(sum(pnls[:1]), 6),
        "top_5_pnl": round(sum(pnls[:5]), 6),
        "top_10_pnl": round(sum(pnls[:10]), 6),
        "top_20_pnl": round(sum(pnls[:20]), 6),
    }
    for count in removals:
        output[f"removed_{count}"] = round(total - sum(pnls[:count]), 6)
    return output


def month_returns(curve: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in curve:
        groups[str(item["date"])[:7]].append(item)
    output = {}
    for month, items in sorted(groups.items()):
        output[month] = {
            "return": float(items[-1]["value"]) / float(items[0]["value"]) - 1.0,
            "start": str(items[0]["date"])[:10],
            "end": str(items[-1]["date"])[:10],
        }
    return output


def hs300_returns() -> dict[str, float]:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    frame = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(
            (pl.col("symbol") == "000300.SH")
            & pl.col("close").is_not_null()
            & (pl.col("close") > 0)
        )
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    dates = [str(value)[:10] for value in frame["date"].to_list()]
    closes = [float(value) for value in frame["close"].to_list()]
    return {dates[index]: closes[index] / closes[index - 1] - 1.0 for index in range(1, len(dates))}


def build_panel(p5: Any) -> tuple[pl.DataFrame, list[str]]:
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.factor_research.incremental import rank_score_frame
    from app.hs300.current import current_symbols

    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    panel = attach_fundamental_factors(
        panel, snapshot, ["pb_latest", "ocf_debt", "debt_ratio_latest"]
    )
    ext = load_existing_ext_factors(
        DATA,
        {
            "ext_alt_hs300_cash_flow_stability_4q_low": "cash_flow_stability_4q_low",
            "ext_alt_hs300_dividend_yield": "dividend_yield",
            "ext_alt_hs300_fund_holding_ratio_low": "fund_holding_ratio_low",
            "ext_alt_hs300_idio_vol_40d_low": "idio_vol_40d_low",
        },
    )
    panel = panel.join(ext, on=["symbol", "date"], how="left").sort(["symbol", "date"])
    for member, direction in (
        ("pb_latest", "low"),
        ("ocf_debt", "high"),
        ("debt_ratio_latest", "low"),
    ):
        panel = rank_score_frame(panel, member, direction, output_column=f"_r504_vq_{member}")
    panel = panel.with_columns(
        (
            pl.col("_r504_vq_pb_latest").fill_null(0.0)
            + pl.col("_r504_vq_ocf_debt").fill_null(0.0)
            + pl.col("_r504_vq_debt_ratio_latest").fill_null(0.0)
        ).alias("value_quality"),
        (
            pl.col("_r504_vq_pb_latest").is_not_null()
            & pl.col("_r504_vq_ocf_debt").is_not_null()
            & pl.col("_r504_vq_debt_ratio_latest").is_not_null()
        ).alias("_r504_vq_valid"),
    ).filter(pl.col("_r504_vq_valid"))
    return panel, sorted(panel["symbol"].unique().to_list())


def run_frozen(
    p5: Any,
    service: Any,
    panel: pl.DataFrame,
    symbols: list[str],
    start: str,
    end: str,
) -> dict[str, Any]:
    from app.factor_research.incremental import build_rank_composite

    weights = {
        "ext_alt_hs300_cash_flow_stability_4q_low": 0.2375,
        "ext_alt_hs300_dividend_yield": 0.2375,
        "ext_alt_hs300_fund_holding_ratio_low": 0.2375,
        "ext_alt_hs300_idio_vol_40d_low": 0.2375,
        "value_quality": 0.05,
    }
    field = "_r504_frozen"
    work, _ = build_rank_composite(
        panel, weights, {member: "high" for member in weights}, field, minimum_weight_coverage=0.75
    )
    config = p5.portfolio._backtest_config(
        {field: 1.0},
        {field: "high"},
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=10,
        cost=p5.portfolio._cost_parameters("2x"),
        max_hold_days=25,
    )
    config.params = {
        **(config.params or {}),
        "entry_score": 70.0,
        "exit_score": 40.0,
        "top_rank": 20,
    }
    config.max_exposure_pct = 1.0
    config.symbols = list(symbols)
    prepared_base = service.prepare_matrix_optimization([config])
    prepared = p5._extend_prepared_with_panel(prepared_base, work, [field])
    result = service.run(config, prepared=prepared)
    if result.error:
        raise RuntimeError(result.error)
    return {
        "curve": list(result.equity_curve),
        "trades": [dict(trade) for trade in result.trades],
        "stats": p5._select_stats(result.stats),
    }


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    verify_sidecar(R499)
    verify_sidecar(R503)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r499 = json.loads(R499.read_text(encoding="utf-8"))
    if float(r499["selected"]["weak_value_quality"]) != 0.05:
        raise RuntimeError("R499 selected sample changed")
    r503 = json.loads(R503.read_text(encoding="utf-8"))
    if r503["source_r499_sha256"] != sha(R499):
        raise RuntimeError("R503 source changed")
    r453 = load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r504_r453")
    p5 = r453.load_p5()
    panel, symbols = build_panel(p5)
    service = p5.portfolio._service()
    start = prereg["windows"]["recent_start"]
    end = prereg["windows"]["end"]
    run = run_frozen(p5, service, panel, symbols, start, end)
    curve = run["curve"]
    trades = run["trades"]
    for trade in trades:
        symbol = str(trade.get("symbol") or "")
        industry = (
            panel.filter(pl.col("symbol") == symbol)
            .select("industry_l1")
            .drop_nulls()
            .unique()
            .head(1)
        )
        trade["industry"] = industry["industry_l1"][0] if industry.height else "unclassified"
    benchmark = hs300_returns()
    months = month_returns(curve)
    benchmark_months: dict[str, float] = {}
    for month, row in months.items():
        values = [
            benchmark[day]
            for day in benchmark
            if day.startswith(month) and row["start"] <= day <= row["end"]
        ]
        benchmark_months[month] = round(math.prod(1.0 + value for value in values) - 1.0, 8)
    industry_pnl: dict[str, float] = defaultdict(float)
    for trade in trades:
        industry_pnl[str(trade["industry"])] += float(trade.get("pnl_amount") or 0.0)
    industry_rows = sorted(
        (
            {"industry": industry, "pnl": round(value, 6)}
            for industry, value in industry_pnl.items()
        ),
        key=lambda row: row["pnl"],
        reverse=True,
    )
    payload = {
        "round": "R504",
        "classification": prereg["classification"],
        "source_r499_sha256": sha(R499),
        "source_r503_sha256": sha(R503),
        "selection_performed": False,
        "window": {"start": start, "end": end},
        "stats": run["stats"],
        "trade_concentration": concentration_rows(trades, prereg["diagnostics"]["top_pnl_removal"]),
        "industry_pnl": industry_rows,
        "monthly": {
            "strategy": {month: row["return"] for month, row in months.items()},
            "benchmark": benchmark_months,
            "excess": {
                month: round(row["return"] - benchmark_months.get(month, 0.0), 8)
                for month, row in months.items()
            },
        },
        "decision": {**prereg["output_policy"]},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    lines = [
        "# R504 R499 近一年相对失效归因",
        "",
        "- 交易数：{trade_count}；正/负：{positive_trades}/{negative_trades}",
        "- 总盈亏：{total_pnl:.2f}",
        f"- 去掉最大 1/5/10/20 笔后：{payload['trade_concentration']['removed_1']:.2f} / {payload['trade_concentration']['removed_5']:.2f} / {payload['trade_concentration']['removed_10']:.2f} / {payload['trade_concentration']['removed_20']:.2f}",
        "",
        "## 月度策略/基准/超额",
        "",
        "| 月份 | 策略 | 基准 | 超额 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for month in sorted(months):
        lines.append(
            f"| {month} | {months[month]['return']:.2%} | {benchmark_months[month]:.2%} | "
            f"{months[month]['return'] - benchmark_months[month]:+.2%} |"
        )
    lines += ["", "## 行业盈亏", "", "| 行业 | 盈亏 |", "| --- | ---: |"]
    for row in industry_rows:
        lines.append(f"| {row['industry']} | {row['pnl']:+.2f} |")
    lines = [
        line.format(**payload["trade_concentration"])
        if "{" in line and "industry" not in line
        else line
        for line in lines
    ]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii"
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
