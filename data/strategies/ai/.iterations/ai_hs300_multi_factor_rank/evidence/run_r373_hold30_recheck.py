"""R373: recheck the R371 hold-30 candidates with the frozen portfolio pipeline."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import statistics as st
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
RESEARCH = DATA / "research"
PREREG = EVIDENCE / "r373_preregistration.json"
R371 = EVIDENCE / "r371_alpha_parameter_grid.json"
R371_CACHE = RESEARCH / "r371_cache"
R360 = EVIDENCE / "r360_industry_fade_backtest.json"
OUTPUT = EVIDENCE / "r373_hold30_recheck.json"
RESULT = EVIDENCE / "r373_result.md"
A_CURVE = RESEARCH / "r344_cache/ai_hs300_macro_gate_quality_2022-05-05_2x_p10.parquet"
C_SESSION = RESEARCH / "r344_cache/ai_hs300_quality_momentum_broad_h60_2022-05-05_2x_p50.parquet"
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


def _stats(curve: pl.DataFrame) -> dict[str, float | int]:
    values = [float(value) for value in curve["value"].to_list()]
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


def _year_returns(curve: pl.DataFrame) -> dict[str, float]:
    by_year: dict[int, list[float]] = {}
    for row in curve.iter_rows(named=True):
        by_year.setdefault(row["date"].year, []).append(float(row["value"]))
    output: dict[str, float] = {}
    previous_end: float | None = None
    for year in sorted(by_year):
        values = by_year[year]
        start = previous_end if previous_end is not None else values[0]
        output[str(year)] = values[-1] / start - 1.0
        previous_end = values[-1]
    return output


def _segment_return(curve: pl.DataFrame, start: date, end: date) -> float | None:
    part = curve.filter(pl.col("date").is_between(start, end)).sort("date")
    if part.height < 2:
        return None
    return float(part["value"][-1] / part["value"][0] - 1.0)


def _cell_slug(fade: int, hold: int, stop: float) -> str:
    return f"fade{fade:02d}_h{hold:02d}_sl{round(abs(stop) * 100):02d}"


def _curve(path: Path) -> pl.DataFrame:
    return pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")


def _context() -> dict[str, Any]:
    return {
        "r331": module(EVIDENCE / "run_r331_three_asset_bucket.py", "r373_r331"),
        "r344": module(EVIDENCE / "run_r344_extended_window.py", "r373_r344"),
        "r359": module(EVIDENCE / "run_r359_cohort_ensemble.py", "r373_r359"),
        "r112": module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r373_r112"),
        "r114": module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r373_r114"),
    }


def _final_curve(a: pl.DataFrame, b: pl.DataFrame, c: pl.DataFrame, ctx: dict[str, Any]) -> pl.DataFrame:
    r331 = ctx["r331"]
    r344 = ctx["r344"]
    r112 = ctx["r112"]
    r114 = ctx["r114"]
    old_114 = (r114.TARGET_VOL, r114.ROUND_TRIP_COST)
    old_331 = (r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX)
    try:
        r114.TARGET_VOL = 0.15
        r114.ROUND_TRIP_COST = 0.0052
        r331.LOOKBACK = 60
        r331.TARGET_VOL = 0.15
        r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
        risk, _ = r344._combine_three((a, b, c), r112.ROUND_TRIP_COST, 0.10)
        equity = r114._vol_target(risk)["curve"]
        gold = r331.asset_frame("gold", "518880.SH")
        treasury = r331.asset_frame("treasury", "511260.SH")
        dividend = r331.asset_frame("dividend", "510880.SH")
        gold_proxy = r344.gold_trend_proxy(gold, treasury, dividend, 0.001)
        return r331.build_three_asset_bucket(
            equity,
            gold_proxy,
            treasury,
            dividend,
            round_trip_cost=0.001,
        )["curve"]
    finally:
        r114.TARGET_VOL, r114.ROUND_TRIP_COST = old_114
        r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX = old_331

def _evaluate(
    row: dict[str, Any],
    curve: pl.DataFrame,
    *,
    cost: int,
    start: date,
    recent_start: date,
    end: date,
    threshold: dict[str, Any],
) -> dict[str, Any]:
    stats = _stats(curve)
    yearly = _year_returns(curve)
    recent = _segment_return(curve, recent_start, end)
    checks = {
        "annual": float(stats["annual"]) >= float(threshold["annual_min"]),
        "sharpe": float(stats["sharpe"]) >= float(threshold["sharpe_min"]),
        "mdd": float(stats["mdd"]) >= float(threshold["mdd_min"]),
        "year_2022": (yearly.get("2022", -1.0) >= float(threshold["year_2022_min_inclusive"])) if cost == 2 else True,
        "year_2023": (yearly.get("2023", -1.0) >= float(threshold["year_2023_min_inclusive"])) if cost == 2 else True,
        "recent": (recent is not None and recent > float(threshold["recent_return_min_exclusive"])) if cost == 2 else True,
    }
    return {
        "cell": _cell_slug(row["fade_days"], row["hold_days"], row["stop_loss"]),
        "cost": cost,
        "stats": stats,
        "yearly": yearly,
        "recent": recent,
        "checks": checks,
        "pass": all(checks.values()),
    }


def build_report() -> dict[str, Any]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r371 = json.loads(R371.read_text(encoding="utf-8"))
    frozen = json.loads(R360.read_text(encoding="utf-8"))["performance"]["candidate"]["2x"]
    ctx = _context()
    r359 = ctx["r359"]
    a = _curve(A_CURVE)
    sessions = _curve(C_SESSION)["date"].to_list()
    cohort_starts = r359.cohort_start_dates(sessions, date(2022, 5, 5))
    c_curves = [
        _curve(RESEARCH / f"r359_v2_cache/ai_hs300_quality_momentum_broad_h60_{day}_2x_p50.parquet")
        for day in cohort_starts
    ]
    c = r359.combine_cohort_curves(c_curves, cohort_starts)
    selected = []
    for fade, hold, stop in prereg["candidate_focus"]:
        row = next(
            item for item in r371["rows"]
            if (item["fade_days"], item["hold_days"], item["stop_loss"]) == (fade, hold, stop)
        )
        b = _curve(R371_CACHE / f"{_cell_slug(fade, hold, stop)}.parquet")
        final = _final_curve(a, b, c, ctx)
        selected.append(
            _evaluate(
                row,
                final,
                cost=2,
                start=date.fromisoformat(prereg["portfolio"]["start"]),
                recent_start=date.fromisoformat(prereg["portfolio"]["recent_start"]),
                end=date.fromisoformat(prereg["portfolio"]["end"]),
                threshold=prereg["evaluation"]["core_2x"],
            )
        )
    control = next(
        item for item in r371["rows"]
        if (item["fade_days"], item["hold_days"], item["stop_loss"]) == tuple(prereg["control"])
    )
    control_b = _curve(R371_CACHE / f"{_cell_slug(*prereg['control'])}.parquet")
    control_curve = _final_curve(a, control_b, c, ctx)
    control_stats = _stats(control_curve)
    control_match = all(
        math.isclose(float(control_stats[key]), float(frozen[key]), rel_tol=0.0, abs_tol=1e-9)
        for key in ("annual", "sharpe", "mdd", "total_return")
    ) and int(control_stats["days"]) == int(frozen["days"])
    if not control_match:
        raise RuntimeError("R373 control does not reconstruct frozen R360")
    return {
        "round": "R373",
        "preregistration_sha256": sha256(PREREG),
        "source_r371_sha256": sha256(R371),
        "control_reconstructs_r360": control_match,
        "control_stats": control_stats,
        "rows": selected,
        "cost_scope": "2x only; 3x requires fresh B-sleeve curves and is not inferred from 2x",
        "concentration_evidence": {
            "available": False,
            "reason": "R371 stores B-sleeve equity curves but not trade-level concentration records.",
        },
        "decision": {
            "selected": None,
            "no_best_cell_selected": True,
            "no_leverage": True,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"] + [prereg["portfolio"]["construction"]],
    }

def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# R373 · R371 hold-30 区域真实净值复核",
        "",
        f"中心对照重建 R360：{report['control_reconstructs_r360']}。",
        "",
        "| 格 | 成本 | 年化 | Sharpe | MDD | 2022 | 2023 | 近一年 | 通过 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['cell']} | {row['cost']}x | {row['stats']['annual']:.2%} | {row['stats']['sharpe']:.3f} | "
            f"{row['stats']['mdd']:.2%} | {row['yearly'].get('2022', float('nan')):.2%} | "
            f"{row['yearly'].get('2023', float('nan')):.2%} | {row['recent']:.2%} | {row['pass']} |"
        )
    lines.extend([
        "",
        "## 判定边界",
        "",
        "- 候选来自 R371 已看过的网格，不是独立发现。",
        "- 本次只做净值复核；R371 缓存没有交易明细，集中度未验证。",
        "- 不选最高年化格，不生成纸面目标、订单或生产配置。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    for path in (OUTPUT, OUTPUT.with_name(OUTPUT.name + ".sha256"), RESULT, RESULT.with_name(RESULT.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"R373 output exists: {path}")
    report = build_report()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha256(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    RESULT.write_text(markdown(report), encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha256(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(json.dumps(report["decision"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
