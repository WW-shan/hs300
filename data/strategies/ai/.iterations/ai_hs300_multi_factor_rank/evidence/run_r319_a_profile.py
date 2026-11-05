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
PREREG = EVIDENCE / "r319_a_profile_preregistration.json"
R315_RUNNER = EVIDENCE / "run_r315_holding_bootstrap.py"
OUTPUT = EVIDENCE / "r319_a_profile.json"
STRATEGY = "ai_hs300_macro_gate_quality"
START = date(2021, 6, 1)
END = date(2026, 10, 14)
RECENT = [
    date(2025, 10, 14), date(2025, 12, 15), date(2026, 2, 13),
    date(2026, 4, 15), date(2026, 6, 15),
]

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


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R319 result exists; refusing overwrite")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R319 preregistration checksum mismatch")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r319")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r319")
    r315 = module(R315_RUNNER, "r315_r319")
    members = membership()
    before_r40 = tree_hash(R40 / "kline_daily_enriched")
    before_prod = tree_hash(DATA / "kline_daily_enriched")
    work = Path(tempfile.mkdtemp(prefix="r319_"))
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

        def run(start: date, cost_factor: int) -> pl.DataFrame:
            cfg = StrategyBacktestConfig(
                strategy_id=STRATEGY, symbols=None, start=start, end=END,
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
                raise RuntimeError(f"{STRATEGY} {start} {cost_factor}x: {result.error}")
            return curve_frame(result.equity_curve)

        full = run(START, 2)
        stress = run(START, 3)
        recent = {day.isoformat(): run(day, 2) for day in RECENT}
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = tree_hash(R40 / "kline_daily_enriched")
    after_prod = tree_hash(DATA / "kline_daily_enriched")

    def make_variants(base: pl.DataFrame) -> dict:
        r114.TARGET_VOL = 0.12
        v12 = r114._vol_target(base)
        r114.TARGET_VOL = 0.15
        v15 = r114._vol_target(base)
        return {"A_raw": {"curve": base, "weights": []}, "A_vol12": v12, "A_vol15": v15}

    full_variants = make_variants(full)
    stress_variants = make_variants(stress)
    rows = {}
    for name in ("A_raw", "A_vol12", "A_vol15"):
        full_curve = full_variants[name]["curve"]
        full_stats = r112._stats(full_curve)
        values = full_curve["value"].to_list()
        returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
        boot = r315.bootstrap(returns)
        rolling = r315.rolling_returns(returns, 252)
        recent_stats = r112._aggregate([r112._stats(make_variants(recent[key])[name]["curve"]) for key in sorted(recent)])
        if name == "A_raw":
            weights = [1.0]
        else:
            weights = [float(row["weight"]) for row in full_variants[name]["weight_records"]]
        rows[name] = {
            "full": full_stats,
            "stress": r112._stats(stress_variants[name]["curve"]),
            "recent": recent_stats,
            "bootstrap": boot,
            "actual_rolling_252": {
                "windows": len(rolling),
                "positive_rate": round(sum(x > 0 for x in rolling) / len(rolling), 6),
                "return_p05": round(r315.quantile(rolling, 0.05), 6),
                "return_median": round(r315.quantile(rolling, 0.50), 6),
                "return_worst": round(min(rolling), 6),
            },
            "yearly_returns": r315.year_returns(full_curve),
            "drawdown_duration": r315.drawdown_duration(full_curve),
            "weights": {"min": round(min(weights), 6), "max": round(max(weights), 6), "mean": round(st.mean(weights), 6)},
        }
    gates = {}
    for name, row in rows.items():
        gates[name] = {
            "G1_return": row["full"]["annual"] >= 0.03,
            "G2_drawdown": row["full"]["mdd"] >= -0.18,
            "G3_bootstrap_positive": row["bootstrap"]["positive_rate"] >= 0.80,
            "G4_actual_rolling": row["actual_rolling_252"]["positive_rate"] >= 0.70,
            "G5_recent": row["recent"]["total_return_mean"] > 0 and row["recent"]["positive_phases"] >= 4,
            "G6_stress": row["stress"]["total_return"] > 0 and row["stress"]["mdd"] >= -0.25,
            "G7_no_leverage": row["weights"]["min"] >= 0 and row["weights"]["max"] <= 1.0,
        }
    passers = [name for name, gate in gates.items() if all(gate.values())]
    ordered = sorted(passers, key=lambda name: (rows[name]["bootstrap"]["positive_rate"], rows[name]["full"]["annual"]), reverse=True)
    selected = ordered[0] if ordered else None
    payload = {
        "round": "R319",
        "classification": "preregistered conservative-profile comparison; no new factor",
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__).resolve()),
        "strategy": STRATEGY,
        "rows": rows,
        "gates": gates,
        "passers": passers,
        "selected": selected,
        "input_isolation": {
            "r40_before": before_r40, "r40_after": after_r40,
            "production_before": before_prod, "production_after": after_prod,
            "stable": before_r40 == after_r40 and before_prod == after_prod,
        },
        "decision": {
            "status": "conservative_profile_candidate" if selected else "no_conservative_profile_passed",
            "selected_variant": selected,
            "production_write": False, "provider_call": False, "parameter_search": False,
            "deployment_blocker": "A sleeve failed standalone C2-C4 concentration in R266; a pass here is research-only."
        },
        "limits": [
            "A sleeve standalone concentration failed C2-C4 in R266.",
            "No new alpha was introduced; this is a risk-profile comparison.",
            "R40 local snapshot and canonical data drift remain unresolved.",
            "No production or paper execution is authorized."
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
                "sharpe": row["full"]["sharpe"],
                "mdd": row["full"]["mdd"],
                "bootstrap_positive": row["bootstrap"]["positive_rate"],
                "rolling_positive": row["actual_rolling_252"]["positive_rate"],
                "recent_mean": row["recent"]["total_return_mean"],
                "recent_positive_phases": row["recent"]["positive_phases"],
                "stress_return": row["stress"]["total_return"],
                "stress_mdd": row["stress"]["mdd"],
                "yearly_returns": row["yearly_returns"],
                "longest_underwater": row["drawdown_duration"]["longest_underwater"],
            }
            for name, row in rows.items()
        },
        "gates": gates,
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
