from __future__ import annotations

import hashlib
import importlib.util
import json
import statistics as st
import sys
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
ASSET_CACHE = DATA / "research/r331_defensive_assets"
STOCK_CACHE = DATA / "research/r344_cache"
PREREG = EVIDENCE / "r349_defensive_dual_momentum_preregistration.json"
PREFLIGHT = EVIDENCE / "r349_defensive_dual_momentum_preflight.json"
PREFLIGHT_ERRATUM = EVIDENCE / "r349_preflight_selection_pair_erratum.json"
R344_RESULT = EVIDENCE / "r344_extended_window.json"
ASSET_MANIFEST = EVIDENCE / "r331_asset_manifest.json"
OUTPUT = EVIDENCE / "r349_defensive_dual_momentum.json"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
C_STRATEGY = "ai_hs300_quality_momentum_broad_h60"
STRATEGIES = (A_STRATEGY, B_STRATEGY, C_STRATEGY)
POSITIONS = {A_STRATEGY: 10, B_STRATEGY: 10, C_STRATEGY: 50}
C_WEIGHT_CAP = 0.10
CAP_GRID = [0.05, 0.10, 0.15, 0.20]
START = date(2022, 5, 5)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
PHASES = [START, date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), date(2025, 11, 5), date(2026, 1, 5)]
LOOKBACKS = [40, 60, 90]
TARGETS = [0.12, 0.15, 0.18]
OVERLAY_COST = {2: 0.001, 3: 0.0015, "3c": 0.003, 4: 0.002, 5: 0.0025}
ASSET_FILES = {
    "gold": "gold_etf_qfq.parquet",
    "treasury": "treasury_etf_qfq.parquet",
    "dividend": "dividend_etf_qfq.parquet",
}
ASSET_NAMES = ("gold", "treasury", "dividend")
sys.path.insert(0, str(ROOT / "backend"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def asset_frame(kind: str, symbol: str) -> pl.DataFrame:
    frame = pl.read_parquet(ASSET_CACHE / ASSET_FILES[kind])
    return frame.filter(pl.col("symbol") == symbol).select(["date", "close"]).sort("date")


def _three_sleeve_targets(dates: list[date], padded_returns: list[list[float]], c_weight_cap: float) -> list[tuple[date, tuple[float, float, float]]]:
    targets = []
    current = (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)
    previous_month = None
    for index, day in enumerate(dates):
        month = (day.year, day.month)
        if index == 0 or month != previous_month:
            if index < 60:
                weights = [1.0 / 3.0] * 3
            else:
                inv = [1.0 / max(st.pstdev(values[index - 60:index]), 1e-6) for values in padded_returns]
                total = sum(inv)
                weights = [value / total for value in inv]
                for _ in range(10):
                    clipped = [min(0.60, max(0.20, value)) for value in weights]
                    clipped_total = sum(clipped)
                    weights = [value / clipped_total for value in clipped]
                    if all(0.20 - 1e-12 <= value <= 0.60 + 1e-12 for value in weights):
                        break
            if weights[2] > c_weight_cap:
                residual = 1.0 - c_weight_cap
                ab_total = weights[0] + weights[1]
                weights = [residual * weights[0] / ab_total, residual * weights[1] / ab_total, c_weight_cap]
            current = tuple(weights)
            targets.append((day, current))
            previous_month = month
    return targets


def _combine_three(curves: tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame], round_trip_cost: float, c_weight_cap: float) -> tuple[pl.DataFrame, list[dict]]:
    joined = (
        curves[0].rename({"value": "a"})
        .join(curves[1].rename({"value": "b"}), on="date", how="inner")
        .join(curves[2].rename({"value": "c"}), on="date", how="inner")
        .sort("date")
    )
    dates = joined["date"].to_list()
    values = {key: joined[key].to_list() for key in ("a", "b", "c")}
    returns = {
        key: [values[key][index] / values[key][index - 1] - 1.0 for index in range(1, len(dates))]
        for key in ("a", "b", "c")
    }
    padded = [[0.0, *returns[key]] for key in ("a", "b", "c")]
    targets = _three_sleeve_targets(dates, padded, c_weight_cap)
    target_map = {day: weights for day, weights in targets}
    target = target_map[dates[0]]
    sleeve_values = [1.0, 1.0, 1.0]
    portfolio = 1.0
    rows = [{"date": dates[0], "value": portfolio}]
    weight_records = [{"date": dates[0], "weight_a": target[0], "weight_b": target[1], "weight_c": target[2]}]
    for index in range(1, len(dates)):
        day = dates[index]
        daily = [returns[key][index - 1] for key in ("a", "b", "c")]
        sleeve_values = [sleeve_values[i] * (1.0 + daily[i]) for i in range(3)]
        portfolio *= 1.0 + sum(target[i] * daily[i] for i in range(3))
        next_month = index + 1 == len(dates) or (dates[index + 1].year, dates[index + 1].month) != (day.year, day.month)
        if next_month:
            gross = sum(target[i] * sleeve_values[i] for i in range(3))
            actual = [target[i] * sleeve_values[i] / gross if gross > 0 else target[i] for i in range(3)]
            next_target = target_map[dates[index + 1]] if index + 1 < len(dates) else target
            turnover = 0.5 * sum(abs(next_target[i] - actual[i]) for i in range(3))
            portfolio *= 1.0 - turnover * round_trip_cost
            sleeve_values = [1.0, 1.0, 1.0]
            target = next_target
        rows.append({"date": day, "value": portfolio})
        if next_month and index + 1 < len(dates):
            weight_records.append({"date": dates[index + 1], "weight_a": target[0], "weight_b": target[1], "weight_c": target[2]})
    return pl.DataFrame(rows), weight_records


def _corr(left: pl.DataFrame, right: pl.DataFrame) -> float:
    joined = left.rename({"value": "left"}).join(right.rename({"value": "right"}), on="date", how="inner").sort("date")
    lv = joined["left"].to_list()
    rv = joined["right"].to_list()
    lr = [lv[index] / lv[index - 1] - 1.0 for index in range(1, len(lv))]
    rr = [rv[index] / rv[index - 1] - 1.0 for index in range(1, len(rv))]
    if len(lr) < 20 or st.pstdev(lr) == 0 or st.pstdev(rr) == 0:
        return 1.0
    return st.correlation(lr, rr)


def gold_trend_proxy(gold: pl.DataFrame, treasury: pl.DataFrame, dividend: pl.DataFrame, round_trip_cost: float) -> pl.DataFrame:
    joined = (
        gold.rename({"close": "gold"})
        .join(treasury.rename({"close": "treasury"}), on="date", how="inner")
        .join(dividend.rename({"close": "dividend"}), on="date", how="inner")
        .sort("date")
    )
    dates = joined["date"].to_list()
    gold_values = joined["gold"].to_list()
    treasury_values = joined["treasury"].to_list()
    dividend_values = joined["dividend"].to_list()
    gold_on = [
        True if index < 199 else gold_values[index] > sum(gold_values[index - 199:index + 1]) / 200.0
        for index in range(len(gold_values))
    ]
    prices = [100.0]
    composition_on = gold_on[0]
    for index in range(len(dates) - 1):
        switched = False
        if index > 0 and (dates[index].year, dates[index].month) != (dates[index - 1].year, dates[index - 1].month) and gold_on[index] != composition_on:
            composition_on = gold_on[index]
            switched = True
        gold_return = gold_values[index + 1] / gold_values[index] - 1.0
        treasury_return = treasury_values[index + 1] / treasury_values[index] - 1.0
        dividend_return = dividend_values[index + 1] / dividend_values[index] - 1.0
        daily_return = gold_return if composition_on else 0.5 * (treasury_return + dividend_return)
        if switched:
            daily_return -= round_trip_cost / 3.0
        prices.append(prices[-1] * (1.0 + daily_return))
    return pl.DataFrame({"date": dates, "close": prices})


def dual_momentum_proxy(
    gold: pl.DataFrame,
    treasury: pl.DataFrame,
    dividend: pl.DataFrame,
    round_trip_cost: float,
) -> dict:
    joined = (
        gold.rename({"close": "gold"})
        .join(treasury.rename({"close": "treasury"}), on="date", how="inner")
        .join(dividend.rename({"close": "dividend"}), on="date", how="inner")
        .sort("date")
    )
    dates = joined["date"].to_list()
    values = {name: joined[name].to_list() for name in ASSET_NAMES}
    weights = {name: 1.0 / 3.0 for name in ASSET_NAMES}
    schedule = [{
        "date": dates[0],
        "selected": list(ASSET_NAMES),
        "momentum": {},
        "weights": dict(weights),
        "turnover": 0.0,
    }]
    weights_by_date = {dates[0]: dict(weights)}
    prices = [100.0]
    contribution_rows = []
    for index in range(len(dates) - 1):
        cost = 0.0
        day = dates[index]
        if index > 0 and (day.year, day.month) != (dates[index - 1].year, dates[index - 1].month) and index >= 60:
            momentum = {
                name: values[name][index] / values[name][index - 60] - 1.0
                for name in ASSET_NAMES
            }
            ranked = sorted(ASSET_NAMES, key=lambda name: (-momentum[name], ASSET_NAMES.index(name)))
            selected = ranked[:2]
            target = {name: 0.5 if name in selected else 0.0 for name in ASSET_NAMES}
            turnover = 0.5 * sum(abs(target[name] - weights[name]) for name in ASSET_NAMES)
            cost = round_trip_cost * turnover
            weights = target
            schedule.append({
                "date": day,
                "selected": selected,
                "momentum": momentum,
                "weights": dict(weights),
                "turnover": turnover,
            })
        weights_by_date[day] = dict(weights)
        returns = {
            name: values[name][index + 1] / values[name][index] - 1.0
            for name in ASSET_NAMES
        }
        before = prices[-1]
        gross_return = sum(weights[name] * returns[name] for name in ASSET_NAMES)
        contribution_rows.append({
            "date": dates[index + 1],
            **{name: before * weights[name] * returns[name] for name in ASSET_NAMES},
        })
        prices.append(before * (1.0 + gross_return - cost))
    weights_by_date[dates[-1]] = dict(weights)
    return {
        "curve": pl.DataFrame({"date": dates, "close": prices}),
        "schedule": schedule,
        "weights_by_date": weights_by_date,
        "contribution_rows": contribution_rows,
    }


def proxy_selection_summary(proxy: dict, start: date, end: date) -> dict:
    rows = [row for row in proxy["schedule"] if start <= row["date"] <= end and row["momentum"]]
    counts = {name: sum(name in row["selected"] for row in rows) for name in ASSET_NAMES}
    pairs = {}
    for row in rows:
        key = "+".join(sorted(row["selected"]))
        pairs[key] = pairs.get(key, 0) + 1
    return {
        "rebalances": len(rows),
        "selection_counts": counts,
        "selection_frequency": {name: counts[name] / len(rows) if rows else None for name in ASSET_NAMES},
        "selection_pairs": dict(sorted(pairs.items())),
        "mean_turnover": sum(row["turnover"] for row in rows) / len(rows) if rows else None,
        "max_turnover": max((row["turnover"] for row in rows), default=None),
    }


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R349 result exists; refusing overwrite")
    for path in (PREREG, PREFLIGHT, PREFLIGHT_ERRATUM, R344_RESULT, ASSET_MANIFEST):
        expected = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if sha256(path) != expected:
            raise RuntimeError(f"checksum mismatch: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    preflight = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    erratum = json.loads(PREFLIGHT_ERRATUM.read_text(encoding="utf-8"))
    manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    r344_result = json.loads(R344_RESULT.read_text(encoding="utf-8"))
    if prereg["round"] != "R349" or prereg["status"] != "pre_registered_before_r349_portfolio_backtest":
        raise RuntimeError("R349 preregistration status is invalid")
    if not preflight["all_checks"] or not preflight["no_return_or_backtest_evaluated"]:
        raise RuntimeError("R349 preflight did not pass")
    if erratum["status"] != "reporting_erratum_no_returns" or not erratum["no_return_or_backtest_evaluated"]:
        raise RuntimeError("R349 preflight erratum is invalid")
    if r344_result["all_pass"] or r344_result["selected"] is not None:
        raise RuntimeError("R349 requires R344 to remain a no-candidate control")
    if not manifest["all_pass"]:
        raise RuntimeError("asset manifest did not pass")
    if sha256(Path(__file__).resolve()) != prereg["artifact_sha256"]["runner"]:
        raise RuntimeError("R349 runner hash changed after preregistration")
    if sha256(EVIDENCE / "run_r349_defensive_dual_momentum_preflight.py") != prereg["artifact_sha256"]["preflight_runner"]:
        raise RuntimeError("R349 preflight runner hash changed after preregistration")
    strategy_files = {
        "strategy_A": DATA / "strategies/ai" / f"{A_STRATEGY}.py",
        "strategy_B": DATA / "strategies/ai" / f"{B_STRATEGY}.py",
        "strategy_C": DATA / "strategies/ai" / f"{C_STRATEGY}.py",
    }
    if any(sha256(path) != prereg["artifact_sha256"][key] for key, path in strategy_files.items()):
        raise RuntimeError("R349 stock strategy source changed after preregistration")

    r331 = module(EVIDENCE / "run_r331_three_asset_bucket.py", "r331_r349")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r349")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r349")
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r349")
    r313 = module(EVIDENCE / "run_r313_concentration_audit.py", "r313_r349")
    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r349")
    r326 = module(EVIDENCE / "run_r326_gold_concentration.py", "r326_r349")
    r331.self_test()
    r114.TARGET_VOL = 0.15
    industry_frame = r313.load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}

    cache_paths: dict[str, Path] = {}
    full = {}
    phase_curves = {}
    full_trades = {}
    for factor in (2, 3, 4, 5):
        full[factor] = {}
        for strategy_id in STRATEGIES:
            path = STOCK_CACHE / f"{strategy_id}_{START}_{factor}x_p{POSITIONS[strategy_id]}.parquet"
            cache_paths[f"{strategy_id}_{START}_{factor}x"] = path
            full[factor][strategy_id] = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
            if factor in (2, 5):
                trade_path = path.with_suffix(".trades.json")
                cache_paths[f"{strategy_id}_{START}_{factor}x_trades"] = trade_path
                full_trades[(factor, strategy_id)] = json.loads(trade_path.read_text(encoding="utf-8"))
    for phase in PHASES:
        phase_curves[phase] = {}
        for strategy_id in STRATEGIES:
            path = STOCK_CACHE / f"{strategy_id}_{phase}_2x_p{POSITIONS[strategy_id]}.parquet"
            cache_paths[f"{strategy_id}_{phase}_2x"] = path
            phase_curves[phase][strategy_id] = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
    cache_before = {key: sha256(path) for key, path in cache_paths.items()}
    if len(cache_before) != 33:
        raise RuntimeError(f"unexpected R344 cache manifest size: {len(cache_before)}")

    risk = {}
    rp = {}
    equity = {}
    for factor in (2, 3, 4, 5):
        risk[factor], rp[factor] = _combine_three(
            (full[factor][A_STRATEGY], full[factor][B_STRATEGY], full[factor][C_STRATEGY]),
            r112.ROUND_TRIP_COST,
            C_WEIGHT_CAP,
        )
        equity[factor] = r114._vol_target(risk[factor])["curve"]

    def build_fixed(equity_curve: pl.DataFrame, lookback: int, overlay_target: float, overlay_cost: float, symbols: dict) -> dict:
        previous_lookback = r331.LOOKBACK
        previous_target = r331.TARGET_VOL
        previous_cap = r331.EQUITY_WEIGHT_MAX
        try:
            r331.LOOKBACK = lookback
            r331.TARGET_VOL = overlay_target
            r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
            gold_asset = asset_frame("gold", symbols["gold"])
            treasury_asset = asset_frame("treasury", symbols["treasury"])
            dividend_asset = asset_frame("dividend", symbols["dividend"])
            return r331.build_three_asset_bucket(
                equity_curve,
                gold_trend_proxy(gold_asset, treasury_asset, dividend_asset, overlay_cost),
                treasury_asset,
                dividend_asset,
                round_trip_cost=overlay_cost,
            )
        finally:
            r331.LOOKBACK = previous_lookback
            r331.TARGET_VOL = previous_target
            r331.EQUITY_WEIGHT_MAX = previous_cap

    def augment_bucket(bucket: dict, proxy: dict, equity_curve: pl.DataFrame) -> dict:
        schedule = proxy["weights_by_date"]
        actual_rows = []
        for row in bucket["rebalances"]:
            day = r326.parse_day(row["date"])
            if day not in schedule:
                raise RuntimeError(f"defensive schedule missing bucket rebalance date: {day}")
            defense_weight = row["target_gold_weight"] + row["target_treasury_weight"] + row["target_dividend_weight"]
            actual_rows.append({
                "date": day,
                "gold": defense_weight * schedule[day]["gold"],
                "treasury": defense_weight * schedule[day]["treasury"],
                "dividend": defense_weight * schedule[day]["dividend"],
            })
        actual_summary = {}
        for name in ASSET_NAMES:
            values = [row[name] for row in actual_rows]
            actual_summary[f"{name}_weight_mean"] = round(st.mean(values), 6)
            actual_summary[f"{name}_weight_max"] = round(max(values), 6)
        bucket["actual_weight_summary"] = actual_summary
        start = equity_curve["date"].min()
        raw_contrib = {
            name: sum(float(row[name]) for row in proxy["contribution_rows"] if start <= row["date"] <= END)
            for name in ASSET_NAMES
        }
        bucket_defense_total = sum(bucket["contributions"][name] for name in ASSET_NAMES)
        raw_total = sum(raw_contrib.values())
        if abs(raw_total) > 1e-12:
            bucket["actual_contributions"] = {
                name: bucket_defense_total * raw_contrib[name] / raw_total
                for name in ASSET_NAMES
            }
        else:
            bucket["actual_contributions"] = {name: bucket_defense_total / 3.0 for name in ASSET_NAMES}
        bucket["defense_selection_summary"] = proxy_selection_summary(proxy, start, END)
        return bucket

    def build_candidate(equity_curve: pl.DataFrame, lookback: int, overlay_target: float, overlay_cost: float, symbols: dict) -> dict:
        previous_lookback = r331.LOOKBACK
        previous_target = r331.TARGET_VOL
        previous_cap = r331.EQUITY_WEIGHT_MAX
        try:
            r331.LOOKBACK = lookback
            r331.TARGET_VOL = overlay_target
            r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
            gold_asset = asset_frame("gold", symbols["gold"])
            treasury_asset = asset_frame("treasury", symbols["treasury"])
            dividend_asset = asset_frame("dividend", symbols["dividend"])
            proxy = dual_momentum_proxy(gold_asset, treasury_asset, dividend_asset, overlay_cost)
            synthetic = proxy["curve"]
            bucket = r331.build_three_asset_bucket(
                equity_curve,
                synthetic,
                synthetic,
                synthetic,
                round_trip_cost=overlay_cost,
            )
            return augment_bucket(bucket, proxy, equity_curve)
        finally:
            r331.LOOKBACK = previous_lookback
            r331.TARGET_VOL = previous_target
            r331.EQUITY_WEIGHT_MAX = previous_cap

    baseline_center = build_fixed(equity[2], 60, 0.15, OVERLAY_COST[2], r331.PRIMARY)
    baseline_3c = build_fixed(equity[3], 60, 0.15, OVERLAY_COST["3c"], r331.PRIMARY)
    baseline_4x = build_fixed(equity[4], 60, 0.15, OVERLAY_COST[4], r331.PRIMARY)
    baseline_5x = build_fixed(equity[5], 60, 0.15, OVERLAY_COST[5], r331.PRIMARY)

    def close_stats(actual: dict, expected: dict) -> bool:
        return all(abs(float(actual[key]) - float(expected[key])) <= 2e-6 for key in ("total_return", "annual", "sharpe", "mdd"))

    baseline_validation = {
        "center_2x": close_stats(r112._stats(baseline_center["curve"]), r344_result["center_2x"]["stats"]),
        "conservative_3x": close_stats(r112._stats(baseline_3c["curve"]), r344_result["center_3x_conservative"]["stats"]),
        "4x": close_stats(r112._stats(baseline_4x["curve"]), r344_result["cost_metrics"]["4x"]["stats"]),
        "5x": close_stats(r112._stats(baseline_5x["curve"]), r344_result["cost_metrics"]["5x"]["stats"]),
    }
    if not all(baseline_validation.values()):
        raise RuntimeError(f"R349 cache does not reconstruct the frozen R344 baseline: {baseline_validation}")

    core = {factor: build_candidate(equity[factor], 60, 0.15, OVERLAY_COST[factor], r331.PRIMARY) for factor in (2, 3, 4, 5)}
    core_conservative_3x = build_candidate(equity[3], 60, 0.15, OVERLAY_COST["3c"], r331.PRIMARY)

    def concentration(factor: int) -> tuple[dict, dict, int]:
        trades = [trade for strategy_id in STRATEGIES for trade in full_trades[(factor, strategy_id)]]
        curve_dates = risk[factor]["date"].to_list()
        scaled = []
        for trade in trades:
            entry = r326.parse_day(trade["entry_date"])
            raw_exit = trade.get("exit_date")
            exit_ = r326.parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
            days = [day for day in curve_dates if entry <= day <= exit_] or [entry]
            scales = []
            for day in days:
                equity_weight = r326.weight_at(core[factor]["rebalances"], day, "target_equity_weight")
                sleeve_share = r326.weight_at(rp[factor], day, f"weight_{trade['sleeve'].lower()}")
                scales.append(equity_weight * sleeve_share)
            scaled.append({**trade, "weight_scale": round(sum(scales) / len(scales), 6), "pnl_amount": round(trade["pnl_amount"] * sum(scales) / len(scales), 6)})
        full_conc = r239._aggregate(scaled, industry)
        recent_conc = r239._aggregate([trade for trade in scaled if r326.parse_day(trade["entry_date"]) >= RECENT_START], industry)
        return full_conc, recent_conc, sum(1 for row in recent_conc["per_symbol"] if row["pnl_amount"] > 0)

    def concentration_gates(factor: int) -> tuple[dict, dict]:
        full_conc, _recent_conc, recent_positive = concentration(factor)
        contributions = {
            "equity": core[factor]["contributions"]["equity"],
            **core[factor]["actual_contributions"],
        }
        total = sum(max(value, 0.0) for value in contributions.values())
        shares = {key: (value / total if total > 0 else None) for key, value in contributions.items()}
        weights = {**core[factor]["weight_summary"], **core[factor]["actual_weight_summary"]}
        gates = {
            "C1_stock_breadth": full_conc["distinct_symbols"] >= 80,
            "C2_top1": full_conc["total_pnl_amount"] > 0 and full_conc["top1_net_share"] <= 0.25,
            "C3_top3": full_conc["total_pnl_amount"] > 0 and full_conc["top3_net_share"] <= 0.55,
            "C4_top5": full_conc["total_pnl_amount"] > 0 and full_conc["top5_net_share"] <= 0.75,
            "C5_recent_breadth": recent_positive >= 8,
            "C6_industry": full_conc["top_industry_gross_share"] is not None and full_conc["top_industry_gross_share"] <= 0.45,
            "C7_loss": full_conc["max_symbol_loss_to_gross"] is not None and full_conc["max_symbol_loss_to_gross"] <= 0.20,
            "C8_asset_contribution": all(value is not None and value <= 0.50 for value in shares.values()),
            "C9_asset_weight": (
                weights["gold_weight_mean"] <= 0.40 and weights["gold_weight_max"] <= 0.50
                and weights["treasury_weight_mean"] <= 0.40 and weights["treasury_weight_max"] <= 0.50
                and weights["dividend_weight_mean"] <= 0.40 and weights["dividend_weight_max"] <= 0.50
            ),
            "C10_liquidity": manifest["gates"]["D3_primary_liquidity"],
            "C11_turnover": core[factor]["annual_turnover"] <= 1.5,
            "C12_weights": weights["max_weight_sum"] <= 1.0 and weights["min_target_weight"] >= 0.0,
        }
        summary = {
            "concentration": {key: full_conc.get(key) for key in ("distinct_symbols", "top1_net_share", "top3_net_share", "top5_net_share", "top_industry_gross_share", "max_symbol_loss_to_gross")},
            "recent_positive_symbols": recent_positive,
            "asset_contribution_shares": {key: round(value, 6) if value is not None else None for key, value in shares.items()},
            "weights": weights,
        }
        return gates, summary

    def metrics(curve: pl.DataFrame, stress_curve: pl.DataFrame | None = None) -> dict:
        values = curve["value"].to_list()
        returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
        bootstrap = r315.bootstrap(returns)
        rolling = r315.rolling_returns(returns, 252)
        output = {
            "stats": r112._stats(curve),
            "yearly": r315.year_returns(curve),
            "recent": r112._stats(curve.filter(pl.col("date").is_between(RECENT_START, END)).sort("date")),
            "bootstrap": bootstrap,
            "rolling": {
                "positive_rate": round(sum(value > 0 for value in rolling) / len(rolling), 6),
                "return_p05": round(sorted(rolling)[int(0.05 * (len(rolling) - 1))], 6),
                "return_worst": round(min(rolling), 6),
            },
        }
        if stress_curve is not None:
            output["stress"] = r112._stats(stress_curve)
        return output

    center_metrics = metrics(core[2]["curve"], core[3]["curve"])
    extension_2022 = r112._stats(core[2]["curve"].filter(pl.col("date").is_between(START, date(2022, 12, 31))).sort("date"))
    prewindow = r112._stats(core[2]["curve"].filter(pl.col("date").is_between(START, date(2023, 5, 3))).sort("date"))
    extension_gates = {
        "T1_2022_partial_return": extension_2022["total_return"] > 0,
        "T2_2022_partial_mdd": extension_2022["mdd"] >= -0.15,
        "T3_2023_full_year": center_metrics["yearly"].get("2023", -1.0) >= 0,
        "T4_prewindow_return": prewindow["total_return"] > 0,
        "T5_prewindow_sharpe": prewindow["sharpe"] >= 0.30,
        "T6_prewindow_mdd": prewindow["mdd"] >= -0.15,
    }
    conservative_metrics = metrics(core_conservative_3x["curve"])
    cost_metrics = {"4x": metrics(core[4]["curve"]), "5x": metrics(core[5]["curve"])}
    c2_gates, c2_summary = concentration_gates(2)
    c5_gates, c5_summary = concentration_gates(5)
    correlations = {
        "AB": round(_corr(full[2][A_STRATEGY], full[2][B_STRATEGY]), 6),
        "AC": round(_corr(full[2][A_STRATEGY], full[2][C_STRATEGY]), 6),
        "BC": round(_corr(full[2][B_STRATEGY], full[2][C_STRATEGY]), 6),
    }
    mean_pairwise_correlation = round(sum(correlations.values()) / 3.0, 6)
    sleeve_weights = {
        key: round(sum(float(row[f"weight_{key.lower()}"]) for row in rp[2]) / len(rp[2]), 6)
        for key in ("A", "B", "C")
    }
    r344_control_stats = r344_result["center_2x"]["stats"]
    performance_gates = {
        "G1_return": center_metrics["stats"]["annual"] >= 0.12,
        "G2_drawdown": center_metrics["stats"]["mdd"] >= -0.15,
        "G3_sharpe": center_metrics["stats"]["sharpe"] >= 1.25,
        "G4_calendar": center_metrics["yearly"].get("2024", -1.0) >= 0.0 and center_metrics["yearly"].get("2025", -1.0) >= 0.0,
        "G5_recent": center_metrics["recent"]["total_return"] > 0,
        "G6_bootstrap": center_metrics["bootstrap"]["positive_rate"] >= 0.80 and center_metrics["bootstrap"]["return_p05"] >= -0.12,
        "G7_rolling": center_metrics["rolling"]["positive_rate"] >= 0.70 and center_metrics["rolling"]["return_worst"] >= -0.15,
        "G8_stress": conservative_metrics["stats"]["total_return"] > 0 and conservative_metrics["stats"]["mdd"] >= -0.25,
        "G9_annual": center_metrics["stats"]["annual"] >= r344_control_stats["annual"] - 0.01,
        "G9_sharpe": center_metrics["stats"]["sharpe"] >= r344_control_stats["sharpe"] - 0.10,
        "G9_mdd": center_metrics["stats"]["mdd"] >= r344_control_stats["mdd"] - 0.005,
        "G10_no_leverage": core[2]["weight_summary"]["max_weight_sum"] <= 1.0 and core[2]["weight_summary"]["min_target_weight"] >= 0.0,
        "G11_cap": sleeve_weights["C"] <= C_WEIGHT_CAP + 0.005,
    }
    robust = {}
    for name, symbols in r331.ROBUSTNESS.items():
        bucket = build_candidate(equity[2], 60, 0.15, OVERLAY_COST[2], symbols)
        robust[name] = {"stats": r112._stats(bucket["curve"]), "yearly": r315.year_returns(bucket["curve"])}
    robust_gates = {
        name: (
            item["stats"]["annual"] >= 0.05
            and item["stats"]["mdd"] >= -0.20
            and item["yearly"].get("2024", -1.0) >= 0.0
            and item["yearly"].get("2025", -1.0) >= 0.0
        )
        for name, item in robust.items()
    }
    fresh = {}
    for phase in PHASES:
        phase_risk, _ = _combine_three(
            (phase_curves[phase][A_STRATEGY], phase_curves[phase][B_STRATEGY], phase_curves[phase][C_STRATEGY]),
            r112.ROUND_TRIP_COST,
            C_WEIGHT_CAP,
        )
        phase_equity = r114._vol_target(phase_risk)["curve"]
        bucket = build_candidate(phase_equity, 60, 0.15, OVERLAY_COST[2], r331.PRIMARY)
        fresh[str(phase)] = {"stats": r112._stats(bucket["curve"]), "yearly": r315.year_returns(bucket["curve"])}
    fresh_gate = sum(1 for item in fresh.values() if item["stats"]["total_return"] > 0) >= 5 and all(item["stats"]["mdd"] >= -0.18 for item in fresh.values())
    fresh_extension_gate = fresh[str(START)]["stats"]["total_return"] > 0

    def concentration_for_variant(allocation: dict, rp_records: list[dict], equity_curve: pl.DataFrame) -> tuple[dict, dict, int]:
        trades = [trade for strategy_id in STRATEGIES for trade in full_trades[(2, strategy_id)]]
        curve_dates = equity_curve["date"].to_list()
        scaled = []
        for trade in trades:
            entry = r326.parse_day(trade["entry_date"])
            raw_exit = trade.get("exit_date")
            exit_ = r326.parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
            days = [day for day in curve_dates if entry <= day <= exit_] or [entry]
            scales = []
            for day in days:
                eqw = r326.weight_at(allocation["rebalances"], day, "target_equity_weight")
                sleeve = r326.weight_at(rp_records, day, f"weight_{trade['sleeve'].lower()}")
                scales.append(eqw * sleeve)
            scaled.append({**trade, "pnl_amount": round(trade["pnl_amount"] * sum(scales) / len(scales), 6)})
        full_conc = r239._aggregate(scaled, industry)
        recent_conc = r239._aggregate([trade for trade in scaled if r326.parse_day(trade["entry_date"]) >= RECENT_START], industry)
        return full_conc, recent_conc, sum(1 for row in recent_conc["per_symbol"] if row["pnl_amount"] > 0)

    def eval_plateau(lookback: int, overlay_target: float, equity_target: float, family: str, name: str) -> dict:
        target_equity = equity[2]
        target_stress = equity[3]
        if equity_target != 0.15:
            previous = r114.TARGET_VOL
            try:
                r114.TARGET_VOL = equity_target
                target_equity = r114._vol_target(risk[2])["curve"]
                target_stress = r114._vol_target(risk[3])["curve"]
            finally:
                r114.TARGET_VOL = previous
        bucket = build_candidate(target_equity, lookback, overlay_target, OVERLAY_COST[2], r331.PRIMARY)
        stress_bucket = build_candidate(target_stress, lookback, overlay_target, OVERLAY_COST["3c"], r331.PRIMARY)
        m = metrics(bucket["curve"], stress_bucket["curve"])
        contributions = {"equity": bucket["contributions"]["equity"], **bucket["actual_contributions"]}
        total = sum(max(value, 0.0) for value in contributions.values())
        shares = {key: (value / total if total > 0 else None) for key, value in contributions.items()}
        conc_full, _, recent_positive = concentration_for_variant(bucket, rp[2], target_equity)
        weights = {**bucket["weight_summary"], **bucket["actual_weight_summary"]}
        gates = {
            "G1": m["stats"]["annual"] >= 0.12,
            "G2": m["stats"]["mdd"] >= -0.15,
            "G3": m["stats"]["sharpe"] >= 1.25,
            "G4": m["yearly"].get("2024", -1.0) >= 0.0 and m["yearly"].get("2025", -1.0) >= 0.0,
            "G5": m["recent"]["total_return"] > 0,
            "G6": m["bootstrap"]["positive_rate"] >= 0.80 and m["bootstrap"]["return_p05"] >= -0.12,
            "G7": m["rolling"]["positive_rate"] >= 0.70 and m["rolling"]["return_worst"] >= -0.15,
            "G8": m["stress"]["total_return"] > 0 and m["stress"]["mdd"] >= -0.25,
            "C1": conc_full["distinct_symbols"] >= 80,
            "C2": conc_full["total_pnl_amount"] > 0 and conc_full["top1_net_share"] <= 0.25,
            "C3": conc_full["total_pnl_amount"] > 0 and conc_full["top3_net_share"] <= 0.55,
            "C4": conc_full["total_pnl_amount"] > 0 and conc_full["top5_net_share"] <= 0.75,
            "C5": recent_positive >= 8,
            "C6": conc_full["top_industry_gross_share"] is not None and conc_full["top_industry_gross_share"] <= 0.45,
            "C7": conc_full["max_symbol_loss_to_gross"] is not None and conc_full["max_symbol_loss_to_gross"] <= 0.20,
            "C8": all(value is not None and value <= 0.50 for value in shares.values()),
            "C9": (
                weights["gold_weight_mean"] <= 0.40 and weights["gold_weight_max"] <= 0.50
                and weights["treasury_weight_mean"] <= 0.40 and weights["treasury_weight_max"] <= 0.50
                and weights["dividend_weight_mean"] <= 0.40 and weights["dividend_weight_max"] <= 0.50
            ),
            "C11": bucket["annual_turnover"] <= 1.5,
            "C12": weights["max_weight_sum"] <= 1.0 and weights["min_target_weight"] >= 0.0,
        }
        return {
            "name": name,
            "family": family,
            "lookback": lookback,
            "overlay_target": overlay_target,
            "equity_target": equity_target,
            "metrics": m,
            "gates": gates,
            "all_pass": all(gates.values()),
            "concentration": {key: conc_full.get(key) for key in ("distinct_symbols", "top1_net_share", "top3_net_share", "top5_net_share", "top_industry_gross_share", "max_symbol_loss_to_gross")},
            "asset_contribution_shares": {key: round(value, 6) if value is not None else None for key, value in shares.items()},
            "defense_selection_summary": bucket["defense_selection_summary"],
        }

    def concentration_at_5x(allocation: dict, rp_records: list[dict], equity_curve: pl.DataFrame) -> dict:
        trades = [trade for strategy_id in STRATEGIES for trade in full_trades[(5, strategy_id)]]
        curve_dates = equity_curve["date"].to_list()
        scaled = []
        for trade in trades:
            entry = r326.parse_day(trade["entry_date"])
            raw_exit = trade.get("exit_date")
            exit_ = r326.parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
            days = [day for day in curve_dates if entry <= day <= exit_] or [entry]
            scales = []
            for day in days:
                eqw = r326.weight_at(allocation["rebalances"], day, "target_equity_weight")
                sleeve = r326.weight_at(rp_records, day, f"weight_{trade['sleeve'].lower()}")
                scales.append(eqw * sleeve)
            scaled.append({**trade, "pnl_amount": round(trade["pnl_amount"] * sum(scales) / len(scales), 6)})
        return r239._aggregate(scaled, industry)

    def eval_cap(cap: float) -> dict:
        risk_2x, _ = _combine_three((full[2][A_STRATEGY], full[2][B_STRATEGY], full[2][C_STRATEGY]), r112.ROUND_TRIP_COST, cap)
        risk_5x, rp_5x = _combine_three((full[5][A_STRATEGY], full[5][B_STRATEGY], full[5][C_STRATEGY]), r112.ROUND_TRIP_COST, cap)
        equity_2x = r114._vol_target(risk_2x)["curve"]
        equity_5x = r114._vol_target(risk_5x)["curve"]
        bucket_2x = build_candidate(equity_2x, 60, 0.15, OVERLAY_COST[2], r331.PRIMARY)
        bucket_5x = build_candidate(equity_5x, 60, 0.15, OVERLAY_COST[5], r331.PRIMARY)
        m2 = metrics(bucket_2x["curve"])
        m5 = metrics(bucket_5x["curve"])
        conc_5x = concentration_at_5x(bucket_5x, rp_5x, equity_5x)
        fresh_positive = 0
        for phase in PHASES:
            phase_risk, _ = _combine_three(
                (phase_curves[phase][A_STRATEGY], phase_curves[phase][B_STRATEGY], phase_curves[phase][C_STRATEGY]),
                r112.ROUND_TRIP_COST,
                cap,
            )
            phase_equity = r114._vol_target(phase_risk)["curve"]
            phase_bucket = build_candidate(phase_equity, 60, 0.15, OVERLAY_COST[2], r331.PRIMARY)
            fresh_positive += r112._stats(phase_bucket["curve"])["total_return"] > 0
        gates = {
            "2x_annual": m2["stats"]["annual"] >= 0.12,
            "2x_sharpe": m2["stats"]["sharpe"] >= 1.25,
            "2x_mdd": m2["stats"]["mdd"] >= -0.15,
            "5x_annual": m5["stats"]["annual"] >= 0.10,
            "5x_sharpe": m5["stats"]["sharpe"] >= 1.10,
            "5x_mdd": m5["stats"]["mdd"] >= -0.15,
            "5x_C2": conc_5x["total_pnl_amount"] > 0 and conc_5x["top1_net_share"] <= 0.25,
            "5x_C3": conc_5x["total_pnl_amount"] > 0 and conc_5x["top3_net_share"] <= 0.55,
            "5x_C4": conc_5x["total_pnl_amount"] > 0 and conc_5x["top5_net_share"] <= 0.75,
            "fresh": fresh_positive >= 5,
        }
        return {
            "cap": cap,
            "metrics_2x": m2,
            "metrics_5x": m5,
            "concentration_5x": {key: conc_5x.get(key) for key in ("distinct_symbols", "top1_net_share", "top3_net_share", "top5_net_share", "top_industry_gross_share", "max_symbol_loss_to_gross", "total_pnl_amount")},
            "fresh_positive": fresh_positive,
            "gates": gates,
            "all_pass": all(gates.values()),
        }

    cap_results = {f"{cap:.2f}": eval_cap(cap) for cap in CAP_GRID}
    cap_passes = sum(1 for item in cap_results.values() if item["all_pass"])
    cap_plateau_pass = cap_passes >= 3 and cap_results[f"{C_WEIGHT_CAP:.2f}"]["all_pass"]
    overlay_results = {}
    for lookback in LOOKBACKS:
        for target in TARGETS:
            name = f"L{lookback}_T{round(target * 100)}"
            overlay_results[name] = eval_plateau(lookback, target, 0.15, "overlay_grid", name)
    joint_results = {}
    for target in TARGETS:
        name = f"joint_T{round(target * 100)}"
        joint_results[name] = overlay_results["L60_T15"] if target == 0.15 else eval_plateau(60, target, target, "joint_target_grid", name)
    overlay_passes = sum(1 for item in overlay_results.values() if item["all_pass"])
    joint_passes = sum(1 for item in joint_results.values() if item["all_pass"])
    plateau_pass = overlay_passes >= 7 and joint_passes >= 2 and overlay_results["L60_T15"]["all_pass"]
    cost_gates = {
        "4x": cost_metrics["4x"]["stats"]["total_return"] > 0 and cost_metrics["4x"]["stats"]["mdd"] >= -0.25,
        "5x_performance": (
            cost_metrics["5x"]["stats"]["total_return"] > 0
            and cost_metrics["5x"]["stats"]["sharpe"] >= 0.50
            and cost_metrics["5x"]["stats"]["mdd"] >= -0.25
            and cost_metrics["5x"]["yearly"].get("2024", -1.0) >= 0.0
            and cost_metrics["5x"]["yearly"].get("2025", -1.0) >= 0.0
            and cost_metrics["5x"]["recent"]["total_return"] > 0
        ),
        "5x_concentration": all(c5_gates.values()),
    }
    conservative_gate = conservative_metrics["stats"]["total_return"] > 0 and conservative_metrics["stats"]["mdd"] >= -0.25
    cache_after = {key: sha256(path) for key, path in cache_paths.items()}
    asset_paths = {kind: ASSET_CACHE / filename for kind, filename in ASSET_FILES.items()}
    assets_before = {kind: sha256(path) for kind, path in asset_paths.items()}
    assets_after = {kind: sha256(path) for kind, path in asset_paths.items()}
    data_gates = {
        "D1_preregistration": prereg["status"] == "pre_registered_before_r349_portfolio_backtest",
        "D2_preflight": bool(preflight["all_checks"] and preflight["no_return_or_backtest_evaluated"]),
        "D3_r344_cache_reconstructs_control": all(baseline_validation.values()),
        "D4_cache_and_assets_stable": cache_before == cache_after and assets_before == assets_after,
        "D5_correlation": mean_pairwise_correlation < 0.85,
    }
    all_pass = (
        all(data_gates.values())
        and all(performance_gates.values())
        and all(extension_gates.values())
        and all(c2_gates.values())
        and all(robust_gates.values())
        and fresh_gate
        and fresh_extension_gate
        and all(cost_gates.values())
        and conservative_gate
        and plateau_pass
        and cap_plateau_pass
    )
    payload = {
        "round": "R349",
        "candidate_id": prereg["candidate_id"],
        "classification": prereg["classification"],
        "hypothesis": prereg["hypothesis"],
        "preregistration_sha256": sha256(PREREG),
        "preflight_sha256": sha256(PREFLIGHT),
        "preflight_erratum_sha256": sha256(PREFLIGHT_ERRATUM),
        "r344_control_result_sha256": sha256(R344_RESULT),
        "asset_manifest_sha256": sha256(ASSET_MANIFEST),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "defensive_rule": prereg["defensive_rule"],
        "center_2x": center_metrics,
        "defense_selection_summary": core[2]["defense_selection_summary"],
        "defense_weights_2x": {**core[2]["weight_summary"], **core[2]["actual_weight_summary"]},
        "r344_control_2x": {
            "stats": r344_control_stats,
            "yearly": r344_result["center_2x"]["yearly"],
            "recent": r344_result["center_2x"]["recent"],
            "extension_2022": r344_result["extension_2022"],
            "prewindow": r344_result["prewindow"],
            "concentration_5x": r344_result["concentration_5x"]["concentration"],
        },
        "extension_2022": extension_2022,
        "prewindow": prewindow,
        "extension_gates": extension_gates,
        "fresh_extension_gate": fresh_extension_gate,
        "center_3x_conservative": conservative_metrics,
        "cost_metrics": cost_metrics,
        "concentration_2x": c2_summary,
        "concentration_5x": c5_summary,
        "performance_gates": performance_gates,
        "concentration_gates_2x": c2_gates,
        "concentration_gates_5x": c5_gates,
        "robustness": robust,
        "robustness_gates": robust_gates,
        "fresh_starts": fresh,
        "fresh_start_gate": fresh_gate,
        "overlay_grid": overlay_results,
        "joint_target_grid": joint_results,
        "plateau_summary": {
            "overlay_passes": overlay_passes,
            "joint_passes": joint_passes,
            "center_pass": overlay_results["L60_T15"]["all_pass"],
            "plateau_pass": plateau_pass,
        },
        "c_satellite_cap": C_WEIGHT_CAP,
        "cap_plateau": cap_results,
        "cap_plateau_summary": {
            "passes": cap_passes,
            "required": 3,
            "center_pass": cap_results[f"{C_WEIGHT_CAP:.2f}"]["all_pass"],
            "plateau_pass": cap_plateau_pass,
        },
        "cost_gates": cost_gates,
        "conservative_3x_gate": conservative_gate,
        "data_gates": data_gates,
        "baseline_cache_validation": baseline_validation,
        "correlations": correlations,
        "mean_pairwise_correlation": mean_pairwise_correlation,
        "sleeve_weights": sleeve_weights,
        "cache_manifest": cache_before,
        "input_isolation": {"cache_stable": cache_before == cache_after, "assets_stable": assets_before == assets_after, "assets_before": assets_before, "assets_after": assets_after},
        "all_pass": all_pass,
        "selected": prereg["candidate_id"] if all_pass else None,
        "decision": {
            "status": "candidate_for_paper_trading_review" if all_pass else "no_candidate_passed",
            "post_hoc": True,
            "paper_target_generated": False,
            "order_file_generated": False,
            "production_write": False,
            "provider_write": False,
            "parameter_search": False,
        },
        "limits": prereg["known_limitations"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({
        "center_2x": center_metrics["stats"],
        "center_yearly": center_metrics["yearly"],
        "extension_2022": extension_2022,
        "prewindow": prewindow,
        "recent": center_metrics["recent"],
        "defense_selection_summary": core[2]["defense_selection_summary"],
        "defense_weights": {**core[2]["weight_summary"], **core[2]["actual_weight_summary"]},
        "3x_conservative": conservative_metrics["stats"],
        "4x": cost_metrics["4x"]["stats"],
        "5x": cost_metrics["5x"]["stats"],
        "concentration_2x": c2_summary["concentration"],
        "concentration_5x": c5_summary["concentration"],
        "plateau": payload["plateau_summary"],
        "cap_plateau": payload["cap_plateau_summary"],
        "cost_gates": cost_gates,
        "robustness_gates": robust_gates,
        "fresh_start_gate": fresh_gate,
        "all_pass": all_pass,
        "selected": payload["selected"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
