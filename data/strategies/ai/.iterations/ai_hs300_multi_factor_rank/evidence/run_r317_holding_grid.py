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
PREREG = EVIDENCE / "r317_holding_grid_preregistration.json"
R314_RESULT = EVIDENCE / "r314_parameter_plateau.json"
R314_RUNNER = EVIDENCE / "run_r314_parameter_plateau.py"
R315_RUNNER = EVIDENCE / "run_r315_holding_bootstrap.py"
OUTPUT = EVIDENCE / "r317_holding_grid.json"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2021, 6, 1)
END = date(2026, 10, 14)
CENTERS = [f"rp{lb}_vol{int(tv * 100)}" for lb in (40, 60, 90) for tv in (0.12, 0.15, 0.18)]

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
        raise RuntimeError("R317 result exists; refusing overwrite")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R317 preregistration checksum mismatch")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r317")
    r314 = module(R314_RUNNER, "r314_r317")
    r315 = module(R315_RUNNER, "r315_r317")
    source = json.loads(R314_RESULT.read_text())
    members = membership()
    before_r40 = tree_hash(R40 / "kline_daily_enriched")
    before_prod = tree_hash(DATA / "kline_daily_enriched")
    work = Path(tempfile.mkdtemp(prefix="r317_"))
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

        a = run(A_STRATEGY)
        b = run(B_STRATEGY)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = tree_hash(R40 / "kline_daily_enriched")
    after_prod = tree_hash(DATA / "kline_daily_enriched")
    rows = {}
    parity = {}
    for key in CENTERS:
        cell = source["cells"][key]
        risk, _ = r314.combine_risk(a, b, int(cell["risk_parity_lookback"]))
        overlay, weights = r314.vol_target(risk, float(cell["target_vol"]))
        curve = overlay
        values = curve["value"].to_list()
        returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
        stats = r112._stats(curve)
        boot = r315.bootstrap(returns)
        rolling = r315.rolling_returns(returns, 252)
        weight_values = [float(row["weight"]) for row in weights]
        rows[key] = {
            "full": stats,
            "bootstrap": boot,
            "actual_rolling_252": {
                "windows": len(rolling),
                "positive_rate": round(sum(x > 0 for x in rolling) / len(rolling), 6),
                "return_p05": round(r315.quantile(rolling, 0.05), 6),
                "return_median": round(r315.quantile(rolling, 0.50), 6),
                "return_worst": round(min(rolling), 6),
            },
            "recent": cell["recent"],
            "stress_3x": cell["stress_3x"],
            "weights": {"min": round(min(weight_values), 6), "max": round(max(weight_values), 6), "mean": round(st.mean(weight_values), 6)},
        }
        parity[key] = {
            "total_return_abs_diff": round(abs(stats["total_return"] - cell["full"]["total_return"]), 12),
            "annual_abs_diff": round(abs(stats["annual"] - cell["full"]["annual"]), 12),
            "sharpe_abs_diff": round(abs(stats["sharpe"] - cell["full"]["sharpe"]), 12),
            "mdd_abs_diff": round(abs(stats["mdd"] - cell["full"]["mdd"]), 12),
        }
    gates = {}
    for key, row in rows.items():
        gates[key] = {
            "G1_full_return": row["full"]["annual"] >= 0.05,
            "G2_full_drawdown": row["full"]["mdd"] >= -0.22,
            "G3_bootstrap_positive": row["bootstrap"]["positive_rate"] >= 0.80,
            "G4_bootstrap_tail": row["bootstrap"]["return_p05"] >= -0.12,
            "G5_bootstrap_median": row["bootstrap"]["return_median"] >= 0.05,
            "G6_actual_rolling": row["actual_rolling_252"]["positive_rate"] >= 0.70,
            "G7_recent": row["recent"]["total_return_mean"] > 0 and row["recent"]["positive_phases"] >= 4,
            "G8_stress": row["stress_3x"]["total_return"] > 0 and row["stress_3x"]["mdd"] >= -0.30,
            "G9_no_leverage": row["weights"]["min"] >= 0 and row["weights"]["max"] <= 1.0,
        }
    passers = [key for key, value in gates.items() if all(value.values())]
    ordered = sorted(passers, key=lambda key: (rows[key]["bootstrap"]["positive_rate"], rows[key]["bootstrap"]["return_median"]), reverse=True)
    selected = ordered[0] if ordered else None
    d1 = (
        len(rows) == 9
        and all(max(v.values()) <= 1e-9 for v in parity.values())
        and all(row["bootstrap"]["paths"] == 1000 for row in rows.values())
    )
    payload = {
        "round": "R317",
        "classification": "preregistered holding-profile selection across the existing 3x3 grid; no new factor or weight search",
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__).resolve()),
        "source_r314_sha256": sha(R314_RESULT),
        "center": "rp60_vol15",
        "rows": rows,
        "parity_with_r314": parity,
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
            "status": "holding_profile_cell_selected" if selected else "no_cell_passed_holding_profile",
            "selected_cell": selected,
            "center_retained": selected is None,
            "production_write": False, "provider_call": False, "parameter_search": False,
        },
        "limits": [
            "All grid cells share the same history and are not independent out-of-sample evidence.",
            "The 3x3 grid was previously used for R314 plateau analysis; this round reuses it only for holding-profile selection.",
            "R40 local snapshot and canonical data drift remain unresolved.",
            "A selected cell would still require paper-trading review."
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
            key: {
                "annual": row["full"]["annual"],
                "mdd": row["full"]["mdd"],
                "bootstrap_positive": row["bootstrap"]["positive_rate"],
                "bootstrap_p05": row["bootstrap"]["return_p05"],
                "bootstrap_median": row["bootstrap"]["return_median"],
                "rolling_positive": row["actual_rolling_252"]["positive_rate"],
            }
            for key, row in rows.items()
        },
        "gates": gates,
        "D1": d1,
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
