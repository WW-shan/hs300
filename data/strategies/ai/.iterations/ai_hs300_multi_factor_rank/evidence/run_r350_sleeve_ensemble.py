from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
ASSET_CACHE = DATA / "research/r331_defensive_assets"
R344_CACHE = DATA / "research/r344_cache"
R346_CACHE = DATA / "research/r346_cache"
PREREG = EVIDENCE / "r350_sleeve_ensemble_preregistration.json"
PREFLIGHT_V1 = EVIDENCE / "r350_sleeve_ensemble_preflight.json"
PREFLIGHT = EVIDENCE / "r350_sleeve_ensemble_preflight_v2.json"
AMENDMENT = EVIDENCE / "r350_preflight_amendment_A1.json"
V2_FAILURE = EVIDENCE / "r350_preflight_v2_attempt1_failure.json"
R344_RESULT = EVIDENCE / "r344_extended_window.json"
R346_RESULT = EVIDENCE / "r346_ab_top20_extended.json"
ASSET_MANIFEST = EVIDENCE / "r331_asset_manifest.json"
OUTPUT = EVIDENCE / "r350_sleeve_ensemble.json"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
C_STRATEGY = "ai_hs300_quality_momentum_broad_h60"
C_WEIGHT_CAP = 0.10
CAP_GRID = [0.05, 0.10, 0.15, 0.20]
START = date(2022, 5, 5)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
PHASES = [START, date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), date(2025, 11, 5), date(2026, 1, 5)]
LOOKBACKS = [40, 60, 90]
TARGETS = [0.12, 0.15, 0.18]
OVERLAY_COST = {2: 0.001, 3: 0.0015, "3c": 0.003, 4: 0.002, 5: 0.0025}
ENSEMBLE_COST = {2: 0.001, 3: 0.0015, "3c": 0.003, 4: 0.002, 5: 0.0025}
CACHE_SOURCES = {
    "A10": (R344_CACHE, A_STRATEGY, 10),
    "A20": (R346_CACHE, A_STRATEGY, 20),
    "B10": (R344_CACHE, B_STRATEGY, 10),
    "B20": (R346_CACHE, B_STRATEGY, 20),
    "C": (R344_CACHE, C_STRATEGY, 50),
}
sys.path.insert(0, str(ROOT / "backend"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def curve_path(label: str, start: date, factor: str) -> Path:
    cache, strategy_id, positions = CACHE_SOURCES[label]
    return cache / f"{strategy_id}_{start}_{factor}x_p{positions}.parquet"


def trades_path(label: str, factor: int) -> Path:
    cache, strategy_id, positions = CACHE_SOURCES[label]
    return cache / f"{strategy_id}_{START}_{factor}x_p{positions}.trades.json"


def load_curve(path: Path) -> pl.DataFrame:
    return pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")


def blend_pair(left: pl.DataFrame, right: pl.DataFrame, round_trip_cost: float, r112) -> tuple[pl.DataFrame, list[dict]]:
    previous = r112.ROUND_TRIP_COST
    try:
        r112.ROUND_TRIP_COST = round_trip_cost
        return r112._combine(left, right, "fixed")
    finally:
        r112.ROUND_TRIP_COST = previous


def close_stats(actual: dict, expected: dict) -> bool:
    return all(abs(float(actual[key]) - float(expected[key])) <= 2e-6 for key in ("total_return", "annual", "sharpe", "mdd"))


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R350 result exists; refusing overwrite")
    for path in (PREREG, PREFLIGHT_V1, PREFLIGHT, AMENDMENT, V2_FAILURE, R344_RESULT, R346_RESULT, ASSET_MANIFEST):
        expected = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if sha256(path) != expected:
            raise RuntimeError(f"checksum mismatch: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    preflight = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    v2_failure = json.loads(V2_FAILURE.read_text(encoding="utf-8"))
    manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    r344_result = json.loads(R344_RESULT.read_text(encoding="utf-8"))
    r346_result = json.loads(R346_RESULT.read_text(encoding="utf-8"))
    if prereg["round"] != "R350" or prereg["status"] != "pre_registered_before_r350_portfolio_backtest":
        raise RuntimeError("R350 preregistration status is invalid")
    if not preflight["all_checks"] or not preflight["no_return_or_backtest_evaluated"]:
        raise RuntimeError("R350 v2 preflight did not pass")
    if amendment["status"] != "pre_registration_amendment_before_candidate_returns" or not amendment["no_return_or_backtest_evaluated"]:
        raise RuntimeError("R350 preflight amendment is invalid")
    if v2_failure["status"] != "failed_no_returns_evaluated" or not v2_failure["no_return_or_backtest_evaluated"]:
        raise RuntimeError("R350 v2 failure record is invalid")
    if not manifest["all_pass"]:
        raise RuntimeError("asset manifest did not pass")
    if sha256(Path(__file__).resolve()) != prereg["artifact_sha256"]["runner"]:
        raise RuntimeError("R350 runner hash changed after preregistration")
    if sha256(EVIDENCE / "run_r350_sleeve_ensemble_preflight_v2.py") != prereg["artifact_sha256"]["preflight_v2_runner"]:
        raise RuntimeError("R350 preflight runner hash changed after preregistration")
    strategy_files = {
        "strategy_A": DATA / "strategies/ai" / f"{A_STRATEGY}.py",
        "strategy_B": DATA / "strategies/ai" / f"{B_STRATEGY}.py",
        "strategy_C": DATA / "strategies/ai" / f"{C_STRATEGY}.py",
    }
    if any(sha256(path) != prereg["artifact_sha256"][key] for key, path in strategy_files.items()):
        raise RuntimeError("R350 stock strategy source changed after preregistration")

    r349 = module(EVIDENCE / "run_r349_defensive_dual_momentum.py", "r349_lib_r350")
    r331 = module(EVIDENCE / "run_r331_three_asset_bucket.py", "r331_r350")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r350")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r350")
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r350")
    r313 = module(EVIDENCE / "run_r313_concentration_audit.py", "r313_r350")
    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r350")
    r326 = module(EVIDENCE / "run_r326_gold_concentration.py", "r326_r350")
    r331.self_test()
    r114.TARGET_VOL = 0.15
    industry_frame = r313.load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}

    cache_paths: dict[str, Path] = {}
    for label in CACHE_SOURCES:
        for factor in (2, 3, 4, 5):
            path = curve_path(label, START, str(factor))
            cache_paths[f"{label}_{factor}x"] = path
        for phase in PHASES:
            path = curve_path(label, phase, "2")
            cache_paths[f"{label}_{phase}_2x"] = path
        for factor in (2, 5):
            cache_paths[f"{label}_{factor}x_trades"] = trades_path(label, factor)
    missing = [key for key, path in cache_paths.items() if not path.is_file()]
    if missing:
        raise RuntimeError(f"R350 cache files missing: {missing[:5]}")
    cache_before = {key: sha256(path) for key, path in cache_paths.items()}
    if len(cache_before) != 60:
        raise RuntimeError(f"unexpected R350 cache manifest size: {len(cache_before)}")

    full = {label: {} for label in CACHE_SOURCES}
    full_trades = {label: {} for label in CACHE_SOURCES}
    phase_curves = {phase: {label: None for label in CACHE_SOURCES} for phase in PHASES}
    for label in CACHE_SOURCES:
        for factor in (2, 3, 4, 5):
            full[label][factor] = load_curve(cache_paths[f"{label}_{factor}x"])
        for phase in PHASES:
            phase_curves[phase][label] = load_curve(cache_paths[f"{label}_{phase}_2x"])
        for factor in (2, 5):
            full_trades[label][factor] = json.loads(cache_paths[f"{label}_{factor}x_trades"].read_text(encoding="utf-8"))

    def build_fixed(equity_curve: pl.DataFrame, lookback: int, overlay_target: float, overlay_cost: float, symbols: dict) -> dict:
        previous_lookback = r331.LOOKBACK
        previous_target = r331.TARGET_VOL
        previous_cap = r331.EQUITY_WEIGHT_MAX
        try:
            r331.LOOKBACK = lookback
            r331.TARGET_VOL = overlay_target
            r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
            gold_asset = r349.asset_frame("gold", symbols["gold"])
            treasury_asset = r349.asset_frame("treasury", symbols["treasury"])
            dividend_asset = r349.asset_frame("dividend", symbols["dividend"])
            return r331.build_three_asset_bucket(
                equity_curve,
                r349.gold_trend_proxy(gold_asset, treasury_asset, dividend_asset, overlay_cost),
                treasury_asset,
                dividend_asset,
                round_trip_cost=overlay_cost,
            )
        finally:
            r331.LOOKBACK = previous_lookback
            r331.TARGET_VOL = previous_target
            r331.EQUITY_WEIGHT_MAX = previous_cap

    def portfolio_from_curves(curves: dict[str, pl.DataFrame], factor: str, cap: float) -> tuple[pl.DataFrame, list[dict], dict]:
        risk, rp = r349._combine_three(
            (curves["A"], curves["B"], curves["C"]),
            r112.ROUND_TRIP_COST,
            cap,
        )
        equity = r114._vol_target(risk)["curve"]
        bucket = build_fixed(equity, 60, 0.15, OVERLAY_COST[factor], r331.PRIMARY)
        return risk, rp, bucket

    baseline_r344 = portfolio_from_curves(
        {"A": full["A10"][2], "B": full["B10"][2], "C": full["C"][2]},
        2,
        C_WEIGHT_CAP,
    )
    baseline_r346 = portfolio_from_curves(
        {"A": full["A20"][2], "B": full["B20"][2], "C": full["C"][2]},
        2,
        C_WEIGHT_CAP,
    )
    baseline_validation = {
        "R344_center_2x": close_stats(r112._stats(baseline_r344[2]["curve"]), r344_result["center_2x"]["stats"]),
        "R346_center_2x": close_stats(r112._stats(baseline_r346[2]["curve"]), r346_result["center_2x"]["stats"]),
    }
    if not all(baseline_validation.values()):
        raise RuntimeError(f"R350 cache does not reconstruct frozen controls: {baseline_validation}")

    risk = {}
    rp = {}
    equity = {}
    core = {}
    for factor in (2, 3, 4, 5):
        a_curve, _ = blend_pair(full["A10"][factor], full["A20"][factor], ENSEMBLE_COST[factor], r112)
        b_curve, _ = blend_pair(full["B10"][factor], full["B20"][factor], ENSEMBLE_COST[factor], r112)
        risk[factor], rp[factor] = r349._combine_three(
            (a_curve, b_curve, full["C"][factor]),
            r112.ROUND_TRIP_COST,
            C_WEIGHT_CAP,
        )
        equity[factor] = r114._vol_target(risk[factor])["curve"]
        core[factor] = build_fixed(equity[factor], 60, 0.15, OVERLAY_COST[factor], r331.PRIMARY)
    core_conservative_3x_a, _ = blend_pair(full["A10"][3], full["A20"][3], ENSEMBLE_COST["3c"], r112)
    core_conservative_3x_b, _ = blend_pair(full["B10"][3], full["B20"][3], ENSEMBLE_COST["3c"], r112)
    conservative_risk, _ = r349._combine_three(
        (core_conservative_3x_a, core_conservative_3x_b, full["C"][3]),
        r112.ROUND_TRIP_COST,
        C_WEIGHT_CAP,
    )
    conservative_equity = r114._vol_target(conservative_risk)["curve"]
    core_conservative_3x = build_fixed(conservative_equity, 60, 0.15, OVERLAY_COST["3c"], r331.PRIMARY)

    def scaled_trades(factor: int) -> list[dict]:
        specs = [
            ("A10", 0.5, "weight_a"),
            ("A20", 0.5, "weight_a"),
            ("B10", 0.5, "weight_b"),
            ("B20", 0.5, "weight_b"),
            ("C", 1.0, "weight_c"),
        ]
        curve_dates = risk[factor]["date"].to_list()
        scaled = []
        for label, sub_weight, sleeve_key in specs:
            for trade in full_trades[label][factor]:
                entry = r326.parse_day(trade["entry_date"])
                raw_exit = trade.get("exit_date")
                exit_ = r326.parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
                days = [day for day in curve_dates if entry <= day <= exit_] or [entry]
                scales = []
                for day in days:
                    equity_weight = r326.weight_at(core[factor]["rebalances"], day, "target_equity_weight")
                    sleeve_share = r326.weight_at(rp[factor], day, sleeve_key)
                    scales.append(sub_weight * equity_weight * sleeve_share)
                scale = sum(scales) / len(scales)
                scaled.append({**trade, "ensemble_label": label, "weight_scale": round(scale, 6), "pnl_amount": round(float(trade["pnl_amount"]) * scale, 6)})
        return scaled

    def concentration(factor: int) -> tuple[dict, dict, int]:
        scaled = scaled_trades(factor)
        full_conc = r239._aggregate(scaled, industry)
        recent_conc = r239._aggregate([trade for trade in scaled if r326.parse_day(trade["entry_date"]) >= RECENT_START], industry)
        return full_conc, recent_conc, sum(1 for row in recent_conc["per_symbol"] if row["pnl_amount"] > 0)

    def concentration_gates(factor: int) -> tuple[dict, dict]:
        full_conc, _recent_conc, recent_positive = concentration(factor)
        contributions = core[factor]["contributions"]
        total = sum(max(value, 0.0) for value in contributions.values())
        shares = {key: (value / total if total > 0 else None) for key, value in contributions.items()}
        weights = core[factor]["weight_summary"]
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
    a_2x, _ = blend_pair(full["A10"][2], full["A20"][2], ENSEMBLE_COST[2], r112)
    b_2x, _ = blend_pair(full["B10"][2], full["B20"][2], ENSEMBLE_COST[2], r112)
    correlations = {
        "AB": round(r349._corr(a_2x, b_2x), 6),
        "AC": round(r349._corr(a_2x, full["C"][2]), 6),
        "BC": round(r349._corr(b_2x, full["C"][2]), 6),
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
        bucket = build_fixed(equity[2], 60, 0.15, OVERLAY_COST[2], symbols)
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
        a_phase, _ = blend_pair(phase_curves[phase]["A10"], phase_curves[phase]["A20"], ENSEMBLE_COST[2], r112)
        b_phase, _ = blend_pair(phase_curves[phase]["B10"], phase_curves[phase]["B20"], ENSEMBLE_COST[2], r112)
        phase_risk, _ = r349._combine_three((a_phase, b_phase, phase_curves[phase]["C"]), r112.ROUND_TRIP_COST, C_WEIGHT_CAP)
        phase_equity = r114._vol_target(phase_risk)["curve"]
        bucket = build_fixed(phase_equity, 60, 0.15, OVERLAY_COST[2], r331.PRIMARY)
        fresh[str(phase)] = {"stats": r112._stats(bucket["curve"]), "yearly": r315.year_returns(bucket["curve"])}
    fresh_gate = sum(1 for item in fresh.values() if item["stats"]["total_return"] > 0) >= 5 and all(item["stats"]["mdd"] >= -0.18 for item in fresh.values())
    fresh_extension_gate = fresh[str(START)]["stats"]["total_return"] > 0

    def candidate_equity_for_target(equity_target: float, factor: int) -> pl.DataFrame:
        if equity_target == 0.15:
            return equity[factor]
        previous = r114.TARGET_VOL
        try:
            r114.TARGET_VOL = equity_target
            return r114._vol_target(risk[factor])["curve"]
        finally:
            r114.TARGET_VOL = previous

    def concentration_for_variant(allocation: dict, rp_records: list[dict], equity_curve: pl.DataFrame) -> tuple[dict, dict, int]:
        curve_dates = equity_curve["date"].to_list()
        specs = [
            ("A10", 0.5, "weight_a"),
            ("A20", 0.5, "weight_a"),
            ("B10", 0.5, "weight_b"),
            ("B20", 0.5, "weight_b"),
            ("C", 1.0, "weight_c"),
        ]
        scaled = []
        for label, sub_weight, sleeve_key in specs:
            for trade in full_trades[label][2]:
                entry = r326.parse_day(trade["entry_date"])
                raw_exit = trade.get("exit_date")
                exit_ = r326.parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
                days = [day for day in curve_dates if entry <= day <= exit_] or [entry]
                scales = []
                for day in days:
                    eqw = r326.weight_at(allocation["rebalances"], day, "target_equity_weight")
                    sleeve = r326.weight_at(rp_records, day, sleeve_key)
                    scales.append(sub_weight * eqw * sleeve)
                scale = sum(scales) / len(scales)
                scaled.append({**trade, "pnl_amount": round(float(trade["pnl_amount"]) * scale, 6)})
        full_conc = r239._aggregate(scaled, industry)
        recent_conc = r239._aggregate([trade for trade in scaled if r326.parse_day(trade["entry_date"]) >= RECENT_START], industry)
        return full_conc, recent_conc, sum(1 for row in recent_conc["per_symbol"] if row["pnl_amount"] > 0)

    def eval_plateau(lookback: int, overlay_target: float, equity_target: float, family: str, name: str) -> dict:
        target_equity = candidate_equity_for_target(equity_target, 2)
        target_stress = candidate_equity_for_target(equity_target, 3)
        bucket = build_fixed(target_equity, lookback, overlay_target, OVERLAY_COST[2], r331.PRIMARY)
        stress_bucket = build_fixed(target_stress, lookback, overlay_target, OVERLAY_COST["3c"], r331.PRIMARY)
        m = metrics(bucket["curve"], stress_bucket["curve"])
        contributions = bucket["contributions"]
        total = sum(max(value, 0.0) for value in contributions.values())
        shares = {key: (value / total if total > 0 else None) for key, value in contributions.items()}
        conc_full, _, recent_positive = concentration_for_variant(bucket, rp[2], target_equity)
        weights = bucket["weight_summary"]
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
        }

    def concentration_at_5x(allocation: dict, rp_records: list[dict], equity_curve: pl.DataFrame) -> dict:
        curve_dates = equity_curve["date"].to_list()
        specs = [
            ("A10", 0.5, "weight_a"),
            ("A20", 0.5, "weight_a"),
            ("B10", 0.5, "weight_b"),
            ("B20", 0.5, "weight_b"),
            ("C", 1.0, "weight_c"),
        ]
        scaled = []
        for label, sub_weight, sleeve_key in specs:
            for trade in full_trades[label][5]:
                entry = r326.parse_day(trade["entry_date"])
                raw_exit = trade.get("exit_date")
                exit_ = r326.parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
                days = [day for day in curve_dates if entry <= day <= exit_] or [entry]
                scales = []
                for day in days:
                    eqw = r326.weight_at(allocation["rebalances"], day, "target_equity_weight")
                    sleeve = r326.weight_at(rp_records, day, sleeve_key)
                    scales.append(sub_weight * eqw * sleeve)
                scale = sum(scales) / len(scales)
                scaled.append({**trade, "pnl_amount": round(float(trade["pnl_amount"]) * scale, 6)})
        return r239._aggregate(scaled, industry)

    def eval_cap(cap: float) -> dict:
        a2, _ = blend_pair(full["A10"][2], full["A20"][2], ENSEMBLE_COST[2], r112)
        b2, _ = blend_pair(full["B10"][2], full["B20"][2], ENSEMBLE_COST[2], r112)
        risk_2x, _ = r349._combine_three((a2, b2, full["C"][2]), r112.ROUND_TRIP_COST, cap)
        a5, _ = blend_pair(full["A10"][5], full["A20"][5], ENSEMBLE_COST[5], r112)
        b5, _ = blend_pair(full["B10"][5], full["B20"][5], ENSEMBLE_COST[5], r112)
        risk_5x, rp_5x = r349._combine_three((a5, b5, full["C"][5]), r112.ROUND_TRIP_COST, cap)
        equity_2x = r114._vol_target(risk_2x)["curve"]
        equity_5x = r114._vol_target(risk_5x)["curve"]
        bucket_2x = build_fixed(equity_2x, 60, 0.15, OVERLAY_COST[2], r331.PRIMARY)
        bucket_5x = build_fixed(equity_5x, 60, 0.15, OVERLAY_COST[5], r331.PRIMARY)
        m2 = metrics(bucket_2x["curve"])
        m5 = metrics(bucket_5x["curve"])
        conc_5x = concentration_at_5x(bucket_5x, rp_5x, equity_5x)
        fresh_positive = 0
        for phase in PHASES:
            a_phase, _ = blend_pair(phase_curves[phase]["A10"], phase_curves[phase]["A20"], ENSEMBLE_COST[2], r112)
            b_phase, _ = blend_pair(phase_curves[phase]["B10"], phase_curves[phase]["B20"], ENSEMBLE_COST[2], r112)
            phase_risk, _ = r349._combine_three((a_phase, b_phase, phase_curves[phase]["C"]), r112.ROUND_TRIP_COST, cap)
            phase_equity = r114._vol_target(phase_risk)["curve"]
            phase_bucket = build_fixed(phase_equity, 60, 0.15, OVERLAY_COST[2], r331.PRIMARY)
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
    data_gates = {
        "D1_preregistration": prereg["status"] == "pre_registered_before_r350_portfolio_backtest",
        "D2_preflight": bool(preflight["all_checks"] and preflight["no_return_or_backtest_evaluated"]),
        "D3_controls_reconstructed": all(baseline_validation.values()),
        "D4_cache_stable": cache_before == cache_after,
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
        "round": "R350",
        "candidate_id": prereg["candidate_id"],
        "classification": prereg["classification"],
        "hypothesis": prereg["hypothesis"],
        "preregistration_sha256": sha256(PREREG),
        "preflight_v1_sha256": sha256(PREFLIGHT_V1),
        "preflight_v2_sha256": sha256(PREFLIGHT),
        "preflight_amendment_sha256": sha256(AMENDMENT),
        "preflight_v2_failure_sha256": sha256(V2_FAILURE),
        "r344_control_result_sha256": sha256(R344_RESULT),
        "r346_top20_result_sha256": sha256(R346_RESULT),
        "asset_manifest_sha256": sha256(ASSET_MANIFEST),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "ensemble_rule": prereg["ensemble_rule"],
        "center_2x": center_metrics,
        "r344_control_2x": {
            "stats": r344_control_stats,
            "yearly": r344_result["center_2x"]["yearly"],
            "recent": r344_result["center_2x"]["recent"],
            "extension_2022": r344_result["extension_2022"],
            "prewindow": r344_result["prewindow"],
            "concentration_5x": r344_result["concentration_5x"]["concentration"],
        },
        "r346_top20_2x": {
            "stats": r346_result["center_2x"]["stats"],
            "yearly": r346_result["center_2x"]["yearly"],
            "extension_2022": r346_result["extension_2022"],
            "concentration_5x": r346_result["concentration_5x"]["concentration"],
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
        "input_isolation": {"cache_stable": cache_before == cache_after},
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
