from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
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
PREREG = EVIDENCE / "r315_holding_bootstrap_preregistration.json"
R312_RESULT = EVIDENCE / "r312_dual_sleeve_vol15.json"
OUTPUT = EVIDENCE / "r315_holding_bootstrap.json"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2021, 6, 1)
END = date(2026, 10, 14)
RECENT_START = date(2025, 10, 14)
PATH_DAYS = 252
BLOCK_DAYS = 20
N_PATHS = 1000
SEED = 20261105

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


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("empty quantile input")
    pos = (len(ordered) - 1) * q
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return ordered[low]
    frac = pos - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac


def path_metrics(returns: list[float]) -> dict:
    values = [1.0]
    for ret in returns:
        values.append(values[-1] * (1.0 + ret))
    total = values[-1] - 1.0
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    sd = st.pstdev(returns)
    sharpe = st.mean(returns) / sd * math.sqrt(252.0) if sd > 0 else 0.0
    return {"total_return": total, "mdd": mdd, "sharpe": sharpe}


def bootstrap(returns: list[float]) -> dict:
    rng = random.Random(SEED)
    n = len(returns)
    blocks_needed = math.ceil(PATH_DAYS / BLOCK_DAYS)
    path_rows = []
    for _ in range(N_PATHS):
        sampled: list[float] = []
        while len(sampled) < PATH_DAYS:
            start = rng.randrange(n)
            for offset in range(BLOCK_DAYS):
                sampled.append(returns[(start + offset) % n])
        metrics = path_metrics(sampled[:PATH_DAYS])
        path_rows.append(metrics)
    totals = [row["total_return"] for row in path_rows]
    mdds = [row["mdd"] for row in path_rows]
    sharpes = [row["sharpe"] for row in path_rows]
    return {
        "paths": N_PATHS,
        "blocks_needed": blocks_needed,
        "positive_rate": round(sum(x > 0 for x in totals) / N_PATHS, 6),
        "return_p05": round(quantile(totals, 0.05), 6),
        "return_p25": round(quantile(totals, 0.25), 6),
        "return_median": round(quantile(totals, 0.50), 6),
        "return_p75": round(quantile(totals, 0.75), 6),
        "return_p95": round(quantile(totals, 0.95), 6),
        "return_worst": round(min(totals), 6),
        "return_best": round(max(totals), 6),
        "mdd_p05": round(quantile(mdds, 0.05), 6),
        "mdd_median": round(quantile(mdds, 0.50), 6),
        "mdd_worst": round(min(mdds), 6),
        "sharpe_p05": round(quantile(sharpes, 0.05), 4),
        "sharpe_median": round(quantile(sharpes, 0.50), 4),
    }


def rolling_returns(returns: list[float], window: int) -> list[float]:
    out = []
    for start in range(0, len(returns) - window + 1):
        product = 1.0
        for ret in returns[start:start + window]:
            product *= 1.0 + ret
        out.append(product - 1.0)
    return out


def year_returns(curve: pl.DataFrame) -> dict:
    rows = curve.iter_rows(named=True)
    values_by_year: dict[int, list[float]] = {}
    for row in rows:
        values_by_year.setdefault(row["date"].year, []).append(float(row["value"]))
    years = sorted(values_by_year)
    out = {}
    previous_end = None
    for year in years:
        values = values_by_year[year]
        start_value = previous_end if previous_end is not None else values[0]
        out[str(year)] = round(values[-1] / start_value - 1.0, 6)
        previous_end = values[-1]
    return out


def month_returns(curve: pl.DataFrame) -> list[dict]:
    out = []
    current_key = None
    start_value = None
    last_value = None
    for row in curve.iter_rows(named=True):
        key = (row["date"].year, row["date"].month)
        value = float(row["value"])
        if current_key is None:
            current_key = key
            start_value = value
        elif key != current_key:
            out.append({"month": f"{current_key[0]:04d}-{current_key[1]:02d}", "return": round(last_value / start_value - 1.0, 6)})
            current_key = key
            start_value = last_value
        last_value = value
    if current_key is not None:
        out.append({"month": f"{current_key[0]:04d}-{current_key[1]:02d}", "return": round(last_value / start_value - 1.0, 6)})
    return out


def drawdown_duration(curve: pl.DataFrame) -> dict:
    dates = curve["date"].to_list()
    values = curve["value"].to_list()
    peak_value = values[0]
    peak_date = dates[0]
    trough_date = dates[0]
    trough_value = values[0]
    in_drawdown = False
    max_peak_to_trough = 0
    max_underwater = 0
    current_underwater_start = None
    max_record = None
    for day, value in zip(dates, values):
        if value >= peak_value:
            if in_drawdown and current_underwater_start is not None:
                duration = (day - current_underwater_start).days
                if duration > max_underwater:
                    max_underwater = duration
                    max_record = {
                        "start": str(current_underwater_start), "recovery": str(day),
                        "calendar_days": duration,
                    }
            peak_value = value
            peak_date = day
            in_drawdown = False
            current_underwater_start = None
        else:
            if not in_drawdown:
                in_drawdown = True
                current_underwater_start = peak_date
            if value < trough_value:
                trough_value = value
                trough_date = day
                max_peak_to_trough = (trough_date - peak_date).days
    if in_drawdown and current_underwater_start is not None:
        duration = (dates[-1] - current_underwater_start).days
        if duration > max_underwater:
            max_underwater = duration
            max_record = {"start": str(current_underwater_start), "end": str(dates[-1]), "calendar_days": duration, "recovered": False}
    return {
        "max_peak_to_trough_calendar_days": max_peak_to_trough,
        "max_underwater_calendar_days": max_underwater,
        "longest_underwater": max_record,
    }


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R315 result exists; refusing overwrite")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R315 preregistration checksum mismatch")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r315")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r315")
    r114.TARGET_VOL = 0.15
    members = membership()
    before_r40 = tree_hash(R40 / "kline_daily_enriched")
    before_prod = tree_hash(DATA / "kline_daily_enriched")
    work = Path(tempfile.mkdtemp(prefix="r315_"))
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

        def run(strategy_id: str) -> pl.DataFrame:
            cfg = StrategyBacktestConfig(
                strategy_id=strategy_id, symbols=None, start=START, end=END,
                params={}, overrides={}, matching="open_t+1",
                fees_pct=0.0004, commission_pct=0.0004, stamp_tax_pct=0.0005,
                slippage_bps=10.0, max_positions=10, max_exposure_pct=1.0,
                position_sizing="equal", mode="position", asset_type="stock", hs300=True,
            )
            with patched_membership(members):
                result = service.run(cfg)
            if result.error:
                raise RuntimeError(f"{strategy_id}: {result.error}")
            return curve_frame(result.equity_curve)

        a_curve = run(A_STRATEGY)
        b_curve = run(B_STRATEGY)
        risk, _ = r112._combine(a_curve, b_curve, "risk")
        candidate = r114._vol_target(risk)["curve"]
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = tree_hash(R40 / "kline_daily_enriched")
    after_prod = tree_hash(DATA / "kline_daily_enriched")
    values = candidate["value"].to_list()
    dates = candidate["date"].to_list()
    returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    if not all(math.isfinite(x) for x in returns):
        raise RuntimeError("non-finite candidate return")
    stats = r112._stats(candidate)
    boot = bootstrap(returns)
    rolling = rolling_returns(returns, PATH_DAYS)
    rolling_mdds = []
    for start in range(0, len(values) - PATH_DAYS):
        window = values[start:start + PATH_DAYS + 1]
        peak = window[0]
        mdd = 0.0
        for value in window:
            peak = max(peak, value)
            mdd = min(mdd, value / peak - 1.0)
        rolling_mdds.append(mdd)
    recent_idx = next(i for i, day in enumerate(dates) if day >= RECENT_START)
    recent_return = values[-1] / values[recent_idx] - 1.0
    months = month_returns(candidate)
    month_values = [row["return"] for row in months]
    reference = json.loads(R312_RESULT.read_text())
    reference_full = reference["full_path"]["combined_vol15"]
    parity = {
        "total_return_abs_diff": round(abs(stats["total_return"] - reference_full["total_return"]), 12),
        "sharpe_abs_diff": round(abs(stats["sharpe"] - reference_full["sharpe"]), 12),
        "mdd_abs_diff": round(abs(stats["mdd"] - reference_full["mdd"]), 12),
    }
    gates = {
        "D1": len(returns) >= 1300 and boot["paths"] == N_PATHS,
        "G1": boot["positive_rate"] >= 0.85,
        "G2": boot["return_p05"] >= -0.15,
        "G3": boot["return_median"] >= 0.05,
        "G4": boot["mdd_p05"] >= -0.30,
        "G5": sum(x > 0 for x in rolling) / len(rolling) >= 0.75,
        "G6": min(rolling) >= -0.20,
        "G7": recent_return > 0,
    }
    payload = {
        "round": "R315",
        "classification": "preregistered holding-experience audit; no parameter search",
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__).resolve()),
        "r312_result_sha256": sha(R312_RESULT),
        "period": [str(START), str(END)],
        "candidate_stats_2x": stats,
        "r312_parity": parity,
        "bootstrap": {**boot, "block_days": BLOCK_DAYS, "path_days": PATH_DAYS, "seed": SEED},
        "actual_rolling_252": {
            "windows": len(rolling),
            "positive_rate": round(sum(x > 0 for x in rolling) / len(rolling), 6),
            "return_p05": round(quantile(rolling, 0.05), 6),
            "return_median": round(quantile(rolling, 0.50), 6),
            "return_worst": round(min(rolling), 6),
            "return_best": round(max(rolling), 6),
            "mdd_p05": round(quantile(rolling_mdds, 0.05), 6),
            "mdd_median": round(quantile(rolling_mdds, 0.50), 6),
            "mdd_worst": round(min(rolling_mdds), 6),
        },
        "recent_1y_return": round(recent_return, 6),
        "yearly_returns": year_returns(candidate),
        "monthly": {
            "months": len(months),
            "positive_rate": round(sum(x > 0 for x in month_values) / len(month_values), 6),
            "median": round(quantile(month_values, 0.50), 6),
            "worst_month": min(months, key=lambda x: x["return"]),
            "best_month": max(months, key=lambda x: x["return"]),
            "rows": months,
        },
        "drawdown_duration": drawdown_duration(candidate),
        "gates": gates,
        "selected": all(gates.values()),
        "input_isolation": {
            "r40_before": before_r40, "r40_after": after_r40,
            "production_before": before_prod, "production_after": after_prod,
            "stable": before_r40 == after_r40 and before_prod == after_prod,
        },
        "decision": {
            "status": "holding_profile_passed" if all(gates.values()) else "holding_profile_weak",
            "production_write": False, "provider_call": False, "parameter_search": False,
        },
        "limits": [
            "Block bootstrap reuses historical returns and cannot create unseen regimes.",
            "The candidate remains dependent on the local R40 research snapshot.",
            "Monthly win rate is descriptive and not used as a promotion gate.",
        ],
        "daily_returns": [
            {"date": str(dates[i + 1]), "return": round(returns[i], 10)}
            for i in range(len(returns))
        ],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "stats": stats,
        "bootstrap": boot,
        "rolling": payload["actual_rolling_252"],
        "recent": recent_return,
        "yearly": payload["yearly_returns"],
        "monthly": {k: payload["monthly"][k] for k in ("months", "positive_rate", "median", "worst_month", "best_month")},
        "gates": gates,
        "selected": payload["selected"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
