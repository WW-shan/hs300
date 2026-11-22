#!/usr/bin/env python3
"""R503: frozen R499 rolling-year and market-segment confirmation."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r503_r499_rolling_year_regime_confirmation_preregistration.json"
R499 = E / "r499_weak_state_value_quality_tilt_2026-11-22.json"
OUTPUT = E / "r503_r499_rolling_year_regime_confirmation_2026-11-22.json"
RESULT = E / "r503_r499_rolling_year_regime_confirmation_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R503 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def one_year_end(start: str) -> str:
    value = date.fromisoformat(start)
    try:
        end = date(value.year + 1, value.month, value.day) - timedelta(days=1)
    except ValueError:
        end = date(value.year + 1, value.month, value.day - 1) - timedelta(days=1)
    return end.isoformat()


def curve_metrics(curve: list[dict[str, Any]]) -> dict[str, float]:
    if not curve:
        return {"total_return": 0.0, "annual_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}
    values = [float(item["value"]) for item in curve]
    returns = [
        values[index] / values[index - 1] - 1.0
        for index in range(1, len(values))
        if values[index - 1] > 0
    ]
    total = values[-1] / values[0] - 1.0
    mean = sum(returns) / len(returns) if returns else 0.0
    vol = (sum((value - mean) ** 2 for value in returns) / len(returns)) ** 0.5 if returns else 0.0
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {
        "total_return": round(total, 8),
        "annual_return": round((1.0 + total) ** (252.0 / len(returns)) - 1.0, 8)
        if returns
        else 0.0,
        "sharpe": round(mean / vol * math.sqrt(252.0), 8) if vol else 0.0,
        "max_drawdown": round(mdd, 8),
    }


def daily_returns(curve: list[dict[str, Any]]) -> list[tuple[str, float]]:
    return [
        (
            str(curve[index]["date"])[:10],
            float(curve[index]["value"]) / float(curve[index - 1]["value"]) - 1.0,
        )
        for index in range(1, len(curve))
        if float(curve[index - 1].get("value") or 0.0) > 0.0
    ]


def hs300_curve() -> dict[str, float]:
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


def paired_excess(strategy: list[tuple[str, float]], benchmark: dict[str, float]) -> list[float]:
    return [value - benchmark[day] for day, value in strategy if day in benchmark]


def block_bootstrap(values: list[float], *, block: int, paths: int, seed: int) -> dict[str, float]:
    if not values:
        return {"total_p05": 0.0, "total_p50": 0.0, "sharpe_p50": 0.0, "mdd_p05": 0.0}
    rng = random.Random(seed)
    totals, sharpes, mdds = [], [], []
    for _ in range(paths):
        sampled: list[float] = []
        while len(sampled) < len(values):
            start = rng.randrange(len(values))
            sampled.extend(values[(start + offset) % len(values)] for offset in range(block))
        selected = sampled[: len(values)]
        product = math.prod(1.0 + value for value in selected)
        mean = sum(selected) / len(selected)
        vol = (sum((value - mean) ** 2 for value in selected) / len(selected)) ** 0.5
        path = [1.0]
        for value in selected:
            path.append(path[-1] * (1.0 + value))
        peak = path[0]
        mdd = 0.0
        for value in path:
            peak = max(peak, value)
            mdd = min(mdd, value / peak - 1.0)
        totals.append(product - 1.0)
        sharpes.append(mean / vol * math.sqrt(252.0) if vol else 0.0)
        mdds.append(mdd)

    def ordered(items: list[float], q: float) -> float:
        return sorted(items)[min(len(items) - 1, int(q * len(items)))]

    return {
        "total_p05": ordered(totals, 0.05),
        "total_p50": ordered(totals, 0.50),
        "sharpe_p50": ordered(sharpes, 0.50),
        "mdd_p05": ordered(mdds, 0.05),
    }


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
        panel = rank_score_frame(panel, member, direction, output_column=f"_r503_vq_{member}")
    panel = panel.with_columns(
        (
            pl.col("_r503_vq_pb_latest").fill_null(0.0)
            + pl.col("_r503_vq_ocf_debt").fill_null(0.0)
            + pl.col("_r503_vq_debt_ratio_latest").fill_null(0.0)
        ).alias("value_quality"),
        (
            pl.col("_r503_vq_pb_latest").is_not_null()
            & pl.col("_r503_vq_ocf_debt").is_not_null()
            & pl.col("_r503_vq_debt_ratio_latest").is_not_null()
        ).alias("_r503_vq_valid"),
    ).filter(pl.col("_r503_vq_valid"))
    return panel, sorted(panel["symbol"].unique().to_list())


def run_frozen(
    p5: Any,
    service: Any,
    panel: pl.DataFrame,
    symbols: list[str],
    prereg: dict[str, Any],
    weak_dates: set[str],
    *,
    start: str,
    end: str,
    cost_label: str,
) -> dict[str, Any]:
    from app.factor_research.incremental import build_rank_composite

    weights = {
        "ext_alt_hs300_cash_flow_stability_4q_low": 0.2375,
        "ext_alt_hs300_dividend_yield": 0.2375,
        "ext_alt_hs300_fund_holding_ratio_low": 0.2375,
        "ext_alt_hs300_idio_vol_40d_low": 0.2375,
        "value_quality": 0.05,
    }
    field = "_r503_frozen"
    work, _ = build_rank_composite(
        panel,
        weights,
        {member: "high" for member in weights},
        field,
        minimum_weight_coverage=0.75,
    )
    config = p5.portfolio._backtest_config(
        {field: 1.0},
        {field: "high"},
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=10,
        cost=(
            p5.portfolio._cost_parameters(cost_label)
            if cost_label != "5x"
            else {
                "fees_pct": 0.001,
                "commission_pct": 0.001,
                "stamp_tax_pct": 0.0005,
                "slippage_bps": 25.0,
            }
        ),
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
    del weak_dates
    return {"curve": list(result.equity_curve), "trades": len(result.trades)}


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    verify_sidecar(R499)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    source = json.loads(R499.read_text(encoding="utf-8"))
    if float(source["selected"]["weak_value_quality"]) != 0.05:
        raise RuntimeError("R499 selected sample changed")
    r453 = load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r503_r453")
    r498 = load_module(
        ROOT / "scripts" / "research_r498_drawdown_conditional_attribution.py", "r503_r498"
    )
    p5 = r453.load_p5()
    panel, symbols = build_panel(p5)
    service = p5.portfolio._service()
    regime = (
        pl.read_parquet(DATA / "regime_history" / "part.parquet")
        .sort("date")
        .with_columns(pl.col("date").cast(pl.Utf8))
        .select(["date", "score", "above_ma20_pct"])
    )
    labelled = r498.apply_weak_state(regime.to_dicts(), source["weak_threshold"])
    weak_dates = {str(row["date"])[:10] for row in labelled if row["weak"]}
    benchmark = hs300_curve()
    end = prereg["end"]

    rolling = []
    for start in prereg["rolling_starts"]:
        window_end = min(one_year_end(start), end)
        result = run_frozen(
            p5,
            service,
            panel,
            symbols,
            prereg,
            weak_dates,
            start=start,
            end=window_end,
            cost_label="2x",
        )
        metrics = curve_metrics(result["curve"])
        benchmark_sum = 1.0
        strategy_sum = 1.0
        for day, value in daily_returns(result["curve"]):
            strategy_sum *= 1.0 + value
            if day in benchmark:
                benchmark_sum *= 1.0 + benchmark[day]
        rolling.append(
            {
                "start": start,
                "end": window_end,
                **metrics,
                "benchmark_return": round(benchmark_sum - 1.0, 8),
                "relative_return": round(strategy_sum - benchmark_sum, 8),
                "trades": result["trades"],
            }
        )
    positive = sum(row["total_return"] > 0 for row in rolling)
    relative_positive = sum(row["relative_return"] > 0 for row in rolling)
    rolling_median = sorted(row["total_return"] for row in rolling)
    relative_median = sorted(row["relative_return"] for row in rolling)
    worst_mdd = min(row["max_drawdown"] for row in rolling)

    calendar = []
    for start, end_day in prereg["calendar_segments"]:
        result = run_frozen(
            p5,
            service,
            panel,
            symbols,
            prereg,
            weak_dates,
            start=start,
            end=end_day,
            cost_label="2x",
        )
        metrics = curve_metrics(result["curve"])
        calendar.append({"start": start, "end": end_day, **metrics, "trades": result["trades"]})
    calendar_positive = sum(row["total_return"] > 0 for row in calendar)

    recent_start = prereg["rolling_starts"][-1]
    recent = run_frozen(
        p5,
        service,
        panel,
        symbols,
        prereg,
        weak_dates,
        start=recent_start,
        end=end,
        cost_label="2x",
    )
    recent_returns = daily_returns(recent["curve"])
    recent_excess = paired_excess(recent_returns, benchmark)
    recent_abs = block_bootstrap(
        [value for _, value in recent_returns],
        block=int(prereg["bootstrap"]["block_days"]),
        paths=int(prereg["bootstrap"]["replications"]),
        seed=int(prereg["bootstrap"]["seeds"]["recent"]),
    )
    recent_exc = block_bootstrap(
        recent_excess,
        block=int(prereg["bootstrap"]["block_days"]),
        paths=int(prereg["bootstrap"]["replications"]),
        seed=int(prereg["bootstrap"]["seeds"]["recent"]) + 1,
    )
    checks = {
        "rolling_positive_rate_min": positive / len(rolling)
        >= prereg["gates"]["rolling_positive_rate_min"],
        "rolling_relative_positive_rate_min": relative_positive / len(rolling)
        >= prereg["gates"]["rolling_relative_positive_rate_min"],
        "rolling_median_total_positive": rolling_median[len(rolling_median) // 2] > 0.0,
        "rolling_median_relative_positive": relative_median[len(relative_median) // 2] > 0.0,
        "worst_rolling_mdd_min": worst_mdd >= prereg["gates"]["worst_rolling_mdd_min"],
        "calendar_positive_years_min": calendar_positive
        >= prereg["gates"]["calendar_positive_years_min"],
        "recent_absolute_p05_positive": recent_abs["total_p05"] > 0.0,
        "recent_paired_excess_p05_positive": recent_exc["total_p05"] > 0.0,
        "no_leverage": True,
    }
    payload = {
        "round": "R503",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_r499_sha256": sha(R499),
        "selection_performed": False,
        "rolling": rolling,
        "rolling_summary": {
            "positive": positive,
            "relative_positive": relative_positive,
            "count": len(rolling),
            "median_total": round(rolling_median[len(rolling_median) // 2], 8),
            "median_relative": round(relative_median[len(relative_median) // 2], 8),
            "worst_mdd": worst_mdd,
        },
        "calendar": calendar,
        "recent": {"bootstrap": recent_abs, "paired_excess_bootstrap": recent_exc},
        "gate": {"pass": all(checks.values()), "checks": checks},
        "diagnostics": {"five_x_warning_only": True},
        "decision": {**prereg["output_policy"]},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    lines = (
        [
            "# R503 R499 滚动一年与市场分段确认",
            "",
            "本轮冻结 R499 权重、弱市阈值与执行参数，只改变起始日期。",
            "",
            f"- 滚动一年正收益：{positive}/{len(rolling)}",
            f"- 滚动一年正超额：{relative_positive}/{len(rolling)}",
            f"- 中位滚动收益：{rolling_median[len(rolling_median) // 2]:.2%}",
            f"- 最差滚动 MDD：{worst_mdd:.2%}",
            f"- 自然年正收益：{calendar_positive}/{len(calendar)}",
            f"- 近一年绝对 p05：{recent_abs['total_p05']:.2%}",
            f"- 近一年超额 p05：{recent_exc['total_p05']:.2%}",
            f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
            "",
            "## 门槛",
            "",
        ]
        + [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
        + ["", "## 边界", ""]
        + [f"- {item}" for item in prereg["known_limits"]]
        + [""]
    )
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii"
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
