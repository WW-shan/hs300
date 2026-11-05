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
PREREG = EVIDENCE / "r320_forecast_tail_filter_preregistration.json"
R315_RUNNER = EVIDENCE / "run_r315_holding_bootstrap.py"
OUTPUT = EVIDENCE / "r320_forecast_tail_filter.json"
A_BASE = "ai_hs300_macro_gate_quality"
B_BASE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
A_FILTER = "ai_hs300_macro_gate_quality_forecastfilter"
B_FILTER = "ai_hs300_industry_relative_strength_quality_h20_sl20_forecastfilter"
START = date(2021, 6, 1)
END = date(2026, 10, 14)
RECENT = [
    date(2025, 10, 14), date(2025, 12, 15), date(2026, 2, 13),
    date(2026, 4, 15), date(2026, 6, 15),
]
VARIANTS = {
    "control": (A_BASE, B_BASE),
    "B_filter": (A_BASE, B_FILTER),
    "A_filter": (A_FILTER, B_BASE),
    "AB_filter": (A_FILTER, B_FILTER),
}

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


def forecast_data_audit() -> dict:
    paths = sorted((DATA / "ext_data/alt_hs300/timeseries").glob("date=*/part.parquet"))
    total = 0
    nonnull = 0
    negative = 0
    zero = 0
    positive = 0
    for path in paths:
        frame = pl.read_parquet(path, columns=["forecast_surprise_60d"])
        series = frame["forecast_surprise_60d"].drop_nulls()
        total += frame.height
        nonnull += series.len()
        negative += int((series < 0).sum())
        zero += int((series == 0).sum())
        positive += int((series > 0).sum())
    return {
        "partitions": len(paths),
        "rows": total,
        "nonnull": nonnull,
        "coverage": round(nonnull / total, 6) if total else None,
        "negative": negative,
        "positive": positive,
        "zero": zero,
        "negative_share": round(negative / nonnull, 6) if nonnull else None,
        "date_min": paths[0].parent.name if paths else None,
        "date_max": paths[-1].parent.name if paths else None,
    }


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R320 result exists; refusing overwrite")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R320 preregistration checksum mismatch")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r320")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r320")
    r315 = module(R315_RUNNER, "r315_r320")
    r315_file = EVIDENCE / "run_r315_holding_bootstrap.py"
    r114.TARGET_VOL = 0.15
    data_audit = forecast_data_audit()
    members = membership()
    before_r40 = tree_hash(R40 / "kline_daily_enriched")
    before_prod = tree_hash(DATA / "kline_daily_enriched")
    work = Path(tempfile.mkdtemp(prefix="r320_"))
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

        def run_pairs(start: date, cost_factor: int) -> dict:
            return {
                "A_base": run(A_BASE, start, cost_factor),
                "A_filter": run(A_FILTER, start, cost_factor),
                "B_base": run(B_BASE, start, cost_factor),
                "B_filter": run(B_FILTER, start, cost_factor),
            }

        full_pairs = run_pairs(START, 2)
        stress_pairs = run_pairs(START, 3)
        recent_pairs = {day.isoformat(): run_pairs(day, 2) for day in RECENT}
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = tree_hash(R40 / "kline_daily_enriched")
    after_prod = tree_hash(DATA / "kline_daily_enriched")

    def portfolio(pairs: dict, a_key: str, b_key: str) -> tuple[pl.DataFrame, list[dict]]:
        risk, rp_weights = r112._combine(pairs[a_key], pairs[b_key], "risk")
        overlay = r114._vol_target(risk)
        return overlay["curve"], overlay["weight_records"]

    rows = {}
    for name, (a_key, b_key) in {
        "control": ("A_base", "B_base"),
        "B_filter": ("A_base", "B_filter"),
        "A_filter": ("A_filter", "B_base"),
        "AB_filter": ("A_filter", "B_filter"),
    }.items():
        full_curve, full_weights = portfolio(full_pairs, a_key, b_key)
        stress_curve, _ = portfolio(stress_pairs, a_key, b_key)
        recent_curves = {key: portfolio(recent_pairs[key], a_key, b_key)[0] for key in sorted(recent_pairs)}
        values = full_curve["value"].to_list()
        returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
        boot = r315.bootstrap(returns)
        rolling = r315.rolling_returns(returns, 252)
        weights = [float(row["weight"]) for row in full_weights]
        rows[name] = {
            "full": r112._stats(full_curve),
            "stress": r112._stats(stress_curve),
            "recent": r112._aggregate([r112._stats(curve) for curve in recent_curves.values()]),
            "bootstrap": boot,
            "actual_rolling_252": {
                "windows": len(rolling),
                "positive_rate": round(sum(x > 0 for x in rolling) / len(rolling), 6),
                "return_p05": round(r315.quantile(rolling, 0.05), 6),
                "return_median": round(r315.quantile(rolling, 0.50), 6),
                "return_worst": round(min(rolling), 6),
            },
            "yearly_returns": r315.year_returns(full_curve),
            "weights": {"mean": round(st.mean(weights), 6), "min": round(min(weights), 6), "max": round(max(weights), 6)},
            "recent_positive_phases": sum(r112._stats(curve)["total_return"] > 0 for curve in recent_curves.values()),
        }
    control_years = rows["control"]["yearly_returns"]
    gates = {}
    for name, row in rows.items():
        gates[name] = {
            "G1_full_return": row["full"]["annual"] >= 0.05,
            "G2_full_drawdown": row["full"]["mdd"] >= -0.22,
            "G3_bootstrap_positive": row["bootstrap"]["positive_rate"] >= 0.80,
            "G4_actual_rolling": row["actual_rolling_252"]["positive_rate"] >= 0.70,
            "G5_year_increment": (
                row["yearly_returns"].get("2022", -1) >= control_years.get("2022", -1) + 0.01
                and row["yearly_returns"].get("2023", -1) >= control_years.get("2023", -1) + 0.01
            ),
            "G6_recent": row["recent"]["total_return_mean"] > 0 and row["recent"]["positive_phases"] >= 4,
            "G7_stress": row["stress"]["total_return"] > 0 and row["stress"]["mdd"] >= -0.30,
            "G8_no_leverage": row["weights"]["min"] >= 0 and row["weights"]["max"] <= 1.0,
        }
    passers = [name for name, values in gates.items() if all(values.values())]
    ordered = sorted(passers, key=lambda name: (rows[name]["bootstrap"]["positive_rate"], rows[name]["bootstrap"]["return_median"]), reverse=True)
    selected = ordered[0] if ordered else None
    d1 = (
        data_audit["coverage"] >= 0.95
        and 0.01 <= data_audit["negative_share"] <= 0.10
        and all(math.isfinite(row["full"]["total_return"]) for row in rows.values())
    )
    payload = {
        "round": "R320",
        "classification": "preregistered negative-earnings-forecast tail-filter experiment; no scoring or risk-parameter tuning",
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__).resolve()),
        "r315_runner_sha256": sha(r315_file),
        "data_audit": data_audit,
        "variants": {k: [v[0], v[1]] for k, v in VARIANTS.items()},
        "rows": rows,
        "gates": gates,
        "D1": d1,
        "passers": passers,
        "selected": selected,
        "input_isolation": {
            "r40_before": before_r40, "r40_after": after_r40,
            "production_before": before_prod, "production_after": after_prod,
            "stable": before_r40 == after_r40 and before_prod == after_prod,
        },
        "decision": {
            "status": "forecast_filter_candidate_for_concentration_audit" if selected else "no_forecast_filter_variant_passed",
            "selected_variant": selected,
            "control_retained": selected is None,
            "production_write": False, "provider_call": False, "parameter_search": False,
        },
        "limits": [
            "R63 found high positive forecast surprise was not a stable long-only strategy; this tests only negative-tail exclusion.",
            "The filter is sparse (about 4% negative rows) and may not change annual outcomes.",
            "All variants share the local R40 snapshot and current factor/industry snapshots.",
            "A passing variant still requires a separate concentration audit and paper-execution review."
        ],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "selected": selected,
        "passers": passers,
        "data_audit": data_audit,
        "rows": {
            name: {
                "annual": row["full"]["annual"],
                "sharpe": row["full"]["sharpe"],
                "mdd": row["full"]["mdd"],
                "bootstrap_positive": row["bootstrap"]["positive_rate"],
                "rolling_positive": row["actual_rolling_252"]["positive_rate"],
                "recent_mean": row["recent"]["total_return_mean"],
                "recent_positive_phases": row["recent"]["positive_phases"],
                "stress_return": row["stress"]["total_return"],
                "stress_mdd": row["stress"]["mdd"],
                "yearly_returns": row["yearly_returns"],
            }
            for name, row in rows.items()
        },
        "gates": gates,
        "D1": d1,
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
