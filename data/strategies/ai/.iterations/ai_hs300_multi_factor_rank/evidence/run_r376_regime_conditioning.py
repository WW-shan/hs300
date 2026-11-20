"""R376: regime-conditioned exposure scaling on the frozen hold-30 platform centre."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import statistics as st
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
RESEARCH = DATA / "research"
PREREG = EVIDENCE / "r376_preregistration.json"
R374 = EVIDENCE / "r374_platform_validation.json"
OUTPUT = EVIDENCE / "r376_regime_conditioning.json"
RESULT = EVIDENCE / "r376_result.md"
REGIME = DATA / "regime_history/part.parquet"
CELL = "fade05_h30_sl20"
START = date(2022, 5, 5)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
ROLLING_STARTS = (date(2022, 5, 5), date(2023, 5, 5), date(2024, 5, 5), date(2025, 5, 5))
ROLLING_LENGTH = 252
ROUND_TRIP_COST = 0.0052
DEFENSIVE_COST = 0.001
MIN_WEIGHT = 0.2
MAX_WEIGHT = 1.0
BOOTSTRAP_BLOCK = 20
BOOTSTRAP_PATHS = 2000
BOOTSTRAP_HORIZON = 252
BOOTSTRAP_SEED = 376
sys.path.insert(0, str(ROOT / "backend"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def _curve(path: Path) -> pl.DataFrame:
    frame = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
    return frame


def stats(values: list[float]) -> dict[str, float | int]:
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    volatility = st.pstdev(returns)
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {
        "total_return": total,
        "annual": (1.0 + total) ** (252.0 / len(returns)) - 1.0,
        "sharpe": st.mean(returns) / volatility * math.sqrt(252.0) if volatility else 0.0,
        "mdd": mdd,
        "days": len(values),
    }


def year_returns(frame: pl.DataFrame) -> dict[str, float]:
    grouped: dict[int, list[float]] = {}
    for row in frame.iter_rows(named=True):
        grouped.setdefault(row["date"].year, []).append(float(row["value"]))
    output: dict[str, float] = {}
    previous: float | None = None
    for year in sorted(grouped):
        values = grouped[year]
        start_value = previous if previous is not None else values[0]
        output[str(year)] = values[-1] / start_value - 1.0
        previous = values[-1]
    return output


def rolling_positive(frame: pl.DataFrame) -> dict[str, Any]:
    windows = []
    for start in ROLLING_STARTS:
        part = frame.filter(pl.col("date") >= start).sort("date").head(ROLLING_LENGTH + 1)
        if part.height != ROLLING_LENGTH + 1:
            raise ValueError(f"rolling window from {start} is incomplete")
        values = part["value"].to_list()
        windows.append({"start": str(start), "return": float(values[-1] / values[0] - 1.0)})
    return {
        "window_count": len(windows),
        "positive_count": sum(window["return"] > 0 for window in windows),
        "windows": windows,
    }


def recent_return(frame: pl.DataFrame) -> float:
    part = frame.filter(pl.col("date") >= RECENT_START).sort("date")
    return float(part["value"][-1] / part["value"][0] - 1.0)


def regime_frame() -> pl.DataFrame:
    frame = pl.read_parquet(REGIME).with_columns(pl.col("date").cast(pl.Date)).sort("date")
    return frame.with_columns([
        pl.col("score").shift(1).alias("score_lag1"),
        (pl.col("score") - pl.col("score").shift(5)).shift(1).alias("score_chg5_lag1"),
        pl.col("above_ma20_pct").shift(1).alias("breadth_lag1"),
        pl.col("trend_score").shift(1).alias("trend_lag1"),
        pl.col("profit_score").shift(1).alias("profit_lag1"),
        pl.col("speculation_score").shift(1).alias("spec_lag1"),
        pl.col("index_pct").rolling_mean(20).shift(1).alias("index_mom20_lag1"),
    ]).select([
        "date",
        "score_lag1",
        "score_chg5_lag1",
        "breadth_lag1",
        "trend_lag1",
        "profit_lag1",
        "spec_lag1",
        "index_mom20_lag1",
    ])


ATOM_FIELDS = {
    "score_level": "score_lag1",
    "score_change_5d": "score_chg5_lag1",
    "breadth_above_ma20": "breadth_lag1",
    "trend_score": "trend_lag1",
    "profit_score": "profit_lag1",
    "speculation_score": "spec_lag1",
    "index_momentum_20d": "index_mom20_lag1",
}


def apply_scale(
    equity: pl.DataFrame,
    regime: pl.DataFrame,
    field: str,
    threshold: float,
    low: float,
) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    """Scale the equity sleeve monthly; the defensive bucket is never scaled down."""
    joined = (
        equity.rename({"value": "equity"})
        .join(regime, on="date", how="left")
        .sort("date")
    )
    dates = joined["date"].to_list()
    equity_values = joined["equity"].to_list()
    signals: list[float | None] = joined[field].to_list()
    rows = [{"date": dates[0], "value": 1.0}]
    weight_records = []
    weight = 1.0
    sleeve = 1.0
    for index in range(1, len(dates)):
        ret = equity_values[index] / equity_values[index - 1] - 1.0
        sleeve *= 1.0 + ret
        rows.append({"date": dates[index], "value": rows[-1]["value"] * (1.0 + weight * ret)})
        last_of_month = index + 1 == len(dates) or dates[index + 1].month != dates[index].month
        if last_of_month and index + 1 < len(dates):
            signal = signals[index]
            if signal is not None and not (isinstance(signal, float) and math.isnan(signal)):
                total = weight * sleeve + (1.0 - weight)
                actual = weight * sleeve / total if total else weight
                target = MAX_WEIGHT if float(signal) >= threshold else low
                target = min(MAX_WEIGHT, max(MIN_WEIGHT, target))
                turnover = abs(target - actual)
                rows[-1]["value"] *= 1.0 - turnover * ROUND_TRIP_COST
                weight_records.append({"date": str(dates[index + 1]), "weight": target})
                sleeve = 1.0
                weight = target
    return pl.DataFrame(rows), weight_records


def paired_bootstrap(diff: list[float]) -> dict[str, Any]:
    import random

    rng = random.Random(BOOTSTRAP_SEED)
    n = len(diff)
    blocks = math.ceil(BOOTSTRAP_HORIZON / BOOTSTRAP_BLOCK)
    annualised = []
    for _ in range(BOOTSTRAP_PATHS):
        sample: list[float] = []
        for _ in range(blocks):
            start = rng.randrange(n)
            for offset in range(BOOTSTRAP_BLOCK):
                sample.append(diff[(start + offset) % n])
        sample = sample[:BOOTSTRAP_HORIZON]
        growth = 1.0
        for value in sample:
            growth *= 1.0 + value
        annualised.append(growth - 1.0)
    annualised.sort()
    return {
        "paths": BOOTSTRAP_PATHS,
        "block_days": BOOTSTRAP_BLOCK,
        "horizon_days": BOOTSTRAP_HORIZON,
        "probability_positive": sum(value > 0 for value in annualised) / len(annualised),
        "p05": annualised[int(0.05 * len(annualised))],
        "p50": annualised[int(0.50 * len(annualised))],
        "p95": annualised[int(0.95 * len(annualised))],
    }


def evaluate(
    equity: pl.DataFrame,
    regime: pl.DataFrame,
    prereg: dict[str, Any],
    composition,
    base_curve: pl.DataFrame,
) -> list[dict[str, Any]]:
    gates = prereg["gates"]
    base_stats = stats(base_curve["value"].to_list())
    base_dates = base_curve["date"].to_list()
    base_values = base_curve["value"].to_list()
    base_returns = [
        base_values[index] / base_values[index - 1] - 1.0 for index in range(1, len(base_values))
    ]
    results = []
    for atom, field in ATOM_FIELDS.items():
        for threshold in prereg["atoms"][atom]["thresholds"]:
            for low in prereg["scale_levels"]:
                if low >= 1.0:
                    continue
                scaled, weights = apply_scale(equity, regime, field, float(threshold), float(low))
                final = composition(scaled)
                values = final["value"].to_list()
                if final["date"].to_list() != base_dates:
                    raise RuntimeError("conditioned curve lost calendar alignment")
                year = year_returns(final)
                rolling = rolling_positive(final)
                recent = recent_return(final)
                metrics = stats(values)
                conditioned_returns = [
                    values[index] / values[index - 1] - 1.0 for index in range(1, len(values))
                ]
                diff = [
                    a - b for a, b in zip(conditioned_returns, base_returns, strict=True)
                ]
                checks_core = {
                    "annual": metrics["annual"] >= gates["core_2x"]["annual_min"],
                    "sharpe": metrics["sharpe"] >= gates["core_2x"]["sharpe_min"],
                    "mdd": metrics["mdd"] >= gates["core_2x"]["mdd_min"],
                    "year_2022": year.get("2022", -1.0) >= 0.0,
                    "year_2023": year.get("2023", -1.0) >= 0.0,
                    "recent": recent > 0.0,
                }
                checks_rolling = {
                    "positive_years": rolling["positive_count"] >= gates["rolling"]["positive_year_count_min"],
                }
                inc = gates["increment_over_frozen_centre"]
                checks_increment = {
                    "does_not_give_up_annual": metrics["annual"] >= base_stats["annual"] - inc["annual_tolerance"],
                    "better_risk": (metrics["sharpe"] >= base_stats["sharpe"] + 0.03)
                    or (metrics["mdd"] >= base_stats["mdd"] + 0.01),
                }
                active_share = len([w for w in weights if w["weight"] < 0.999]) / max(len(weights), 1)
                bootstrap = paired_bootstrap(diff)
                checks_bootstrap = {
                    "probability": bootstrap["probability_positive"] >= 0.60,
                    "p05": bootstrap["p05"] >= -0.01,
                }
                results.append({
                    "atom": atom,
                    "field": field,
                    "threshold": float(threshold),
                    "low_scale": float(low),
                    "metrics": metrics,
                    "yearly": year,
                    "rolling": rolling,
                    "recent": recent,
                    "checks_core": checks_core,
                    "checks_rolling": checks_rolling,
                    "checks_increment": checks_increment,
                    "checks_bootstrap": checks_bootstrap,
                    "mean_exposure": st.mean([w["weight"] for w in weights]) if weights else 1.0,
                    "scale_down_share": active_share,
                    "bootstrap": bootstrap,
                    "core_pass": all(checks_core.values()),
                    "rolling_pass": all(checks_rolling.values()),
                    "increment_pass": all(checks_increment.values()),
                    "bootstrap_pass": all(checks_bootstrap.values()),
                })
    thresholds_by_atom: dict[str, list[float]] = {}
    for atom in ATOM_FIELDS:
        thresholds_by_atom[atom] = sorted({row["threshold"] for row in results if row["atom"] == atom})
    for row in results:
        levels = thresholds_by_atom[row["atom"]]
        index = levels.index(row["threshold"])
        neighbours = [levels[i] for i in (index - 1, index + 1) if 0 <= i < len(levels)]
        sibling_pass = [
            any(
                other["atom"] == row["atom"]
                and other["threshold"] == neighbour
                and other["low_scale"] == row["low_scale"]
                and other["core_pass"]
                and other["rolling_pass"]
                and other["increment_pass"]
                and other["bootstrap_pass"]
                for other in results
            )
            for neighbour in neighbours
        ]
        row["plateau_pass"] = bool(
            row["core_pass"] and row["rolling_pass"] and row["increment_pass"] and row["bootstrap_pass"]
            and sum(sibling_pass) >= 1
        )
        row["joint_pass"] = bool(
            row["core_pass"] and row["rolling_pass"] and row["increment_pass"]
            and row["bootstrap_pass"] and row["plateau_pass"]
        )
    return results


def base_equity(ctx: dict[str, Any]) -> pl.DataFrame:
    r331 = ctx["r331"]
    r344 = ctx["r344"]
    r359 = ctx["r359"]
    r112 = ctx["r112"]
    r114 = ctx["r114"]
    a = _curve(RESEARCH / "r344_cache/ai_hs300_macro_gate_quality_2022-05-05_2x_p10.parquet")
    starts = (date(2022, 5, 5), date(2022, 6, 2), date(2022, 7, 1))
    c = r359.combine_cohort_curves(
        [
            _curve(RESEARCH / f"r359_v2_cache/ai_hs300_quality_momentum_broad_h60_{start}_2x_p50.parquet")
            for start in starts
        ],
        list(starts),
    )
    b = _curve(RESEARCH / f"r371_cache/{CELL}.parquet")
    old_112 = r112.ROUND_TRIP_COST
    old_114 = (r114.TARGET_VOL, r114.ROUND_TRIP_COST)
    old_331 = (r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX)
    try:
        r112.ROUND_TRIP_COST = ROUND_TRIP_COST
        r114.TARGET_VOL = 0.15
        r114.ROUND_TRIP_COST = ROUND_TRIP_COST
        r331.LOOKBACK = 60
        r331.TARGET_VOL = 0.15
        r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
        risk, _ = r344._combine_three((a, b, c), ROUND_TRIP_COST, 0.10)
        equity = r114._vol_target(risk)["curve"]
        # Cache the exact R374 gold proxy before the defensive bucket is built.
        (RESEARCH / "r376_cache").mkdir(parents=True, exist_ok=True)
        gold_proxy = r344.gold_trend_proxy(
            r331.asset_frame("gold", "518880.SH"),
            r331.asset_frame("treasury", "511260.SH"),
            r331.asset_frame("dividend", "510880.SH"),
            DEFENSIVE_COST,
        )
        equity.write_parquet(RESEARCH / "r376_cache/equity_centre_2x.parquet", compression="zstd")
        gold_proxy.write_parquet(RESEARCH / "r376_cache/gold_proxy_2x.parquet", compression="zstd")
        return equity
    finally:
        r112.ROUND_TRIP_COST = old_112
        r114.TARGET_VOL, r114.ROUND_TRIP_COST = old_114
        r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX = old_331


def composition(ctx: dict[str, Any]):
    r331 = ctx["r331"]
    # Use the exact defensive proxy cached by base_equity. Gold's 200-day trend
    # uses a full-history window, so rebuilding it per variant would be both
    # slow and, more importantly, risks a different proxy vintage.
    gold_proxy = pl.read_parquet(RESEARCH / "r376_cache/gold_proxy_2x.parquet").with_columns(
        pl.col("date").cast(pl.Date)
    ).sort("date")
    treasury = r331.asset_frame("treasury", "511260.SH")
    dividend = r331.asset_frame("dividend", "510880.SH")

    def build(equity: pl.DataFrame) -> pl.DataFrame:
        # The defensive bucket rebalances on the one-third equity-weight cap and
        # the 15% target. Those globals must be set for every variant, because
        # base_equity restores them before this closure runs.
        old = (r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX)
        try:
            r331.LOOKBACK = 60
            r331.TARGET_VOL = 0.15
            r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
            return r331.build_three_asset_bucket(
                equity,
                gold_proxy,
                treasury,
                dividend,
                round_trip_cost=DEFENSIVE_COST,
            )["curve"]
        finally:
            r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX = old

    return build


def select_best(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    passing = [row for row in rows if row["joint_pass"]]
    if not passing:
        return None
    passing.sort(key=lambda row: (-row["metrics"]["sharpe"], row["mean_exposure"], row["atom"], row["threshold"]))
    return passing[0]


def build_report() -> dict[str, Any]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    frozen = json.loads(R374.read_text(encoding="utf-8"))
    expected = next(row for row in frozen["rows"] if row["cell"] == CELL)
    ctx = {
        "r331": module(EVIDENCE / "run_r331_three_asset_bucket.py", "r376_r331"),
        "r344": module(EVIDENCE / "run_r344_extended_window.py", "r376_r344"),
        "r359": module(EVIDENCE / "run_r359_cohort_ensemble.py", "r376_r359"),
        "r112": module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r376_r112"),
        "r114": module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r376_r114"),
    }
    equity = base_equity(ctx)
    build = composition(ctx)
    base_curve = build(equity)
    base_stats = stats(base_curve["value"].to_list())
    if not math.isclose(base_stats["annual"], expected["annual_2x"], abs_tol=1e-9):
        raise RuntimeError("R376 baseline does not reconstruct the R374 centre")
    if not math.isclose(base_stats["sharpe"], expected["sharpe_2x"], abs_tol=1e-9):
        raise RuntimeError("R376 baseline Sharpe does not reconstruct the R374 centre")
    regime = regime_frame()
    rows = evaluate(equity, regime, prereg, build, base_curve)
    best = select_best(rows)
    return {
        "round": "R376",
        "classification": prereg["classification"],
        "preregistration_sha256": sha256(PREREG),
        "cell": CELL,
        "base": {"stats": base_stats, "yearly": year_returns(base_curve), "rolling": rolling_positive(base_curve), "recent": recent_return(base_curve)},
        "variant_count": len(rows),
        "joint_pass_count": sum(row["joint_pass"] for row in rows),
        "core_pass_count": sum(row["core_pass"] for row in rows),
        "increment_pass_count": sum(row["increment_pass"] for row in rows),
        "selected": None if best is None else {
            "atom": best["atom"],
            "threshold": best["threshold"],
            "low_scale": best["low_scale"],
            "stats": best["metrics"],
            "yearly": best["yearly"],
            "recent": best["recent"],
            "rolling": best["rolling"],
            "mean_exposure": best["mean_exposure"],
            "bootstrap": best["bootstrap"],
        },
        "rows": rows,
        "decision": {
            "conditioning_accepted": best is not None,
            "regime_conditioning_is_negative_knowledge": best is None,
            "no_leverage": True,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }


def markdown(report: dict[str, Any]) -> str:
    base = report["base"]["stats"]
    lines = [
        "# R376 · 市场状态条件化真实回测",
        "",
        f"基准：R374 平台中心 `{CELL}`，2× 年化 {base['annual']:.2%}、Sharpe {base['sharpe']:.3f}、MDD {base['mdd']:.2%}。",
        "",
        f"测试 {report['variant_count']} 个条件化变体；核心门槛通过 {report['core_pass_count']} 个，"
        f"增量门槛通过 {report['increment_pass_count']} 个，全部门槛（含邻域平台和配对 bootstrap）通过 {report['joint_pass_count']} 个。",
        "",
    ]
    selected = report["selected"]
    if selected is None:
        lines.append("**结论：没有条件化变体通过全部门槛，状态条件化记为负知识，基准保持不变。**")
    else:
        s = selected["stats"]
        lines.extend([
            f"**选中**：`{selected['atom']}` 阈值 {selected['threshold']}，风险关闭时仓位 {selected['low_scale']:.0%}。",
            "",
            f"- 年化 {s['annual']:.2%}，Sharpe {s['sharpe']:.3f}，MDD {s['mdd']:.2%}，近一年 {selected['recent']:.2%}。",
            f"- 平均敞口 {selected['mean_exposure']:.1%}，配对 bootstrap 正概率 {selected['bootstrap']['probability_positive']:.1%}。",
            "",
        ])
    top = sorted(report["rows"], key=lambda row: -row["metrics"]["sharpe"])[:8]
    lines.extend([
        "| 原子 | 阈值 | 低仓位 | 年化 | Sharpe | MDD | 增量 | 平台 | 全过 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: |",
    ])
    for row in top:
        lines.append(
            f"| {row['atom']} | {row['threshold']:.2f} | {row['low_scale']:.0%} | "
            f"{row['metrics']['annual']:.2%} | {row['metrics']['sharpe']:.3f} | {row['metrics']['mdd']:.2%} | "
            f"{row['increment_pass']} | {row['plateau_pass']} | {row['joint_pass']} |"
        )
    lines.extend([
        "",
        "- 提高 Sharpe 但年化损失超过 0.5pp 的变体不算通过；只降仓不改善风险收益比的变体不算通过。",
        "- 条件化原子和阈值已在一次探索性草稿中出现，本轮是固定假设的确认检验，不是盲测发现。",
        "- 无杠杆、不生成纸面目标、订单或生产配置。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    for path in (OUTPUT, OUTPUT.with_name(OUTPUT.name + ".sha256"), RESULT, RESULT.with_name(RESULT.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"R376 output exists: {path}")
    report = build_report()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha256(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    RESULT.write_text(markdown(report), encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha256(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(json.dumps(report["decision"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
