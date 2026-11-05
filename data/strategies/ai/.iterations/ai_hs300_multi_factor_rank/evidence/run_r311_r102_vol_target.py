from __future__ import annotations

import hashlib
import importlib.util
import json
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
PREREG = EVIDENCE / "r311_r102_vol_target_preregistration.json"
OUTPUT = EVIDENCE / "r311_r102_vol_target.json"
R309 = EVIDENCE / "run_r309_r40_reproducibility.py"
R114 = ROOT / "scripts/backtest_r114_b_vol_target.py"
R112 = ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py"
STRATEGY_ID = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2021, 6, 1)
END = date(2026, 10, 14)
PHASES = [
    date(2021, 6, 1), date(2022, 1, 4), date(2022, 7, 1),
    date(2023, 1, 3), date(2023, 7, 3), date(2024, 1, 2),
    date(2024, 7, 1), date(2025, 1, 2), date(2025, 7, 1), date(2026, 1, 5),
]
RECENT = [
    date(2025, 10, 14), date(2025, 12, 15), date(2026, 2, 13),
    date(2026, 4, 15), date(2026, 6, 15),
]
sys.path.insert(0, str(DATA / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"))
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
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(root: Path) -> dict:
    paths = sorted(root.glob("date=*/part.parquet"))
    h = hashlib.sha256()
    size = 0
    for path in paths:
        h.update(path.relative_to(ROOT).as_posix().encode())
        h.update(b"\0")
        h.update(sha(path).encode())
        h.update(b"\n")
        size += path.stat().st_size
    return {"files": len(paths), "bytes": size, "sha256": h.hexdigest()}


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


def fixed_exposure(curve: pl.DataFrame, weight: float = 0.5, cost: float = 0.0052) -> dict:
    dates = curve["date"].to_list()
    values = curve["value"].to_list()
    returns = [values[i] / values[i - 1] - 1 for i in range(1, len(values))]
    portfolio = 1.0
    sleeve = 1.0
    target = weight
    rows = [{"date": dates[0], "value": portfolio}]
    weights = [{"date": dates[0], "weight": target}]
    turnover = 0.0
    for i in range(1, len(dates)):
        day = dates[i]
        ret = returns[i - 1]
        sleeve *= 1.0 + ret
        portfolio *= 1.0 + target * ret
        next_month = i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (day.year, day.month)
        if next_month:
            total = target * sleeve + (1.0 - target)
            actual = target * sleeve / total if total else target
            nxt = weight
            turnover += abs(nxt - actual)
            portfolio *= 1.0 - abs(nxt - actual) * cost
            sleeve = 1.0
            target = nxt
        rows.append({"date": day, "value": portfolio})
        if next_month and i + 1 < len(dates):
            weights.append({"date": dates[i + 1], "weight": target})
    return {"curve": pl.DataFrame(rows), "weights": weights, "turnover": turnover}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R311 result exists; refusing overwrite")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R311 preregistration checksum mismatch")
    r114 = module(R114, "r114")
    r112 = module(R112, "r112")
    members = membership()
    before_r40 = tree_hash(R40 / "kline_daily_enriched")
    before_prod = tree_hash(DATA / "kline_daily_enriched")
    work = Path(tempfile.mkdtemp(prefix="r311_r40_"))
    all_runs = {}
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

        def run(start: date, cost: float) -> pl.DataFrame:
            scale = cost / 2.0
            cfg = StrategyBacktestConfig(
                strategy_id=STRATEGY_ID, symbols=None, start=start, end=END,
                params={}, overrides={}, matching="open_t+1",
                fees_pct=0.0004 * scale, commission_pct=0.0004 * scale,
                stamp_tax_pct=0.0005, slippage_bps=10.0 * scale,
                max_positions=10, max_exposure_pct=1.0,
                position_sizing="equal", mode="position", asset_type="stock", hs300=True,
            )
            with patched_membership(members):
                result = service.run(cfg)
            if result.error:
                raise RuntimeError(f"{start} {cost}x: {result.error}")
            return curve_frame(result.equity_curve)

        for label, starts in (("phases", PHASES), ("recent", RECENT)):
            all_runs[label] = {}
            for start in starts:
                key = start.isoformat()
                base = run(start, 2.0)
                r114.TARGET_VOL = 0.15
                vol15 = r114._vol_target(base)["curve"]
                r114.TARGET_VOL = 0.20
                vol20 = r114._vol_target(base)["curve"]
                fixed = fixed_exposure(base)["curve"]
                all_runs[label][key] = {
                    "base": r112._stats(base),
                    "vol15": r112._stats(vol15),
                    "vol20": r112._stats(vol20),
                    "fixed50": r112._stats(fixed),
                }
                print(f"{label} {key}: base={all_runs[label][key]['base']['total_return']:.3f} vol15={all_runs[label][key]['vol15']['total_return']:.3f} vol20={all_runs[label][key]['vol20']['total_return']:.3f}", flush=True)
        # 3x full and recent stress
        stress = {}
        for start in [START, *RECENT]:
            base = run(start, 3.0)
            r114.TARGET_VOL = 0.15
            v15 = r114._vol_target(base)["curve"]
            r114.TARGET_VOL = 0.20
            v20 = r114._vol_target(base)["curve"]
            stress[start.isoformat()] = {"base": base, "vol15": v15, "vol20": v20}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    after_r40 = tree_hash(R40 / "kline_daily_enriched")
    after_prod = tree_hash(DATA / "kline_daily_enriched")

    def agg(records: dict, name: str) -> dict:
        return r112._aggregate([records[k][name] for k in sorted(records)])

    long = {name: agg(all_runs["phases"], name) for name in ("base", "vol15", "vol20", "fixed50")}
    recent = {name: agg(all_runs["recent"], name) for name in ("base", "vol15", "vol20", "fixed50")}
    stress_long = {name: r112._stats(stress[START.isoformat()][name]) for name in ("base", "vol15", "vol20")}
    stress_recent = {
        name: r112._aggregate([r112._stats(stress[d.isoformat()][name]) for d in RECENT])
        for name in ("base", "vol15", "vol20")
    }
    candidates = {}
    for name in ("vol15", "vol20"):
        x = long[name]; y = recent[name]; z = stress_long[name]
        gates = {
            "G1_execution": len(all_runs["phases"]) == 10 and len(all_runs["recent"]) == 5,
            "G2_drawdown": x["mdd_mean"] >= -0.25,
            "G3_recent": y["total_return_mean"] > 0 and y["positive_phases"] >= 4,
            "G4_return": x["annual_mean"] >= 0.05 and x["sharpe_mean"] >= 0.35 and x["positive_phases"] == 10,
            "G5_cost": z["total_return"] > 0 and z["mdd"] >= -0.30,
        }
        fixed = long["fixed50"]
        gates["G6_control"] = (x["mdd_mean"] >= fixed["mdd_mean"] + 0.05) or (x["sharpe_mean"] >= fixed["sharpe_mean"] + 0.05)
        candidates[name] = {"gates": gates, "long": x, "recent": y, "stress_long": z}
    selected = [name for name, row in candidates.items() if all(row["gates"].values())]
    payload = {
        "round": "R311",
        "classification": "preregistered fixed-factor risk-budget experiment on R40; no parameter tuning",
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__).resolve()),
        "periods": {"full": [str(START), str(END)], "phases": [str(x) for x in PHASES], "recent": [str(x) for x in RECENT]},
        "strategy": STRATEGY_ID,
        "execution": {"matching": "open_t+1", "membership": "R78 exact daily", "main_cost": "2x", "stress_cost": "3x"},
        "long": long,
        "recent": recent,
        "stress_full": stress_long,
        "stress_recent": stress_recent,
        "candidates": candidates,
        "selected": selected,
        "input_isolation": {"r40_before": before_r40, "r40_after": after_r40, "production_before": before_prod, "production_after": after_prod, "stable": before_r40 == after_r40 and before_prod == after_prod},
        "decision": {"status": "candidate_for_separate_review" if selected else "no_candidate_passed", "production_write": False, "provider_call": False, "parameter_search": False},
        "limits": ["R40 is a local research snapshot, not an official archive.", "Monthly overlay cost is 0.52% round trip; this is not a full market-impact model.", "Passing this round would not authorize production or paper trading."],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({"selected": selected, "gates": {k:v["gates"] for k,v in candidates.items()}, "long": {k: {q: long[k][q] for q in ("total_return_mean","annual_mean","sharpe_mean","mdd_mean","positive_phases")} for k in long}, "recent": {k: {q: recent[k][q] for q in ("total_return_mean","positive_phases")} for k in recent}, "output": str(OUTPUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
