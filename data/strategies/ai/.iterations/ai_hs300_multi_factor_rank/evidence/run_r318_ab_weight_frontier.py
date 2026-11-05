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
PREREG = EVIDENCE / "r318_ab_weight_frontier_preregistration.json"
R312_RESULT = EVIDENCE / "r312_dual_sleeve_vol15.json"
R315_RUNNER = EVIDENCE / "run_r315_holding_bootstrap.py"
OUTPUT = EVIDENCE / "r318_ab_weight_frontier.json"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2021, 6, 1)
END = date(2026, 10, 14)
RECENT = [
    date(2025, 10, 14), date(2025, 12, 15), date(2026, 2, 13),
    date(2026, 4, 15), date(2026, 6, 15),
]
COST = 0.0052
VARIANTS = {
    "rp_cap08": ("risk", 0.8),
    "rp_cap09": ("risk", 0.9),
    "rp_cap10": ("risk", 1.0),
    "fixed70": ("fixed", 0.7),
    "fixed80": ("fixed", 0.8),
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


def combine(a: pl.DataFrame, b: pl.DataFrame, mode: str, value: float) -> pl.DataFrame:
    joined = a.rename({"value": "a"}).join(b.rename({"value": "b"}), on="date", how="inner").sort("date")
    dates = joined["date"].to_list()
    values_a = joined["a"].to_list()
    values_b = joined["b"].to_list()
    returns_a = [values_a[i] / values_a[i - 1] - 1.0 for i in range(1, len(values_a))]
    returns_b = [values_b[i] / values_b[i - 1] - 1.0 for i in range(1, len(values_b))]
    targets = []
    prev_month = None
    for i, day in enumerate(dates):
        month = (day.year, day.month)
        if i == 0 or month != prev_month:
            if mode == "fixed":
                weight = value
            elif i < 60:
                weight = 0.5
            else:
                vol_a = max(st.pstdev(returns_a[i - 60:i]), 1e-6)
                vol_b = max(st.pstdev(returns_b[i - 60:i]), 1e-6)
                weight = (1.0 / vol_a) / (1.0 / vol_a + 1.0 / vol_b)
                weight = min(value, max(0.2, weight))
            targets.append((day, weight))
            prev_month = month
    target_map = dict(targets)
    portfolio = 1.0
    sleeve_a = 1.0
    sleeve_b = 1.0
    target = target_map[dates[0]]
    rows = [{"date": dates[0], "value": portfolio}]
    for i in range(1, len(dates)):
        day = dates[i]
        sleeve_a *= 1.0 + returns_a[i - 1]
        sleeve_b *= 1.0 + returns_b[i - 1]
        portfolio *= 1.0 + target * returns_a[i - 1] + (1.0 - target) * returns_b[i - 1]
        next_month = i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (day.year, day.month)
        if next_month:
            total = target * sleeve_a + (1.0 - target) * sleeve_b
            actual_a = target * sleeve_a / total if total else target
            actual_b = 1.0 - actual_a
            next_target = target_map[dates[i + 1]] if i + 1 < len(dates) else target
            turnover = 0.5 * (abs(next_target - actual_a) + abs((1.0 - next_target) - actual_b))
            portfolio *= 1.0 - turnover * COST
            sleeve_a = 1.0
            sleeve_b = 1.0
            target = next_target
        rows.append({"date": day, "value": portfolio})
    return pl.DataFrame(rows)


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R318 result exists; refusing overwrite")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R318 preregistration checksum mismatch")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r318")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r318")
    r315 = module(R315_RUNNER, "r315_r318")
    r114.TARGET_VOL = 0.15
    reference = json.loads(R312_RESULT.read_text())
    members = membership()
    before_r40 = tree_hash(R40 / "kline_daily_enriched")
    before_prod = tree_hash(DATA / "kline_daily_enriched")
    work = Path(tempfile.mkdtemp(prefix="r318_"))
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

        def sleeve_pair(start: date, cost_factor: int):
            return run(A_STRATEGY, start, cost_factor), run(B_STRATEGY, start, cost_factor)

        full_a, full_b = sleeve_pair(START, 2)
        stress_a, stress_b = sleeve_pair(START, 3)
        recent_pairs = {day.isoformat(): sleeve_pair(day, 2) for day in RECENT}
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = tree_hash(R40 / "kline_daily_enriched")
    after_prod = tree_hash(DATA / "kline_daily_enriched")
    rows = {}
    for name, (mode, value) in VARIANTS.items():
        full_risk = combine(full_a, full_b, mode, value)
        stress_risk = combine(stress_a, stress_b, mode, value)
        full_overlay = r114._vol_target(full_risk)
        stress_overlay = r114._vol_target(stress_risk)
        recent_stats = []
        for pair in recent_pairs.values():
            recent_risk = combine(pair[0], pair[1], mode, value)
            recent_stats.append(r112._stats(r114._vol_target(recent_risk)["curve"]))
        values = full_overlay["curve"]["value"].to_list()
        returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
        boot = r315.bootstrap(returns)
        rolling = r315.rolling_returns(returns, 252)
        weights = [float(row["weight"]) for row in full_overlay["weight_records"]]
        rows[name] = {
            "full": r112._stats(full_overlay["curve"]),
            "stress": r112._stats(stress_overlay["curve"]),
            "recent": r112._aggregate(recent_stats),
            "bootstrap": boot,
            "actual_rolling_252": {
                "windows": len(rolling),
                "positive_rate": round(sum(x > 0 for x in rolling) / len(rolling), 6),
                "return_p05": round(r315.quantile(rolling, 0.05), 6),
                "return_median": round(r315.quantile(rolling, 0.50), 6),
                "return_worst": round(min(rolling), 6),
            },
            "yearly_returns": r315.year_returns(full_overlay["curve"]),
            "weights": {"mean": round(st.mean(weights), 6), "min": round(min(weights), 6), "max": round(max(weights), 6)},
        }
    ref_full = reference["full_path"]["combined_vol15"]
    parity = {
        "total_return_abs_diff": round(abs(rows["rp_cap08"]["full"]["total_return"] - ref_full["total_return"]), 12),
        "sharpe_abs_diff": round(abs(rows["rp_cap08"]["full"]["sharpe"] - ref_full["sharpe"]), 12),
        "mdd_abs_diff": round(abs(rows["rp_cap08"]["full"]["mdd"] - ref_full["mdd"]), 12),
    }
    gates = {}
    for name, row in rows.items():
        gates[name] = {
            "G1_full_return": row["full"]["annual"] >= 0.05,
            "G2_full_drawdown": row["full"]["mdd"] >= -0.22,
            "G3_bootstrap_positive": row["bootstrap"]["positive_rate"] >= 0.80,
            "G4_bootstrap_tail": row["bootstrap"]["return_p05"] >= -0.12,
            "G5_bootstrap_median": row["bootstrap"]["return_median"] >= 0.05,
            "G6_actual_rolling": row["actual_rolling_252"]["positive_rate"] >= 0.70,
            "G7_recent": row["recent"]["total_return_mean"] > 0 and row["recent"]["positive_phases"] >= 4,
            "G8_stress": row["stress"]["total_return"] > 0 and row["stress"]["mdd"] >= -0.30,
            "G9_no_leverage": row["weights"]["min"] >= 0 and row["weights"]["max"] <= 1.0,
        }
    passers = [name for name, value in gates.items() if all(value.values())]
    ordered = sorted(passers, key=lambda name: (rows[name]["bootstrap"]["positive_rate"], rows[name]["bootstrap"]["return_median"]), reverse=True)
    selected = ordered[0] if ordered else None
    d1 = max(parity.values()) <= 1e-9 and all(row["bootstrap"]["paths"] == 1000 for row in rows.values())
    payload = {
        "round": "R318",
        "classification": "preregistered A/B risk-budget frontier; no factor changes",
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__).resolve()),
        "r312_result_sha256": sha(R312_RESULT),
        "variants": VARIANTS,
        "rows": rows,
        "current_parity": parity,
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
            "status": "weight_frontier_variant_selected" if selected else "no_weight_variant_passed",
            "selected_variant": selected,
            "current_cap08_retained": selected is None,
            "production_write": False, "provider_call": False, "parameter_search": False,
        },
        "limits": [
            "This is a risk-budget adjustment after observing the R315 holding profile, not independent alpha discovery.",
            "All variants share the same underlying A/B legs and history.",
            "R40 local snapshot and canonical data drift remain unresolved.",
            "A pass would still be research/paper-review only."
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
        "rows": {
            name: {
                "annual": row["full"]["annual"],
                "mdd": row["full"]["mdd"],
                "bootstrap_positive": row["bootstrap"]["positive_rate"],
                "bootstrap_p05": row["bootstrap"]["return_p05"],
                "bootstrap_median": row["bootstrap"]["return_median"],
                "rolling_positive": row["actual_rolling_252"]["positive_rate"],
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
