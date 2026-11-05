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
PREREG = EVIDENCE / "r334_equity_cap33_preregistration.json"
R332_RESULT = EVIDENCE / "r332_top10_bucket_promotion.json"
R333_RESULT = EVIDENCE / "r333_parameter_plateau.json"
ASSET_MANIFEST = EVIDENCE / "r331_asset_manifest.json"
R332_CACHE = DATA / "research" / "r332_cache"
OUTPUT = EVIDENCE / "r334_equity_cap33.json"
START = date(2021, 6, 1)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
PHASES = [date(2022, 1, 4), date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), date(2025, 11, 5)]
LOOKBACKS = [40, 60, 90]
TARGETS = [0.12, 0.15, 0.18]
MAIN_COST = 0.001
STRESS_COST = 0.003
sys.path.insert(0, str(ROOT / "backend"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def load_curve(strategy_id: str, start: date, cost_factor: int) -> pl.DataFrame:
    path = R332_CACHE / f"{strategy_id}_{start}_{cost_factor}x.parquet"
    if not path.exists():
        raise RuntimeError(f"R332 curve cache missing: {path.name}")
    return pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R333 result exists; refusing overwrite")
    for path in (PREREG, R332_RESULT, R333_RESULT, ASSET_MANIFEST):
        expected = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if sha256(path) != expected:
            raise RuntimeError(f"checksum mismatch: {path.name}")

    r331 = module(EVIDENCE / "run_r331_three_asset_bucket.py", "r331_r334")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r334")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r334")
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r334")
    r313 = module(EVIDENCE / "run_r313_concentration_audit.py", "r313_r334")
    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r334")
    r326 = module(EVIDENCE / "run_r326_gold_concentration.py", "r326_r334")
    r331.self_test()
    manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    if not manifest["all_pass"]:
        raise RuntimeError("asset manifest did not pass")

    a2 = load_curve(r331.A_STRATEGY, START, 2)
    b2 = load_curve(r331.B_STRATEGY, START, 2)
    a3 = load_curve(r331.A_STRATEGY, START, 3)
    b3 = load_curve(r331.B_STRATEGY, START, 3)
    a_trades = json.loads((R332_CACHE / f"{r331.A_STRATEGY}_{START}_2x.trades.json").read_text(encoding="utf-8"))
    b_trades = json.loads((R332_CACHE / f"{r331.B_STRATEGY}_{START}_2x.trades.json").read_text(encoding="utf-8"))
    risk2, rp2 = r112._combine(a2, b2, "risk")
    risk3, _ = r112._combine(a3, b3, "risk")
    curve_dates = risk2["date"].to_list()

    def vol_target_curve(risk: pl.DataFrame, target: float) -> pl.DataFrame:
        previous = r114.TARGET_VOL
        try:
            r114.TARGET_VOL = target
            return r114._vol_target(risk)["curve"]
        finally:
            r114.TARGET_VOL = previous

    equity_by_target = {target: vol_target_curve(risk2, target) for target in TARGETS}
    stress_equity_by_target = {target: vol_target_curve(risk3, target) for target in TARGETS}
    phase_risk = {}
    for phase in PHASES:
        pa = load_curve(r331.A_STRATEGY, phase, 2)
        pb = load_curve(r331.B_STRATEGY, phase, 2)
        phase_risk[phase], _ = r112._combine(pa, pb, "risk")
    phase_equity = {
        target: {phase: vol_target_curve(risk, target) for phase, risk in phase_risk.items()}
        for target in TARGETS
    }

    industry_frame = r313.load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}
    all_trades = [*a_trades, *b_trades]

    def build_with_params(equity: pl.DataFrame, lookback: int, overlay_target: float, cost: float) -> dict:
        previous_lookback = r331.LOOKBACK
        previous_target = r331.TARGET_VOL
        previous_equity_cap = r331.EQUITY_WEIGHT_MAX
        try:
            r331.LOOKBACK = lookback
            r331.TARGET_VOL = overlay_target
            r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
            return r331.build_three_asset_bucket(
                equity,
                r331.asset_frame("gold", r331.PRIMARY["gold"]),
                r331.asset_frame("treasury", r331.PRIMARY["treasury"]),
                r331.asset_frame("dividend", r331.PRIMARY["dividend"]),
                round_trip_cost=cost,
            )
        finally:
            r331.LOOKBACK = previous_lookback
            r331.TARGET_VOL = previous_target
            r331.EQUITY_WEIGHT_MAX = previous_equity_cap

    def scaled_concentration(allocation_records: list[dict]) -> tuple[dict, int]:
        scaled = []
        for trade in all_trades:
            entry = r326.parse_day(trade["entry_date"])
            raw_exit = trade.get("exit_date")
            exit_ = r326.parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
            days = [day for day in curve_dates if entry <= day <= exit_] or [entry]
            scale_values = []
            for day in days:
                equity_weight = r326.weight_at(allocation_records, day, "target_equity_weight")
                sleeve_share = r326.weight_at(rp2, day, "weight_a")
                scale_values.append(equity_weight * (sleeve_share if trade["sleeve"] == "A" else 1.0 - sleeve_share))
            scale = sum(scale_values) / len(scale_values)
            scaled.append({**trade, "weight_scale": round(scale, 6), "pnl_amount": round(trade["pnl_amount"] * scale, 6)})
        full = r239._aggregate(scaled, industry)
        recent_trades = [trade for trade in scaled if r326.parse_day(trade["entry_date"]) >= RECENT_START]
        recent = r239._aggregate(recent_trades, industry)
        return full, sum(1 for row in recent["per_symbol"] if row["pnl_amount"] > 0)

    def evaluate(name: str, lookback: int, overlay_target: float, equity_target: float, family: str) -> dict:
        equity = equity_by_target[equity_target]
        stress_equity = stress_equity_by_target[equity_target]
        primary = build_with_params(equity, lookback, overlay_target, MAIN_COST)
        stress = build_with_params(stress_equity, lookback, overlay_target, STRESS_COST)
        curve = primary["curve"]
        values = curve["value"].to_list()
        daily_returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
        stats = r112._stats(curve)
        yearly = r315.year_returns(curve)
        recent = r112._stats(r324_slice(primary["curve"]))
        bootstrap = r315.bootstrap(daily_returns)
        rolling = r315.rolling_returns(daily_returns, 252)
        rolling_positive = sum(value > 0 for value in rolling) / len(rolling)
        stress_stats = r112._stats(stress["curve"])
        full, recent_positive = scaled_concentration(primary["rebalances"])
        gross_contributions = sum(max(value, 0.0) for value in primary["contributions"].values())
        contribution_shares = {
            key: round(value / gross_contributions, 6) if gross_contributions > 0 else None
            for key, value in primary["contributions"].items()
        }
        weights = primary["weight_summary"]
        phase_results = {}
        for phase in PHASES:
            phase_bucket = build_with_params(phase_equity[equity_target][phase], lookback, overlay_target, MAIN_COST)
            phase_results[str(phase)] = {
                "stats": r112._stats(phase_bucket["curve"]),
                "yearly": r315.year_returns(phase_bucket["curve"]),
            }
        robust = {}
        for robust_name, symbols in r331.ROBUSTNESS.items():
            previous_lookback = r331.LOOKBACK
            previous_target = r331.TARGET_VOL
            previous_equity_cap = r331.EQUITY_WEIGHT_MAX
            try:
                r331.LOOKBACK = lookback
                r331.TARGET_VOL = overlay_target
                r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
                bucket = r331.build_three_asset_bucket(
                    equity,
                    r331.asset_frame("gold", symbols["gold"]),
                    r331.asset_frame("treasury", symbols["treasury"]),
                    r331.asset_frame("dividend", symbols["dividend"]),
                    round_trip_cost=MAIN_COST,
                )
            finally:
                r331.LOOKBACK = previous_lookback
                r331.TARGET_VOL = previous_target
                r331.EQUITY_WEIGHT_MAX = previous_equity_cap
            robust[robust_name] = {"stats": r112._stats(bucket["curve"]), "yearly": r315.year_returns(bucket["curve"])}
        positive_phases = sum(1 for item in phase_results.values() if item["stats"]["total_return"] > 0)
        performance_gates = {
            "G1_return": stats["annual"] >= 0.06,
            "G2_drawdown": stats["mdd"] >= -0.18,
            "G3_sharpe": stats["sharpe"] >= 0.70,
            "G4_calendar": yearly.get("2022", -1.0) >= 0.0 and yearly.get("2023", -1.0) >= 0.0,
            "G5_recent": recent["total_return"] > 0,
            "G6_bootstrap": bootstrap["positive_rate"] >= 0.80 and bootstrap["return_p05"] >= -0.12,
            "G7_rolling": rolling_positive >= 0.70 and min(rolling) >= -0.15,
            "G8_stress": stress_stats["total_return"] > 0 and stress_stats["mdd"] >= -0.25,
        }
        concentration_gates = {
            "C1_stock_breadth": full["distinct_symbols"] >= 80,
            "C2_top1": full["total_pnl_amount"] > 0 and full["top1_net_share"] <= 0.25,
            "C3_top3": full["total_pnl_amount"] > 0 and full["top3_net_share"] <= 0.55,
            "C4_top5": full["total_pnl_amount"] > 0 and full["top5_net_share"] <= 0.75,
            "C5_recent_breadth": recent_positive >= 8,
            "C6_industry": full["top_industry_gross_share"] is not None and full["top_industry_gross_share"] <= 0.45,
            "C7_loss": full["max_symbol_loss_to_gross"] is not None and full["max_symbol_loss_to_gross"] <= 0.20,
            "C8_asset_contribution": all(value is not None and value <= 0.50 for value in contribution_shares.values()),
            "C9_asset_weight": (
                weights["gold_weight_mean"] <= 0.40 and weights["gold_weight_max"] <= 0.50
                and weights["treasury_weight_mean"] <= 0.40 and weights["treasury_weight_max"] <= 0.50
                and weights["dividend_weight_mean"] <= 0.40 and weights["dividend_weight_max"] <= 0.50
            ),
            "C10_liquidity": manifest["gates"]["D3_primary_liquidity"],
            "C11_turnover": primary["annual_turnover"] <= 1.5,
            "C12_weights": weights["max_weight_sum"] <= 1.0 and weights["min_target_weight"] >= 0.0,
        }
        fresh_gates = positive_phases >= 4 and all(item["stats"]["mdd"] >= -0.18 for item in phase_results.values())
        robust_gates = {
            key: (
                item["stats"]["annual"] >= 0.05
                and item["stats"]["mdd"] >= -0.20
                and item["yearly"].get("2022", -1.0) >= 0.0
                and item["yearly"].get("2023", -1.0) >= 0.0
            )
            for key, item in robust.items()
        }
        all_pass = (
            curve["date"].min() == START
            and curve["date"].max() == END
            and all(performance_gates.values())
            and all(concentration_gates.values())
            and fresh_gates
            and all(robust_gates.values())
        )
        return {
            "name": name,
            "family": family,
            "lookback_days": lookback,
            "overlay_target_vol": overlay_target,
            "equity_composite_target_vol": equity_target,
            "stats": stats,
            "yearly": yearly,
            "recent_year": recent,
            "stress_3x": stress_stats,
            "bootstrap": bootstrap,
            "rolling_252": {
                "positive_rate": round(rolling_positive, 6),
                "return_p05": round(sorted(rolling)[int(0.05 * (len(rolling) - 1))], 6),
                "return_worst": round(min(rolling), 6),
            },
            "weights": weights,
            "turnover": round(primary["annual_turnover"], 6),
            "asset_contribution_shares": contribution_shares,
            "concentration": {key: full.get(key) for key in ("distinct_symbols", "top1_net_share", "top3_net_share", "top5_net_share", "top_industry_gross_share", "max_symbol_loss_to_gross")},
            "fresh_starts": phase_results,
            "positive_fresh_starts": positive_phases,
            "robustness": robust,
            "performance_gates": performance_gates,
            "concentration_gates": concentration_gates,
            "fresh_start_gate": fresh_gates,
            "robustness_gates": robust_gates,
            "all_pass": all_pass,
        }

    def r324_slice(curve: pl.DataFrame) -> pl.DataFrame:
        return curve.filter(pl.col("date").is_between(RECENT_START, END)).sort("date")

    overlay_results = {}
    for lookback in LOOKBACKS:
        for target in TARGETS:
            name = f"L{lookback}_T{round(target * 100)}"
            overlay_results[name] = evaluate(name, lookback, target, 0.15, "overlay_grid")
    joint_results = {}
    for target in TARGETS:
        name = f"joint_T{round(target * 100)}"
        if target == 0.15:
            joint_results[name] = overlay_results["L60_T15"]
        else:
            joint_results[name] = evaluate(name, 60, target, target, "joint_target_grid")

    overlay_passes = sum(1 for item in overlay_results.values() if item["all_pass"])
    joint_passes = sum(1 for item in joint_results.values() if item["all_pass"])
    center = overlay_results["L60_T15"]
    r332 = json.loads(R332_RESULT.read_text(encoding="utf-8"))
    r333 = json.loads(R333_RESULT.read_text(encoding="utf-8"))
    if r333["summary"]["plateau_pass"]:
        raise RuntimeError("R334 requires the documented R333 fragility result")
    plateau_pass = overlay_passes >= 7 and joint_passes >= 2 and center["all_pass"]
    payload = {
        "round": "R333",
        "classification": "post-hoc equity-cap structural repair with the R333 parameter-neighborhood audit; no parameter selection",
        "preregistration_sha256": sha256(PREREG),
        "r332_result_sha256": sha256(R332_RESULT),
        "r333_result_sha256": sha256(R333_RESULT),
        "asset_manifest_sha256": sha256(ASSET_MANIFEST),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "periods": {"full": [str(START), str(END)], "recent_year": [str(RECENT_START), str(END)]},
        "overlay_grid": overlay_results,
        "joint_target_grid": joint_results,
        "summary": {
            "overlay_variants": len(overlay_results),
            "overlay_passes": overlay_passes,
            "joint_variants": len(joint_results),
            "joint_passes": joint_passes,
            "center_all_pass": center["all_pass"],
            "plateau_pass": plateau_pass,
            "center_remains_selected": True,
            "equity_risk_share_max": 1.0 / 3.0,
            "selected": "r334_top10_three_asset_equity_cap33_posthoc" if plateau_pass else None,
            "status": "candidate_equity_cap33_plateau_confirmed" if plateau_pass else "research_only_equity_cap33_fragile",
            "paper_review_status": "candidate_for_paper_trading_review" if plateau_pass else "downgrade_to_research_only",
            "delta_vs_r332_center": {
                "annual": center["stats"]["annual"] - r332["primary_stats"]["annual"],
                "sharpe": center["stats"]["sharpe"] - r332["primary_stats"]["sharpe"],
                "mdd": center["stats"]["mdd"] - r332["primary_stats"]["mdd"],
                "return_2022": center["yearly"].get("2022", 0.0) - r332["primary_yearly"].get("2022", 0.0),
            },
            "production_write": False,
            "parameter_selection": False,
        },
        "limits": json.loads(PREREG.read_text(encoding="utf-8"))["known_limitations"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "summary": payload["summary"],
        "overlay": {
            name: {
                "annual": item["stats"]["annual"],
                "sharpe": item["stats"]["sharpe"],
                "mdd": item["stats"]["mdd"],
                "2022": item["yearly"].get("2022"),
                "2023": item["yearly"].get("2023"),
                "pass": item["all_pass"],
            }
            for name, item in overlay_results.items()
        },
        "joint": {
            name: {
                "annual": item["stats"]["annual"],
                "sharpe": item["stats"]["sharpe"],
                "mdd": item["stats"]["mdd"],
                "2022": item["yearly"].get("2022"),
                "2023": item["yearly"].get("2023"),
                "pass": item["all_pass"],
            }
            for name, item in joint_results.items()
        },
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
