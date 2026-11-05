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
PREREG = EVIDENCE / "r312_dual_sleeve_vol15_preregistration.json"
OUTPUT = EVIDENCE / "r312_dual_sleeve_vol15.json"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
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
        raise RuntimeError("R312 result exists; refusing overwrite")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R312 preregistration checksum mismatch")

    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114")
    r114.TARGET_VOL = 0.15
    members = membership()
    before_r40 = tree_hash(R40 / "kline_daily_enriched")
    before_prod = tree_hash(DATA / "kline_daily_enriched")
    work = Path(tempfile.mkdtemp(prefix="r312_"))
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
                strategy_id=strategy_id,
                symbols=None,
                start=start,
                end=END,
                params={},
                overrides={},
                matching="open_t+1",
                fees_pct=0.0002 * cost_factor,
                commission_pct=0.0002 * cost_factor,
                stamp_tax_pct=0.0005 if cost_factor == 2 else 0.001,
                slippage_bps=5.0 * cost_factor,
                max_positions=10,
                max_exposure_pct=1.0,
                position_sizing="equal",
                mode="position",
                asset_type="stock",
                hs300=True,
            )
            with patched_membership(members):
                result = service.run(cfg)
            if result.error:
                raise RuntimeError(f"{strategy_id} {start} {cost_factor}x: {result.error}")
            return curve_frame(result.equity_curve)

        def portfolio(a: pl.DataFrame, b: pl.DataFrame) -> dict:
            risk, rp_weights = r112._combine(a, b, "risk")
            overlay = r114._vol_target(risk)
            return {
                "risk_parity": r112._stats(risk),
                "combined_vol15": r112._stats(overlay["curve"]),
                "correlation": round(r112._corr(a, b), 4),
                "risk_parity_weight_a": {
                    "mean": round(st.mean(row["weight_a"] for row in rp_weights), 6),
                    "min": round(min(row["weight_a"] for row in rp_weights), 6),
                    "max": round(max(row["weight_a"] for row in rp_weights), 6),
                },
                "overlay_weight": {
                    "mean": round(st.mean(row["weight"] for row in overlay["weight_records"]), 6),
                    "min": round(min(row["weight"] for row in overlay["weight_records"]), 6),
                    "max": round(max(row["weight"] for row in overlay["weight_records"]), 6),
                    "annual_turnover": round(float(overlay["annual_turnover"]), 6),
                },
                "_curve": overlay["curve"],
            }

        records: dict[str, dict] = {}
        for bucket, starts in (("phases", PHASES), ("recent", RECENT)):
            records[bucket] = {}
            for phase in starts:
                key = phase.isoformat()
                a = run(A_STRATEGY, phase, 2)
                b = run(B_STRATEGY, phase, 2)
                p = portfolio(a, b)
                b_vol15 = r114._vol_target(b)["curve"]
                rec = {
                    "A": r112._stats(a),
                    "B": r112._stats(b),
                    "B_vol15": r112._stats(b_vol15),
                    "risk_parity": p["risk_parity"],
                    "combined_vol15": p["combined_vol15"],
                    "correlation": p["correlation"],
                    "risk_parity_weight_a": p["risk_parity_weight_a"],
                    "overlay_weight": p["overlay_weight"],
                    "aligned_days": p["combined_vol15"]["days"],
                }
                records[bucket][key] = rec
                print(
                    f"{bucket} {key}: A={rec['A']['total_return']:.3f} B={rec['B']['total_return']:.3f} "
                    f"Bvol15={rec['B_vol15']['total_return']:.3f} combined={rec['combined_vol15']['total_return']:.3f}",
                    flush=True,
                )

        stress_a = run(A_STRATEGY, START, 3)
        stress_b = run(B_STRATEGY, START, 3)
        stress_p = portfolio(stress_a, stress_b)
        stress = {
            "A": r112._stats(stress_a),
            "B": r112._stats(stress_b),
            "combined_vol15": stress_p["combined_vol15"],
            "overlay_weight": stress_p["overlay_weight"],
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = tree_hash(R40 / "kline_daily_enriched")
    after_prod = tree_hash(DATA / "kline_daily_enriched")

    def agg(bucket: dict, name: str) -> dict:
        return r112._aggregate([bucket[k][name] for k in sorted(bucket)])

    long_combined = agg(records["phases"], "combined_vol15")
    long_b = agg(records["phases"], "B_vol15")
    long_a = agg(records["phases"], "A")
    recent_combined = agg(records["recent"], "combined_vol15")
    full = records["phases"][START.isoformat()]["combined_vol15"]
    b_full = records["phases"][START.isoformat()]["B_vol15"]
    gates = {
        "G1_execution": (
            len(records["phases"]) == 10
            and len(records["recent"]) == 5
            and all(x["aligned_days"] > 0 for x in records["phases"].values())
            and all(x["aligned_days"] > 0 for x in records["recent"].values())
        ),
        "G2_full_drawdown": full["mdd"] >= -0.25,
        "G3_full_return": full["annual"] >= 0.05 and full["sharpe"] >= 0.60,
        "G4_recent": recent_combined["total_return_mean"] > 0 and recent_combined["positive_phases"] >= 4,
        "G5_stress": stress["combined_vol15"]["total_return"] > 0 and stress["combined_vol15"]["mdd"] >= -0.30,
        "G6_incrementality": (
            (full["sharpe"] >= b_full["sharpe"] + 0.05 or full["mdd"] >= b_full["mdd"] + 0.03)
            and full["annual"] >= b_full["annual"] - 0.02
        ),
        "G7_no_leverage": all(
            0.0 <= rec["overlay_weight"]["min"] and rec["overlay_weight"]["max"] <= 1.0
            for rec in [*records["phases"].values(), *records["recent"].values()]
        ),
    }
    payload = {
        "round": "R312",
        "classification": "preregistered current-data dual-sleeve reconstruction; no parameter search",
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__).resolve()),
        "periods": {"full": [str(START), str(END)], "phases": [str(x) for x in PHASES], "recent": [str(x) for x in RECENT]},
        "sleeves": {"A": A_STRATEGY, "B": B_STRATEGY},
        "execution": {
            "matching": "open_t+1", "membership": "R78 exact daily",
            "main_cost": "2x commission 4bp + slippage 10bp + stamp 5bp",
            "stress_cost": "3x commission 6bp + slippage 15bp + stamp 10bp",
        },
        "portfolio_rule": "60d inverse-vol risk parity, A bounds [0.2,0.8], then 15% vol target [0.2,1.0], monthly",
        "full_path": {
            "A": records["phases"][START.isoformat()]["A"],
            "B": records["phases"][START.isoformat()]["B"],
            "B_vol15": b_full,
            "combined_vol15": full,
            "risk_parity_weight_a": records["phases"][START.isoformat()]["risk_parity_weight_a"],
            "overlay_weight": records["phases"][START.isoformat()]["overlay_weight"],
        },
        "long_phase_mean": {"A": long_a, "B_vol15": long_b, "combined_vol15": long_combined},
        "recent": {"combined_vol15": recent_combined},
        "stress_3x": stress,
        "gates": gates,
        "selected": all(gates.values()),
        "input_isolation": {
            "r40_before": before_r40, "r40_after": after_r40,
            "production_before": before_prod, "production_after": after_prod,
            "stable": before_r40 == after_r40 and before_prod == after_prod,
        },
        "decision": {
            "status": "candidate_for_concentration_audit" if all(gates.values()) else "no_candidate_passed",
            "production_write": False, "provider_call": False, "parameter_search": False,
        },
        "limits": [
            "R40 is a local research snapshot, not an official archive.",
            "The shape derives from R131 but is tested on current data; old R131 performance is not restored.",
            "A standalone R266 failed concentration gates; a pass here still requires a stock/trade concentration audit.",
            "Overlapping phases are descriptive and not independent out-of-sample evidence.",
        ],
        "phase_records": records["phases"],
        "recent_records": records["recent"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "gates": gates,
        "selected": payload["selected"],
        "full_path": {
            "A": {k: payload["full_path"]["A"][k] for k in ("total_return", "annual", "sharpe", "mdd")},
            "B_vol15": {k: b_full[k] for k in ("total_return", "annual", "sharpe", "mdd")},
            "combined_vol15": {k: full[k] for k in ("total_return", "annual", "sharpe", "mdd")},
        },
        "recent": {k: recent_combined[k] for k in ("total_return_mean", "positive_phases", "mdd_mean")},
        "stress_3x": {k: stress["combined_vol15"][k] for k in ("total_return", "annual", "sharpe", "mdd")},
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
