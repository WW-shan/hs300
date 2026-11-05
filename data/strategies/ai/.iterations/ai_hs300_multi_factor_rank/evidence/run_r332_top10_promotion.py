from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import statistics as st
import tempfile
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
R40 = DATA / "research" / "r40_engine_data"
PREREG = EVIDENCE / "r332_top10_bucket_promotion_preregistration.json"
R331_RESULT = EVIDENCE / "r331_three_asset_bucket_v2.json"
ASSET_MANIFEST = EVIDENCE / "r331_asset_manifest.json"
RUN_CACHE = DATA / "research" / "r332_cache"
OUTPUT = EVIDENCE / "r332_top10_bucket_promotion.json"
START = date(2021, 6, 1)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
PHASES = [date(2022, 1, 4), date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), date(2025, 11, 5)]
MAX_POSITIONS = 10
MAIN_COST = 0.001
STRESS_COST = 0.003


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R332 result exists; refusing overwrite")
    for path in (PREREG, R331_RESULT, ASSET_MANIFEST):
        expected = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if sha256(path) != expected:
            raise RuntimeError(f"checksum mismatch: {path.name}")

    r331 = module(EVIDENCE / "run_r331_three_asset_bucket.py", "r331_r332")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r332")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r332")
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r332")
    r313 = module(EVIDENCE / "run_r313_concentration_audit.py", "r313_r332")
    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r332")
    r324 = module(EVIDENCE / "run_r324_defensive_ballast.py", "r324_r332")
    r326 = module(EVIDENCE / "run_r326_gold_concentration.py", "r326_r332")
    r331.self_test()
    r114.TARGET_VOL = 0.15
    members = r313.membership()
    manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    if not manifest["all_pass"]:
        raise RuntimeError("R331 asset manifest did not pass")
    for name, item in manifest["caches"].items():
        if sha256(ROOT / item["path"]) != item["sha256"]:
            raise RuntimeError(f"asset cache drift: {name}")

    before_r40 = r331.tree_hash(R40 / "kline_daily_enriched")
    before_prod = r331.tree_hash(DATA / "kline_daily_enriched")
    RUN_CACHE.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="r332_"))
    engine_root = work / "engine_data"
    shutil.copytree(R40, engine_root, symlinks=True)
    r40_latest = r331.latest_partition_date(R40 / "kline_daily_enriched")
    if r40_latest is None:
        raise RuntimeError("R40 enriched history is empty")
    for source in sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet")):
        partition_day = date.fromisoformat(source.parent.name.split("=")[1])
        if partition_day <= r40_latest:
            continue
        target = engine_root / "kline_daily_enriched" / source.parent.name / "part.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
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
    service = r324.StrategyBacktestService(r324.BacktestEngine(r324.KlineRepository(r324.DataStore(engine_root))), engine)

    def run(strategy_id: str, start: date, cost_factor: int, *, capture_trades: bool) -> tuple[pl.DataFrame, list[dict]]:
        key = f"{strategy_id}_{start}_{cost_factor}x"
        curve_path = RUN_CACHE / f"{key}.parquet"
        trades_path = RUN_CACHE / f"{key}.trades.json"
        if curve_path.exists():
            curve = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
            trades = json.loads(trades_path.read_text(encoding="utf-8")) if capture_trades else []
            if capture_trades and not trades_path.exists():
                raise RuntimeError(f"trade cache missing for {key}")
        else:
            cfg = r324.StrategyBacktestConfig(
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
                max_positions=MAX_POSITIONS,
                max_exposure_pct=1.0,
                position_sizing="equal",
                mode="position",
                asset_type="stock",
                hs300=True,
            )
            with r324.patched_membership(members):
                result = service.run(cfg)
            if result.error:
                raise RuntimeError(f"{strategy_id} {start} {cost_factor}x: {result.error}")
            curve = r324.curve_frame(result.equity_curve)
            curve.write_parquet(curve_path, compression="zstd")
            trades = []
            if capture_trades:
                trades = [{
                    "sleeve": "A" if strategy_id == r331.A_STRATEGY else "B",
                    "symbol": trade.get("symbol"),
                    "name": trade.get("name"),
                    "entry_date": str(trade.get("entry_date")),
                    "exit_date": str(trade.get("exit_date")),
                    "pnl_pct": trade.get("pnl_pct"),
                    "pnl_amount": float(trade.get("pnl_amount") or 0.0),
                    "exit_reason": trade.get("exit_reason"),
                } for trade in result.trades]
                trades_path.write_text(json.dumps(trades, ensure_ascii=False), encoding="utf-8")
            print(f"ran {strategy_id} {start} {cost_factor}x: {r112._stats(curve)}", flush=True)
        if curve["date"].max() != END:
            raise RuntimeError(f"{key} does not reach END: {curve['date'].max()}")
        if start == START and curve["date"].min() != START:
            raise RuntimeError(f"{key} does not start at START: {curve['date'].min()}")
        return curve, trades

    try:
        a2, a_trades = run(r331.A_STRATEGY, START, 2, capture_trades=True)
        b2, b_trades = run(r331.B_STRATEGY, START, 2, capture_trades=True)
        a3, _ = run(r331.A_STRATEGY, START, 3, capture_trades=False)
        b3, _ = run(r331.B_STRATEGY, START, 3, capture_trades=False)
        phase_curves = {}
        for phase in PHASES:
            pa, _ = run(r331.A_STRATEGY, phase, 2, capture_trades=False)
            pb, _ = run(r331.B_STRATEGY, phase, 2, capture_trades=False)
            phase_curves[phase] = (pa, pb)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = r331.tree_hash(R40 / "kline_daily_enriched")
    after_prod = r331.tree_hash(DATA / "kline_daily_enriched")
    risk2, rp2 = r112._combine(a2, b2, "risk")
    equity2 = r114._vol_target(risk2)["curve"]
    risk3, _ = r112._combine(a3, b3, "risk")
    equity3 = r114._vol_target(risk3)["curve"]
    primary = r331.build_three_asset_bucket(
        equity2,
        r331.asset_frame("gold", r331.PRIMARY["gold"]),
        r331.asset_frame("treasury", r331.PRIMARY["treasury"]),
        r331.asset_frame("dividend", r331.PRIMARY["dividend"]),
        round_trip_cost=MAIN_COST,
    )
    stress = r331.build_three_asset_bucket(
        equity3,
        r331.asset_frame("gold", r331.PRIMARY["gold"]),
        r331.asset_frame("treasury", r331.PRIMARY["treasury"]),
        r331.asset_frame("dividend", r331.PRIMARY["dividend"]),
        round_trip_cost=STRESS_COST,
    )
    robust = {}
    for name, symbols in r331.ROBUSTNESS.items():
        robust[name] = r331.build_three_asset_bucket(
            equity2,
            r331.asset_frame("gold", symbols["gold"]),
            r331.asset_frame("treasury", symbols["treasury"]),
            r331.asset_frame("dividend", symbols["dividend"]),
            round_trip_cost=MAIN_COST,
        )
    phases = {}
    for phase, (pa, pb) in phase_curves.items():
        phase_risk, _ = r112._combine(pa, pb, "risk")
        phase_equity = r114._vol_target(phase_risk)["curve"]
        phase_bucket = r331.build_three_asset_bucket(
            phase_equity,
            r331.asset_frame("gold", r331.PRIMARY["gold"]),
            r331.asset_frame("treasury", r331.PRIMARY["treasury"]),
            r331.asset_frame("dividend", r331.PRIMARY["dividend"]),
            round_trip_cost=MAIN_COST,
        )
        phases[str(phase)] = {
            "stats": r112._stats(phase_bucket["curve"]),
            "yearly": r315.year_returns(phase_bucket["curve"]),
            "curve_start": str(phase_bucket["curve"]["date"].min()),
            "curve_end": str(phase_bucket["curve"]["date"].max()),
        }

    curve = primary["curve"]
    values = curve["value"].to_list()
    daily_returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
    primary_stats = r112._stats(curve)
    primary_yearly = r315.year_returns(curve)
    recent_stats = r112._stats(r324.slice_curve(curve, RECENT_START, END))
    control_stats = r112._stats(equity2)
    bootstrap = r315.bootstrap(daily_returns)
    rolling = r315.rolling_returns(daily_returns, 252)
    rolling_positive = sum(value > 0 for value in rolling) / len(rolling)
    stress_stats = r112._stats(stress["curve"])
    robust_stats = {name: r112._stats(item["curve"]) for name, item in robust.items()}
    robust_yearly = {name: r315.year_returns(item["curve"]) for name, item in robust.items()}

    fin = {}
    for name in ("metrics", "income", "balance_sheet", "cash_flow"):
        frame = pl.read_parquet(DATA / "financials" / name / "part.parquet", columns=["period_end", "announce_date"])
        fin[name] = {
            "max_period_end": str(frame["period_end"].max()),
            "max_announce_date": str(frame["announce_date"].max()),
            "future_announce_rows": frame.filter(pl.col("announce_date") > str(END)).height,
        }
    membership = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))
    equity_latest = r331.latest_partition_date(DATA / "kline_daily_enriched")
    alt_latest = r331.latest_partition_date(DATA / "ext_data" / "alt_hs300" / "timeseries")
    industry_path = DATA / "ext_data" / "ext_hy_ths" / "part.parquet"
    data_gates = {
        "D1_asset_integrity": manifest["gates"]["D1_asset_integrity"] and primary["coverage"] >= 0.95,
        "D2_strategy_inputs": (
            equity_latest == END
            and alt_latest == END
            and all(item["max_period_end"] >= "2026-09-30" for item in fin.values())
            and membership.get("as_of") == str(END)
            and industry_path.exists()
        ),
        "D3_primary_liquidity": manifest["gates"]["D3_primary_liquidity"],
        "D4_curve_coverage": curve["date"].min() == START and curve["date"].max() == END,
    }
    performance_gates = {
        "G1_return": primary_stats["annual"] >= 0.06,
        "G2_drawdown": primary_stats["mdd"] >= -0.18,
        "G3_sharpe": primary_stats["sharpe"] >= 0.70,
        "G4_calendar_stability": primary_yearly.get("2022", -1.0) >= 0.0 and primary_yearly.get("2023", -1.0) >= 0.0,
        "G5_bootstrap": bootstrap["positive_rate"] >= 0.80 and bootstrap["return_p05"] >= -0.12,
        "G6_rolling_year": rolling_positive >= 0.70 and min(rolling) >= -0.15,
        "G7_recent_year": recent_stats["total_return"] > 0,
        "G8_stress": stress_stats["total_return"] > 0 and stress_stats["mdd"] >= -0.25,
        "G9_incremental": (
            (
                primary_stats["sharpe"] >= control_stats["sharpe"] + 0.05
                or primary_stats["mdd"] >= control_stats["mdd"] + 0.03
            )
            and primary_stats["annual"] >= control_stats["annual"] - 0.01
        ),
        "G10_no_leverage": (
            primary["weight_summary"]["max_weight_sum"] <= 1.0
            and primary["weight_summary"]["min_target_weight"] >= 0.0
        ),
    }
    robustness_gates = {
        name: (
            robust_stats[name]["annual"] >= 0.05
            and robust_stats[name]["mdd"] >= -0.20
            and robust_yearly[name].get("2022", -1.0) >= 0.0
            and robust_yearly[name].get("2023", -1.0) >= 0.0
        )
        for name in robust
    }

    curve_dates = risk2["date"].to_list()
    industry_frame = r313.load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}
    scaled = []
    for trade in [*a_trades, *b_trades]:
        entry = r326.parse_day(trade["entry_date"])
        raw_exit = trade.get("exit_date")
        exit_ = r326.parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
        days = [day for day in curve_dates if entry <= day <= exit_] or [entry]
        scale_values = []
        for day in days:
            equity_weight = r326.weight_at(primary["rebalances"], day, "target_equity_weight")
            sleeve_share = r326.weight_at(rp2, day, "weight_a")
            scale_values.append(equity_weight * (sleeve_share if trade["sleeve"] == "A" else 1.0 - sleeve_share))
        scale = sum(scale_values) / len(scale_values)
        scaled.append({**trade, "weight_scale": round(scale, 6), "pnl_amount": round(trade["pnl_amount"] * scale, 6)})
    full = r239._aggregate(scaled, industry)
    recent_trades = [trade for trade in scaled if r326.parse_day(trade["entry_date"]) >= RECENT_START]
    recent = r239._aggregate(recent_trades, industry)
    recent_positive = sum(1 for row in recent["per_symbol"] if row["pnl_amount"] > 0)
    gross_contributions = sum(max(value, 0.0) for value in primary["contributions"].values())
    contribution_shares = {
        key: round(value / gross_contributions, 6) if gross_contributions > 0 else None
        for key, value in primary["contributions"].items()
    }
    weights = primary["weight_summary"]
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
    positive_phases = sum(1 for item in phases.values() if item["stats"]["total_return"] > 0)
    promotion_gates = {
        "P1_stress": stress_stats["total_return"] > 0 and stress_stats["mdd"] >= -0.25,
        "P2_fresh_starts": positive_phases >= 4 and all(item["stats"]["mdd"] >= -0.18 for item in phases.values()),
        "P3_full_curve": curve["date"].min() == START and curve["date"].max() == END,
    }
    all_pass = (
        all(data_gates.values())
        and all(performance_gates.values())
        and all(robustness_gates.values())
        and all(concentration_gates.values())
        and all(promotion_gates.values())
    )
    r331_diag = json.loads(R331_RESULT.read_text(encoding="utf-8"))["diagnostic_top10"]["stats"]
    for key in ("total_return", "annual", "sharpe", "mdd"):
        if abs(float(primary_stats[key]) - float(r331_diag[key])) > 1e-4:
            raise RuntimeError(f"R331 Top10 parity failed for {key}: {primary_stats[key]} vs {r331_diag[key]}")
    payload = {
        "round": "R332",
        "classification": "post-hoc promotion validation of the R331 Top10 diagnostic; no parameter search",
        "preregistration_sha256": sha256(PREREG),
        "r331_result_sha256": sha256(R331_RESULT),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "periods": {"full": [str(START), str(END)], "recent_year": [str(RECENT_START), str(END)], "fresh_start_phases": [str(x) for x in PHASES]},
        "data": {
            "primary_assets": r331.PRIMARY,
            "robustness_assets": r331.ROBUSTNESS,
            "asset_manifest_sha256": sha256(ASSET_MANIFEST),
            "equity_enriched_latest": str(equity_latest),
            "alt_factors_latest": str(alt_latest),
            "membership_as_of": membership.get("as_of"),
            "financials": fin,
            "industry_snapshot_mtime": industry_path.stat().st_mtime,
        },
        "weights": weights,
        "turnover": {"annual": round(primary["annual_turnover"], 6), "total": round(primary["total_turnover"], 6)},
        "asset_contributions": {key: round(value, 6) for key, value in primary["contributions"].items()},
        "asset_contribution_shares": contribution_shares,
        "primary_stats": primary_stats,
        "primary_yearly": primary_yearly,
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
        "equity_only_top10_control": {"stats": control_stats, "yearly": r315.year_returns(equity2)},
        "stress_3x": {"stats": stress_stats, "weights": stress["weight_summary"], "turnover": round(stress["annual_turnover"], 6)},
        "robustness": {
            name: {"stats": robust_stats[name], "yearly": robust_yearly[name], "weights": robust[name]["weight_summary"]}
            for name in robust
        },
        "fresh_starts": phases,
        "positive_fresh_starts": positive_phases,
        "concentration_full": full,
        "concentration_recent_12m": recent,
        "recent_positive_symbols": recent_positive,
        "curve_dates": {"start": str(curve["date"].min()), "end": str(curve["date"].max()), "days": curve.height},
        "data_gates": data_gates,
        "performance_gates": performance_gates,
        "robustness_gates": robustness_gates,
        "concentration_gates": concentration_gates,
        "promotion_gates": promotion_gates,
        "selected": "top10_three_asset_bucket_posthoc" if all_pass else None,
        "decision": {
            "status": "candidate_for_paper_trading_review" if all_pass else "no_candidate_passed",
            "post_hoc": True,
            "paper_target_generated": False,
            "order_file_generated": False,
            "production_write": False,
            "provider_write": False,
            "parameter_search": False,
        },
        "input_isolation": {
            "r40_before": before_r40,
            "r40_after": after_r40,
            "production_before": before_prod,
            "production_after": after_prod,
            "stable": before_r40 == after_r40 and before_prod == after_prod,
        },
        "limits": json.loads(PREREG.read_text(encoding="utf-8"))["known_limitations"],
        "trades": scaled,
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "primary_stats": primary_stats,
        "primary_yearly": primary_yearly,
        "recent_year": recent_stats,
        "stress_3x": stress_stats,
        "robustness": {name: robust_stats[name] for name in robust},
        "robustness_gates": robustness_gates,
        "fresh_starts": {key: item["stats"] for key, item in phases.items()},
        "positive_fresh_starts": positive_phases,
        "weights": weights,
        "asset_contribution_shares": contribution_shares,
        "concentration": {key: full.get(key) for key in ("distinct_symbols", "top1_net_share", "top3_net_share", "top5_net_share", "top_industry_gross_share", "max_symbol_loss_to_gross")},
        "data_gates": data_gates,
        "performance_gates": performance_gates,
        "concentration_gates": concentration_gates,
        "promotion_gates": promotion_gates,
        "selected": payload["selected"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
