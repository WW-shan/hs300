from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import statistics as st
import tempfile
from contextlib import contextmanager
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
R40 = DATA / "research" / "r40_engine_data"
GOLD_CACHE = DATA / "research" / "r325_gold_assets" / "gold_etf_qfq.parquet"
PREREG = EVIDENCE / "r325_gold_ballast_preregistration.json"
PROBE = EVIDENCE / "r325_asset_probe.json"
R324_RUNNER = EVIDENCE / "run_r324_defensive_ballast.py"
OUTPUT = EVIDENCE / "r325_gold_ballast.json"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R325 result exists; refusing overwrite")
    prereg_sha = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    if sha256(PREREG) != prereg_sha:
        raise RuntimeError("R325 preregistration checksum mismatch")
    if not json.loads(PROBE.read_text(encoding="utf-8"))["all_pass"]:
        raise RuntimeError("R325 asset probe did not pass")

    r324 = module(R324_RUNNER, "r324_r325")
    r324.self_test()
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r325")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r325")
    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r325")
    r114.TARGET_VOL = 0.15
    start = r324.START
    end = r324.END
    recent_start = r324.RECENT_START
    members = r324.membership()
    r324.RUN_CACHE.mkdir(parents=True, exist_ok=True)
    before_r40 = r324.tree_hash(R40 / "kline_daily_enriched")
    before_prod = r324.tree_hash(DATA / "kline_daily_enriched")

    @contextmanager
    def service_context():
        work = Path(tempfile.mkdtemp(prefix="r325_"))
        try:
            engine_root = work / "engine_data"
            shutil.copytree(R40, engine_root, symlinks=True)
            raw_target = engine_root / "kline_daily"
            if raw_target.is_symlink() or raw_target.is_file():
                raw_target.unlink()
            elif raw_target.exists():
                shutil.rmtree(raw_target)
            shutil.copytree(DATA / "kline_daily", raw_target)
            r324.load_into_registry(DATA)
            engine = r324.StrategyEngine(
                strategy_dirs=[DATA / "strategies/ai"],
                override_loader=lambda sid: r324.strategy_config.load_override(DATA, sid),
                industry_loader=r324.industry_loader_for(DATA),
            )
            yield r324.StrategyBacktestService(
                r324.BacktestEngine(r324.KlineRepository(r324.DataStore(engine_root))), engine
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)

    service = None
    service_holder = None

    def run_sleeve(strategy_id: str, cost_factor: int) -> pl.DataFrame:
        nonlocal service, service_holder
        cache = r324.RUN_CACHE / f"{strategy_id}_{start.isoformat()}_{end.isoformat()}_{cost_factor}x.parquet"
        if cache.exists():
            return pl.read_parquet(cache).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        if service is None:
            service_holder = service_context()
            service = service_holder.__enter__()
        cfg = r324.StrategyBacktestConfig(
            strategy_id=strategy_id,
            symbols=None,
            start=start,
            end=end,
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
        with r324.patched_membership(members):
            result = service.run(cfg)
        if result.error:
            raise RuntimeError(f"{strategy_id} {cost_factor}x: {result.error}")
        frame = r324.curve_frame(result.equity_curve)
        frame.write_parquet(cache, compression="zstd")
        print(f"ran {strategy_id} {cost_factor}x: {r112._stats(frame)}", flush=True)
        return frame

    try:
        def composite(cost_factor: int) -> pl.DataFrame:
            a = run_sleeve(A_STRATEGY, cost_factor)
            b = run_sleeve(B_STRATEGY, cost_factor)
            risk, _ = r112._combine(a, b, "risk")
            return r114._vol_target(risk)["curve"]

        equity_2x = composite(2)
        equity_3x = composite(3)
    finally:
        if service_holder is not None:
            service_holder.__exit__(None, None, None)

    after_r40 = r324.tree_hash(R40 / "kline_daily_enriched")
    after_prod = r324.tree_hash(DATA / "kline_daily_enriched")

    source_r312 = json.loads((EVIDENCE / "r312_dual_sleeve_vol15.json").read_text(encoding="utf-8"))
    baseline_2x_stats = r112._stats(equity_2x)
    expected_2x = source_r312["full_path"]["combined_vol15"]
    for key in ("total_return", "annual", "sharpe", "mdd", "days"):
        tolerance = 1e-4 if key != "days" else 0
        if abs(float(baseline_2x_stats[key]) - float(expected_2x[key])) > tolerance:
            raise RuntimeError(f"R312 2x parity failed for {key}: {baseline_2x_stats[key]} vs {expected_2x[key]}")
    stress_3x_stats = r112._stats(equity_3x)
    expected_3x = source_r312["stress_3x"]["combined_vol15"]
    for key in ("total_return", "annual", "sharpe", "mdd", "days"):
        tolerance = 1e-4 if key != "days" else 0
        if abs(float(stress_3x_stats[key]) - float(expected_3x[key])) > tolerance:
            raise RuntimeError(f"R312 3x parity failed for {key}: {stress_3x_stats[key]} vs {expected_3x[key]}")

    gold = pl.read_parquet(GOLD_CACHE).select(["symbol", "date", "close"])
    primary_gold = gold.filter(pl.col("symbol") == "518880.SH").select(["date", "close"]).sort("date")
    robustness_gold = gold.filter(pl.col("symbol") == "159937.SZ").select(["date", "close"]).sort("date")
    primary = r324.build_ballast(equity_2x, primary_gold, round_trip_cost=0.001)
    robustness = r324.build_ballast(equity_2x, robustness_gold, round_trip_cost=0.001)
    stress = r324.build_ballast(equity_3x, primary_gold, round_trip_cost=0.003)

    primary_curve = primary["curve"]
    robustness_curve = robustness["curve"]
    stress_curve = stress["curve"]
    primary_values = primary_curve["value"].to_list()
    primary_daily_returns = [primary_values[i] / primary_values[i - 1] - 1.0 for i in range(1, len(primary_values))]
    primary_yearly = r315.year_returns(primary_curve)
    robustness_yearly = r315.year_returns(robustness_curve)
    baseline_yearly = r315.year_returns(equity_2x)
    gold_aligned = equity_2x.select("date").join(primary_gold, on="date", how="inner").sort("date")
    gold_curve = gold_aligned.select("date", (pl.col("close") / pl.col("close").first()).alias("value"))
    gold_values = gold_curve["value"].to_list()
    gold_daily_returns = [gold_values[i] / gold_values[i - 1] - 1.0 for i in range(1, len(gold_values))]
    equity_values = equity_2x["value"].to_list()
    equity_daily_returns = [equity_values[i] / equity_values[i - 1] - 1.0 for i in range(1, len(equity_values))]
    correlation = st.correlation(equity_daily_returns, gold_daily_returns)
    bootstrap = r315.bootstrap(primary_daily_returns)
    rolling = r315.rolling_returns(primary_daily_returns, 252)
    rolling_positive = sum(value > 0 for value in rolling) / len(rolling)
    primary_stats = r112._stats(primary_curve)
    robustness_stats = r112._stats(robustness_curve)
    stress_stats = r112._stats(stress_curve)
    recent_stats = r112._stats(r324.slice_curve(primary_curve, recent_start, end))

    gates = {
        "D1_data": primary["coverage"] >= 0.95 and robustness["coverage"] >= 0.95,
        "G1_return": primary_stats["annual"] >= 0.06,
        "G2_drawdown": primary_stats["mdd"] >= -0.18,
        "G3_sharpe": primary_stats["sharpe"] >= 0.70,
        "G4_calendar_stability": primary_yearly.get("2022", -1.0) >= 0.0 and primary_yearly.get("2023", -1.0) >= 0.0,
        "G5_bootstrap": bootstrap["positive_rate"] >= 0.80 and bootstrap["return_p05"] >= -0.12,
        "G6_rolling_year": rolling_positive >= 0.70 and min(rolling) >= -0.15,
        "G7_recent_year": recent_stats["total_return"] > 0.0,
        "G8_stress": stress_stats["total_return"] > 0.0 and stress_stats["mdd"] >= -0.25,
        "G9_incremental": (
            (primary_stats["sharpe"] >= baseline_2x_stats["sharpe"] + 0.05 or primary_stats["mdd"] >= baseline_2x_stats["mdd"] + 0.03)
            and primary_stats["annual"] >= baseline_2x_stats["annual"] - 0.01
        ),
        "G10_no_leverage": (
            primary["weight_summary"]["max_weight_sum"] <= 1.0
            and primary["weight_summary"]["min_target_weight"] >= 0.0
        ),
        "R1_robustness": (
            robustness_yearly.get("2022", -1.0) >= 0.0
            and robustness_yearly.get("2023", -1.0) >= 0.0
            and robustness_stats["mdd"] >= -0.20
        ),
    }
    all_pass = all(gates.values())
    payload = {
        "round": "R325",
        "classification": "preregistered gold defensive-asset ballast test; no parameter search",
        "preregistration_sha256": sha256(PREREG),
        "probe_sha256": sha256(PROBE),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "periods": {"full": [str(start), str(end)], "recent_year": [str(recent_start), str(end)]},
        "data": {
            "primary_gold": "518880.SH",
            "robustness_gold": "159937.SZ",
            "gold_cache_sha256": sha256(GOLD_CACHE),
            "primary_coverage": round(primary["coverage"], 6),
            "robustness_coverage": round(robustness["coverage"], 6),
        },
        "baseline_r312": {
            "same_dates_2x": baseline_2x_stats,
            "source_full_path_2x": expected_2x,
            "same_dates_stress_3x": stress_3x_stats,
            "yearly": baseline_yearly,
        },
        "defensive_asset_diagnostics": {
            "standalone_2x_dates": r112._stats(gold_curve),
            "yearly": r315.year_returns(gold_curve),
            "correlation_with_equity": round(correlation, 6),
        },
        "primary_gold": {
            "stats": primary_stats,
            "yearly": primary_yearly,
            "recent_year": recent_stats,
            "bootstrap": bootstrap,
            "rolling_252": {
                "windows": len(rolling),
                "positive_rate": round(rolling_positive, 6),
                "return_p05": round(sorted(rolling)[int(0.05 * (len(rolling) - 1))], 6),
                "return_median": round(st.median(rolling), 6),
                "return_worst": round(min(rolling), 6),
                "return_best": round(max(rolling), 6),
            },
            "drawdown_duration": r315.drawdown_duration(primary_curve),
            "turnover": {
                "total": round(primary["total_turnover"], 6),
                "annual": round(primary["annual_turnover"], 6),
            },
            "weights": primary["weight_summary"],
        },
        "robustness_gold": {
            "stats": robustness_stats,
            "yearly": robustness_yearly,
            "weights": robustness["weight_summary"],
        },
        "stress_3x": {
            "stats": stress_stats,
            "weights": stress["weight_summary"],
            "turnover": {
                "total": round(stress["total_turnover"], 6),
                "annual": round(stress["annual_turnover"], 6),
            },
        },
        "gates": gates,
        "selected": "primary_gold" if all_pass else None,
        "decision": {
            "status": "candidate_for_concentration_audit" if all_pass else "no_candidate_passed",
            "production_write": False,
            "provider_call": False,
            "parameter_search": False,
        },
        "input_isolation": {
            "r40_before": before_r40,
            "r40_after": after_r40,
            "production_before": before_prod,
            "production_after": after_prod,
            "stable": before_r40 == after_r40 and before_prod == after_prod,
        },
        "limits": [
            "Gold ETF qfq prices are provider-adjusted and are not an official PIT archive.",
            "The sample contains a strong gold bull market; a pass is not proof of a permanent gold premium.",
            "The recent-year result is a slice of the full backtest, not a fresh-start path.",
            "A pass is only a candidate for a separate concentration, liquidity and paper-execution audit.",
        ],
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
        "primary": primary_stats,
        "primary_yearly": primary_yearly,
        "bootstrap": bootstrap,
        "rolling_positive": rolling_positive,
        "recent_year": recent_stats,
        "stress_3x": stress_stats,
        "weights": primary["weight_summary"],
        "gold_diagnostics": payload["defensive_asset_diagnostics"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
