from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import shutil
import statistics as st
import sys
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
R40 = DATA / "research/r40_engine_data"
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
PREREG = EVIDENCE / "r316_defensive_overlay_preregistration.json"
OUTPUT = EVIDENCE / "r316_defensive_overlay.json"
R315_RUNNER = EVIDENCE / "run_r315_holding_bootstrap.py"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2021, 6, 1)
END = date(2026, 10, 14)
RECENT = [
    date(2025, 10, 14), date(2025, 12, 15), date(2026, 2, 13),
    date(2026, 4, 15), date(2026, 6, 15),
]
OVERLAY_COST = 0.0052
MODES = ("base", "ma200_half", "ma200_quarter", "dd10_half")

sys.path.insert(0, str(ROOT / "backend"))
from app.backtest import strategy as strategy_module
from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.factors.store import load_into_registry
from app.services.industry_map import industry_loader_for
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyEngine
from app.tickflow.repository import DataStore, KlineRepository


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(root: Path) -> dict:
    paths = sorted(root.glob("date=*/part.parquet"))
    digest = hashlib.sha256()
    size = 0
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(sha(path).encode())
        digest.update(b"\n")
        size += path.stat().st_size
    return {"files": len(paths), "bytes": size, "sha256": digest.hexdigest()}


def membership() -> pl.DataFrame:
    return (
        pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"])
        .select(pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String))
        .unique()
        .sort(["effective_date", "symbol"])
    )


@contextmanager
def patched_membership(frame: pl.DataFrame):
    original = strategy_module._hs300_membership_frame
    strategy_module._hs300_membership_frame = lambda _start, _end: frame
    try:
        yield
    finally:
        strategy_module._hs300_membership_frame = original


def curve_frame(curve: list[dict]) -> pl.DataFrame:
    rows = []
    for item in curve or []:
        raw = item.get("date")
        day = raw if isinstance(raw, date) else date.fromisoformat(str(raw)[:10])
        rows.append({"date": day, "value": float(item.get("value", 0.0))})
    if not rows:
        raise RuntimeError("empty equity curve")
    return pl.DataFrame(rows).sort("date").unique(subset=["date"], keep="last")


def index_monthly() -> tuple[dict, dict, int]:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError("missing HS300 index history")
    index = (
        pl.scan_parquet([str(p) for p in paths], extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
        .with_columns(pl.col("date").cast(pl.Date))
        .filter(pl.col("date") <= END)
        .with_columns([
            pl.col("close").rolling_mean(200).alias("_ma200"),
            pl.col("close").rolling_max(252).alias("_high252"),
        ])
        .with_columns((pl.col("close") / pl.col("_high252") - 1.0).alias("_dd252"))
    )
    index_dates = set(index["date"].to_list())
    monthly = {}
    for row in index.iter_rows(named=True):
        key = (row["date"].year, row["date"].month)
        monthly[key] = {
            "above_ma200": bool(row["_ma200"] is not None and row["close"] > row["_ma200"]),
            "dd252": float(row["_dd252"]) if row["_dd252"] is not None else 0.0,
            "date": str(row["date"]),
        }
    return monthly, {"rows": index.height, "start": str(index["date"].min()), "end": str(index["date"].max())}, len(index_dates)


def prev_month(day: date) -> tuple[int, int]:
    if day.month == 1:
        return day.year - 1, 12
    return day.year, day.month - 1


def apply_overlay(risk: pl.DataFrame, mode: str, monthly_index: dict) -> dict:
    r114 = apply_overlay.r114
    base_records = r114._vol_target(risk)["weight_records"]
    target_by_month = {(row["date"].year, row["date"].month): float(row["weight"]) for row in base_records}
    values = risk["value"].to_list()
    dates = risk["date"].to_list()
    returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    first_key = prev_month(dates[0])
    first_market = monthly_index.get(first_key, {"above_ma200": True, "dd252": 0.0})
    multiplier = 1.0
    if mode == "ma200_half" and not first_market["above_ma200"]:
        multiplier = 0.5
    elif mode == "ma200_quarter" and not first_market["above_ma200"]:
        multiplier = 0.25
    target = min(1.0, max(0.0, target_by_month[(dates[0].year, dates[0].month)] * multiplier))
    portfolio = 1.0
    sleeve = 1.0
    peak = portfolio
    rows = [{"date": dates[0], "value": portfolio}]
    weights = [{"date": dates[0], "weight": target}]
    total_turnover = 0.0
    for i in range(1, len(dates)):
        day = dates[i]
        sleeve *= 1.0 + returns[i - 1]
        portfolio *= 1.0 + target * returns[i - 1]
        peak = max(peak, portfolio)
        next_month = i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (day.year, day.month)
        if next_month:
            total = target * sleeve + (1.0 - target)
            actual = target * sleeve / total if total else target
            if i + 1 < len(dates):
                next_day = dates[i + 1]
                next_base = target_by_month[(next_day.year, next_day.month)]
            else:
                next_base = target
            current_market = monthly_index.get((day.year, day.month), {"above_ma200": True, "dd252": 0.0, "date": str(day)})
            current_dd = portfolio / peak - 1.0 if peak else 0.0
            if mode == "base":
                defensive = 1.0
            elif mode == "ma200_half":
                defensive = 1.0 if current_market["above_ma200"] else 0.5
            elif mode == "ma200_quarter":
                defensive = 1.0 if current_market["above_ma200"] else 0.25
            elif mode == "dd10_half":
                defensive = 0.5 if current_dd <= -0.10 else 1.0
            else:
                raise ValueError(mode)
            next_target = min(1.0, max(0.0, next_base * defensive))
            turnover = abs(next_target - actual)
            total_turnover += turnover
            portfolio *= 1.0 - turnover * OVERLAY_COST
            sleeve = 1.0
            target = next_target
        rows.append({"date": day, "value": portfolio})
        if next_month and i + 1 < len(dates):
            weights.append({"date": dates[i + 1], "weight": target})
    return {
        "curve": pl.DataFrame(rows),
        "weights": weights,
        "total_turnover": total_turnover,
        "annual_turnover": total_turnover * 252.0 / max(len(dates) - 1, 1),
    }

apply_overlay.r114 = None


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R316 result exists; refusing overwrite")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R316 preregistration checksum mismatch")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r316")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r316")
    r315 = module(R315_RUNNER, "r315_r316")
    apply_overlay.r114 = r114
    r114.TARGET_VOL = 0.15
    members = membership()
    monthly_index, index_meta, index_date_count = index_monthly()
    before_r40 = tree_hash(R40 / "kline_daily_enriched")
    before_prod = tree_hash(DATA / "kline_daily_enriched")
    work = Path(tempfile.mkdtemp(prefix="r316_"))
    try:
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
        raw_target = engine_root / "kline_daily"
        if raw_target.is_symlink() or raw_target.is_file():
            raw_target.unlink()
        elif raw_target.exists():
            shutil.rmtree(raw_target)
        shutil.copytree(DATA / "kline_daily", raw_target)
        load_into_registry(DATA)
        engine = StrategyEngine(
            strategy_dirs=[DATA / "strategies/ai"],
            override_loader=lambda sid: strategy_config.load_override(DATA, sid),
            industry_loader=industry_loader_for(DATA),
        )
        service = StrategyBacktestService(BacktestEngine(KlineRepository(DataStore(engine_root))), engine)

        def run(strategy_id: str, start: date, cost_factor: int) -> pl.DataFrame:
            cfg = StrategyBacktestConfig(
                strategy_id=strategy_id, symbols=None, start=start, end=END,
                params={}, overrides={}, matching="open_t+1",
                fees_pct=0.0002 * cost_factor,
                commission_pct=0.0002 * cost_factor,
                stamp_tax_pct=0.0005 if cost_factor == 2 else 0.001,
                slippage_bps=5.0 * cost_factor,
                max_positions=10, max_exposure_pct=1.0, position_sizing="equal",
                mode="position", asset_type="stock", hs300=True,
            )
            with patched_membership(members):
                result = service.run(cfg)
            if result.error:
                raise RuntimeError(f"{strategy_id} {start} {cost_factor}x: {result.error}")
            return curve_frame(result.equity_curve)

        def combined(start: date, cost_factor: int) -> pl.DataFrame:
            a = run(A_STRATEGY, start, cost_factor)
            b = run(B_STRATEGY, start, cost_factor)
            risk, _ = r112._combine(a, b, "risk")
            return risk

        full_risk = combined(START, 2)
        stress_risk = combined(START, 3)
        recent_risks = {day.isoformat(): combined(day, 2) for day in RECENT}

        full_runs = {mode: apply_overlay(full_risk, mode, monthly_index) for mode in MODES}
        stress_runs = {mode: apply_overlay(stress_risk, mode, monthly_index) for mode in MODES}
        recent_runs = {
            key: {mode: apply_overlay(risk, mode, monthly_index) for mode in MODES}
            for key, risk in recent_risks.items()
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = tree_hash(R40 / "kline_daily_enriched")
    after_prod = tree_hash(DATA / "kline_daily_enriched")
    full_dates = full_risk["date"].to_list()
    index_paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    index_frame = (
        pl.scan_parquet([str(p) for p in index_paths], extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date"]).collect().with_columns(pl.col("date").cast(pl.Date))
        .filter(pl.col("date") <= END)
    )
    index_dates = set(index_frame["date"].to_list())
    coverage_ratio = sum(day in index_dates for day in full_dates) / len(full_dates)
    summaries = {}
    for mode in MODES:
        curve = full_runs[mode]["curve"]
        returns = [curve["value"][i] / curve["value"][i - 1] - 1.0 for i in range(1, curve.height)]
        boot = r315.bootstrap(returns)
        rolling = r315.rolling_returns(returns, 252)
        recent_stats = r112._aggregate([r112._stats(recent_runs[key][mode]["curve"]) for key in sorted(recent_runs)])
        stress_stats = r112._stats(stress_runs[mode]["curve"])
        weights = [float(row["weight"]) for row in full_runs[mode]["weights"]]
        summaries[mode] = {
            "full": r112._stats(curve),
            "stress": stress_stats,
            "recent": recent_stats,
            "bootstrap": boot,
            "actual_rolling_252": {
                "windows": len(rolling),
                "positive_rate": round(sum(x > 0 for x in rolling) / len(rolling), 6),
                "return_p05": round(r315.quantile(rolling, 0.05), 6),
                "return_median": round(r315.quantile(rolling, 0.50), 6),
                "return_worst": round(min(rolling), 6),
            },
            "weights": {
                "mean": round(st.mean(weights), 6),
                "min": round(min(weights), 6),
                "max": round(max(weights), 6),
                "annual_turnover": round(float(full_runs[mode]["annual_turnover"]), 6),
            },
        }
    gates_by_mode = {}
    for mode, row in summaries.items():
        gates = {
            "G2_return": row["full"]["annual"] >= 0.05,
            "G3_drawdown": row["full"]["mdd"] >= -0.25,
            "G4_bootstrap_positive": row["bootstrap"]["positive_rate"] >= 0.80,
            "G5_bootstrap_tail": row["bootstrap"]["return_p05"] >= -0.12,
            "G6_actual_rolling": row["actual_rolling_252"]["positive_rate"] >= 0.70,
            "G7_recent": row["recent"]["total_return_mean"] > 0 and row["recent"]["positive_phases"] >= 4,
            "G8_stress": row["stress"]["total_return"] > 0 and row["stress"]["mdd"] >= -0.30,
            "G9_no_leverage": row["weights"]["min"] >= 0 and row["weights"]["max"] <= 1.0,
        }
        gates_by_mode[mode] = gates
    d1 = len(full_dates) >= 1300 and coverage_ratio >= 0.95 and all(
        math.isfinite(x)
        for mode in MODES
        for x in [summaries[mode]["full"]["total_return"], summaries[mode]["full"]["mdd"]]
    )
    g1 = all(len(recent_runs[key]) == len(MODES) for key in recent_runs) and all(len(summaries) == len(MODES) for _ in [0])
    passers = [mode for mode, gates in gates_by_mode.items() if all(gates.values())]
    passers_sorted = sorted(passers, key=lambda x: (summaries[x]["bootstrap"]["positive_rate"], summaries[x]["bootstrap"]["return_median"]), reverse=True)
    selected = passers_sorted[0] if passers_sorted else None
    payload = {
        "round": "R316",
        "classification": "preregistered defensive-overlay audit; no factor or risk-parity changes",
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__).resolve()),
        "candidate_parity": {
            "base_total_return": summaries["base"]["full"]["total_return"],
            "base_mdd": summaries["base"]["full"]["mdd"],
            "r315_candidate_total_return": json.loads((EVIDENCE / "r315_holding_bootstrap.json").read_text())["candidate_stats_2x"]["total_return"],
            "r315_candidate_mdd": json.loads((EVIDENCE / "r315_holding_bootstrap.json").read_text())["candidate_stats_2x"]["mdd"],
        },
        "data": {
            "index": index_meta,
            "index_dates": index_date_count,
            "candidate_dates": len(full_dates),
            "candidate_index_coverage": round(coverage_ratio, 6),
        },
        "variants": summaries,
        "gates": gates_by_mode,
        "D1": d1,
        "G1": g1,
        "passers": passers,
        "selected": selected,
        "input_isolation": {
            "r40_before": before_r40, "r40_after": after_r40,
            "production_before": before_prod, "production_after": after_prod,
            "stable": before_r40 == after_r40 and before_prod == after_prod,
        },
        "decision": {
            "status": "defensive_overlay_selected" if selected else "no_defensive_overlay_passed",
            "selected_mode": selected,
            "production_write": False, "provider_call": False, "parameter_search": False,
        },
        "limits": [
            "Standalone MA200/index timing previously failed in R56; this is a defensive overlay test, not a new alpha claim.",
            "All variants use overlapping historical data and are not independent confirmation.",
            "R40 local snapshot and canonical data drift remain unresolved.",
            "No production or paper execution is authorized by this round.",
        ],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "base": {k: summaries["base"]["full"][k] for k in ("total_return", "annual", "sharpe", "mdd")},
        "bootstrap": {m: {k: summaries[m]["bootstrap"][k] for k in ("positive_rate", "return_p05", "return_median", "mdd_p05")} for m in MODES},
        "rolling_positive": {m: summaries[m]["actual_rolling_252"]["positive_rate"] for m in MODES},
        "selected": selected,
        "passers": passers,
        "gates": gates_by_mode,
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
