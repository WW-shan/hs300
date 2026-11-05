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
from datetime import date, timedelta
from pathlib import Path

import polars as pl

# ruff: noqa: E402

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
R40 = DATA / "research" / "r40_engine_data"
MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
TREASURY_CACHE = DATA / "research" / "r324_defensive_assets" / "treasury_etf_qfq.parquet"
RUN_CACHE = DATA / "research" / "r324_cache"
PREREG = EVIDENCE / "r324_defensive_ballast_preregistration.json"
PROBE = EVIDENCE / "r324_asset_probe.json"
OUTPUT = EVIDENCE / "r324_defensive_ballast.json"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2021, 6, 1)
END = date(2026, 10, 14)
RECENT_START = date(2025, 10, 14)
LOOKBACK = 60
TARGET_VOL = 0.15
EQUITY_WEIGHT_MIN = 0.20
EQUITY_WEIGHT_MAX = 0.80
GROSS_MIN = 0.20
GROSS_MAX = 1.00
MAIN_COST = 0.001
STRESS_COST = 0.003

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


def sha256(path: Path) -> str:
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
        digest.update(sha256(path).encode())
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


def trailing_weights(ab_returns: list[float], bond_returns: list[float]) -> tuple[float, float, float, float]:
    if len(ab_returns) < LOOKBACK:
        return 0.5, 0.5, 0.25, 0.25
    window_ab = ab_returns[-LOOKBACK:]
    window_bond = bond_returns[-LOOKBACK:]
    vol_ab = max(st.pstdev(window_ab) * math.sqrt(252.0), 1e-8)
    vol_bond = max(st.pstdev(window_bond) * math.sqrt(252.0), 1e-8)
    equity_share = (1.0 / vol_ab) / ((1.0 / vol_ab) + (1.0 / vol_bond))
    equity_share = min(EQUITY_WEIGHT_MAX, max(EQUITY_WEIGHT_MIN, equity_share))
    mean_ab = st.mean(window_ab)
    mean_bond = st.mean(window_bond)
    covariance = sum(
        (ab - mean_ab) * (bond - mean_bond)
        for ab, bond in zip(window_ab, window_bond, strict=True)
    ) / LOOKBACK
    variance = (
        equity_share**2 * vol_ab**2
        + (1.0 - equity_share) ** 2 * vol_bond**2
        + 2.0 * equity_share * (1.0 - equity_share) * covariance * 252.0
    )
    portfolio_vol = math.sqrt(max(variance, 1e-12))
    gross = min(GROSS_MAX, max(GROSS_MIN, TARGET_VOL / portfolio_vol))
    return equity_share, gross, equity_share * gross, (1.0 - equity_share) * gross


def build_ballast(ab_curve: pl.DataFrame, bond: pl.DataFrame, *, round_trip_cost: float) -> dict:
    joined = (
        ab_curve.rename({"value": "ab"})
        .join(bond.rename({"close": "bond"}), on="date", how="inner")
        .sort("date")
    )
    if joined.height < LOOKBACK + 2:
        raise RuntimeError("not enough aligned treasury observations")
    dates = joined["date"].to_list()
    ab_values = joined["ab"].to_list()
    bond_values = joined["bond"].to_list()
    ab_returns = [ab_values[i] / ab_values[i - 1] - 1.0 for i in range(1, len(ab_values))]
    bond_returns = [bond_values[i] / bond_values[i - 1] - 1.0 for i in range(1, len(bond_values))]

    equity_share, gross, equity_weight, bond_weight = trailing_weights([], [])
    cash_weight = 1.0 - gross
    equity_growth = 1.0
    bond_growth = 1.0
    value = 1.0
    initial_turnover = 0.5 * (equity_weight + bond_weight + cash_weight)
    value *= 1.0 - round_trip_cost * initial_turnover
    rows = [{"date": dates[0], "value": 1.0}]
    rebalances = [
        {
            "date": str(dates[0]),
            "equity_share": equity_share,
            "gross_exposure": gross,
            "target_equity_weight": equity_weight,
            "target_bond_weight": bond_weight,
            "target_cash_weight": cash_weight,
            "turnover": initial_turnover,
        }
    ]
    total_turnover = initial_turnover

    for i in range(1, len(dates)):
        if (dates[i].year, dates[i].month) != (dates[i - 1].year, dates[i - 1].month):
            total_before = equity_weight * equity_growth + bond_weight * bond_growth + cash_weight
            actual_equity = equity_weight * equity_growth / total_before
            actual_bond = bond_weight * bond_growth / total_before
            actual_cash = cash_weight / total_before
            history_edge = max(0, i - 1)
            equity_share, gross, target_equity, target_bond = trailing_weights(
                ab_returns[:history_edge], bond_returns[:history_edge]
            )
            target_cash = 1.0 - gross
            turnover = 0.5 * (
                abs(target_equity - actual_equity)
                + abs(target_bond - actual_bond)
                + abs(target_cash - actual_cash)
            )
            total_turnover += turnover
            value = total_before * (1.0 - round_trip_cost * turnover)
            equity_growth = 1.0
            bond_growth = 1.0
            equity_weight = target_equity
            bond_weight = target_bond
            cash_weight = target_cash
            rebalances.append(
                {
                    "date": str(dates[i]),
                    "equity_share": equity_share,
                    "gross_exposure": gross,
                    "target_equity_weight": target_equity,
                    "target_bond_weight": target_bond,
                    "target_cash_weight": target_cash,
                    "turnover": turnover,
                }
            )
        equity_growth *= 1.0 + ab_returns[i - 1]
        bond_growth *= 1.0 + bond_returns[i - 1]
        value = equity_weight * equity_growth + bond_weight * bond_growth + cash_weight
        rows.append({"date": dates[i], "value": value})

    curve = pl.DataFrame(rows)
    annual_turnover = total_turnover * 252.0 / max(len(dates) - 1, 1)
    weights = [row for row in rebalances if row["date"] != str(dates[0])]
    return {
        "curve": curve,
        "rebalances": rebalances,
        "aligned_dates": len(dates),
        "coverage": len(dates) / ab_curve.height,
        "total_turnover": total_turnover,
        "annual_turnover": annual_turnover,
        "weight_summary": {
            "equity_share_mean": round(st.mean(row["equity_share"] for row in rebalances), 6),
            "equity_share_min": round(min(row["equity_share"] for row in rebalances), 6),
            "equity_share_max": round(max(row["equity_share"] for row in rebalances), 6),
            "gross_mean": round(st.mean(row["gross_exposure"] for row in rebalances), 6),
            "gross_min": round(min(row["gross_exposure"] for row in rebalances), 6),
            "gross_max": round(max(row["gross_exposure"] for row in rebalances), 6),
            "max_weight_sum": round(
                max(row["target_equity_weight"] + row["target_bond_weight"] for row in rebalances), 6
            ),
            "min_target_weight": round(
                min(
                    min(row["target_equity_weight"], row["target_bond_weight"], row["target_cash_weight"])
                    for row in rebalances
                ),
                6,
            ),
            "rebalances": len(weights),
        },
    }


def slice_curve(curve: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    out = curve.filter((pl.col("date") >= start) & (pl.col("date") <= end)).sort("date")
    if out.height < 2:
        raise RuntimeError("slice is too short")
    return out


def standalone_stats(curve: pl.DataFrame, r112) -> dict:
    return r112._stats(curve)


def self_test() -> None:
    dates = [date(2021, 1, 1) + timedelta(days=index) for index in range(130)]
    ab = pl.DataFrame({"date": dates, "value": [1.0 + index * 0.0005 for index in range(130)]})
    bond = pl.DataFrame({"date": dates, "close": [100.0 + index * 0.02 for index in range(130)]})
    result = build_ballast(ab, bond, round_trip_cost=MAIN_COST)
    assert result["curve"].height == 130
    assert all(math.isfinite(value) and value > 0 for value in result["curve"]["value"].to_list())
    assert result["weight_summary"]["max_weight_sum"] <= 1.0 + 1e-12
    assert result["weight_summary"]["min_target_weight"] >= 0.0


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R324 result exists; refusing overwrite")
    prereg_sha = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    if sha256(PREREG) != prereg_sha:
        raise RuntimeError("R324 preregistration checksum mismatch")
    if not json.loads(PROBE.read_text(encoding="utf-8"))["all_pass"]:
        raise RuntimeError("R324 asset probe did not pass")
    self_test()

    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r324")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r324")
    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r324")
    r114.TARGET_VOL = TARGET_VOL
    members = membership()
    RUN_CACHE.mkdir(parents=True, exist_ok=True)
    before_r40 = tree_hash(R40 / "kline_daily_enriched")
    before_prod = tree_hash(DATA / "kline_daily_enriched")
    work = Path(tempfile.mkdtemp(prefix="r324_"))
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

        def run_sleeve(strategy_id: str, cost_factor: int) -> pl.DataFrame:
            cache = RUN_CACHE / f"{strategy_id}_{START.isoformat()}_{END.isoformat()}_{cost_factor}x.parquet"
            if cache.exists():
                return pl.read_parquet(cache).with_columns(pl.col("date").cast(pl.Date)).sort("date")
            cfg = StrategyBacktestConfig(
                strategy_id=strategy_id,
                symbols=None,
                start=START,
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
                raise RuntimeError(f"{strategy_id} {cost_factor}x: {result.error}")
            frame = curve_frame(result.equity_curve)
            frame.write_parquet(cache, compression="zstd")
            print(f"ran {strategy_id} {cost_factor}x: {r112._stats(frame)}", flush=True)
            return frame

        def composite(cost_factor: int) -> pl.DataFrame:
            a = run_sleeve(A_STRATEGY, cost_factor)
            b = run_sleeve(B_STRATEGY, cost_factor)
            risk, _ = r112._combine(a, b, "risk")
            return r114._vol_target(risk)["curve"]

        equity_2x = composite(2)
        equity_3x = composite(3)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = tree_hash(R40 / "kline_daily_enriched")
    after_prod = tree_hash(DATA / "kline_daily_enriched")

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

    treasury = pl.read_parquet(TREASURY_CACHE).select(["symbol", "date", "close"])
    primary_bond = treasury.filter(pl.col("symbol") == "511260.SH").select(["date", "close"]).sort("date")
    robustness_bond = treasury.filter(pl.col("symbol") == "511010.SH").select(["date", "close"]).sort("date")
    primary = build_ballast(equity_2x, primary_bond, round_trip_cost=MAIN_COST)
    robustness = build_ballast(equity_2x, robustness_bond, round_trip_cost=MAIN_COST)
    stress = build_ballast(equity_3x, primary_bond, round_trip_cost=STRESS_COST)

    primary_curve = primary["curve"]
    robustness_curve = robustness["curve"]
    stress_curve = stress["curve"]
    primary_returns = [float(row["value"]) for row in primary_curve.iter_rows(named=True)]
    primary_daily_returns = [
        primary_returns[index] / primary_returns[index - 1] - 1.0
        for index in range(1, len(primary_returns))
    ]
    robustness_yearly = r315.year_returns(robustness_curve)
    primary_yearly = r315.year_returns(primary_curve)
    baseline_yearly = r315.year_returns(equity_2x)
    bond_aligned = (
        equity_2x.select("date")
        .join(primary_bond, on="date", how="inner")
        .sort("date")
    )
    bond_curve = bond_aligned.select(
        "date",
        (pl.col("close") / pl.col("close").first()).alias("value"),
    )
    bond_returns = bond_curve["value"].to_list()
    bond_daily_returns = [bond_returns[index] / bond_returns[index - 1] - 1.0 for index in range(1, len(bond_returns))]
    equity_values = equity_2x["value"].to_list()
    equity_daily_returns = [equity_values[index] / equity_values[index - 1] - 1.0 for index in range(1, len(equity_values))]
    correlation = st.correlation(equity_daily_returns, bond_daily_returns) if len(equity_daily_returns) > 2 else 0.0
    bootstrap = r315.bootstrap(primary_daily_returns)
    rolling = r315.rolling_returns(primary_daily_returns, 252)
    rolling_positive = sum(value > 0 for value in rolling) / len(rolling)
    recent_stats = r112._stats(slice_curve(primary_curve, RECENT_START, END))
    primary_stats = r112._stats(primary_curve)
    robustness_stats = r112._stats(robustness_curve)
    stress_stats = r112._stats(stress_curve)

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
        "round": "R324",
        "classification": "preregistered defensive-asset ballast test; no parameter search",
        "question": json.loads(PREREG.read_text(encoding="utf-8"))["question"],
        "preregistration_sha256": sha256(PREREG),
        "probe_sha256": sha256(PROBE),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "periods": {"full": [str(START), str(END)], "recent_year": [str(RECENT_START), str(END)]},
        "data": {
            "primary_bond": "511260.SH",
            "robustness_bond": "511010.SH",
            "treasury_cache_sha256": sha256(TREASURY_CACHE),
            "primary_coverage": round(primary["coverage"], 6),
            "robustness_coverage": round(robustness["coverage"], 6),
        },
        "portfolio_rule": {
            "lookback_days": LOOKBACK,
            "target_annual_volatility": TARGET_VOL,
            "equity_weight_bounds": [EQUITY_WEIGHT_MIN, EQUITY_WEIGHT_MAX],
            "gross_exposure_bounds": [GROSS_MIN, GROSS_MAX],
            "main_round_trip_cost": MAIN_COST,
            "stress_round_trip_cost": STRESS_COST,
        },
        "baseline_r312": {
            "same_dates_2x": baseline_2x_stats,
            "source_full_path_2x": expected_2x,
            "same_dates_stress_3x": stress_3x_stats,
            "yearly": baseline_yearly,
        },
        "defensive_asset_diagnostics": {
            "standalone_2x_dates": standalone_stats(bond_curve, r112),
            "yearly": r315.year_returns(bond_curve),
            "correlation_with_equity": round(correlation, 6),
        },
        "primary_10y": {
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
        "robustness_5y": {
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
        "selected": "primary_10y" if all_pass else None,
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
            "Treasury ETF qfq prices are provider-adjusted and are not an official PIT archive.",
            "The equity composite still uses R40 local research data, current industry snapshots and current factor versions.",
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
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
