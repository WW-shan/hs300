# 输入说明: 进程内运行 (无需后端)。运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r25_sentiment.py
# 作用: R25 预注册 — 个股情绪因子 IC/分组/成本/相关性 + state 交互 + 拥挤度风险。
# 结果 JSON: 同目录 r25_sentiment_interaction.json。
import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
REPO = EV.parents[5]
DATA = REPO / "data"
START, END = date(2021, 6, 1), date(2026, 10, 14)

_spec = importlib.util.spec_from_file_location("r22", EV / "script_r22_scan.py")
r22 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r22)

FACTORS = [
    "turnover_z_60d", "vol_ratio_5d", "vol_ratio_10d", "amount_ratio_5d",
    "gap_return", "intraday_return", "close_position", "upper_shadow",
    "limit_up_count_20d", "limit_up_count_60d", "max_ret_20d", "up_days_20d",
    "vol_price_corr_20d", "obv_trend_20d",
]
CROWDING = ["turnover_z_60d", "vol_ratio_5d", "amount_ratio_5d"]


def load_panel(symbols: list[str]) -> pl.DataFrame:
    lf = pl.scan_parquet(DATA / "kline_daily_enriched" / "date=*" / "part.parquet", extra_columns="ignore")
    panel = (
        lf.filter(
            pl.col("symbol").is_in(symbols)
            & (pl.col("date") >= date(2021, 1, 4))
            & (pl.col("date") <= END)
        )
        .select(["symbol", "date", "open", "high", "low", "close", "volume", "amount", "turnover_rate", "consecutive_limit_ups"])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date))
    )
    from app.backtest.factor import FactorBacktestService

    needs = {f for f in FACTORS if f != "upper_shadow"} | {"annual_vol_20d"}
    panel = FactorBacktestService._compute_missing_factors(panel, needs)
    return panel.with_columns(
        pl.when((pl.col("high") - pl.col("low")) > 0)
        .then((pl.col("high") - pl.col("close")) / (pl.col("high") - pl.col("low")))
        .otherwise(None)
        .alias("upper_shadow")
    )


def interact(factor: str, good_high: bool, reb: pl.DataFrame) -> dict:
    ic = r22.ic_frame(reb, factor)
    by_date = dict(zip(ic["date"].to_list(), ic["ic"].to_list(), strict=False))
    out = {}
    for state, group in reb.group_by("state"):
        key = state[0] if isinstance(state, tuple) else state
        vals = [v for d in group["date"].to_list() if (v := by_date.get(d)) is not None]
        if len(vals) >= 12:
            mean = st.mean(vals)
            out[key] = {
                "oriented_ic": round(mean if good_high else -mean, 4),
                "n_dates": len(vals),
            }
    return out


def crowding(reb: pl.DataFrame) -> dict:
    out = {}
    for factor in CROWDING:
        d = reb.filter(pl.col(factor).is_not_null() & pl.col("fwd").is_not_null()).with_columns(
            (pl.col(factor).rank().over("date") / pl.len().over("date")).alias("_pct")
        )
        top = d.filter(pl.col("_pct") >= 0.8)
        # 组合口径: 每期 top 20% 等权收益, 再按 state 汇总 (worst = 组合最差月份, 不是单票)
        monthly = (
            top.group_by(["date", "state"])
            .agg(pl.col("fwd").mean().alias("r"))
            .sort("date")
        )
        per_state = {}
        for state in ("strong", "weak", "range", "lean_weak", "lean_strong"):
            vals = monthly.filter(pl.col("state") == state)["r"].to_list()
            vals = [v for v in vals if v is not None]
            if vals:
                per_state[state] = {
                    "n_months": len(vals),
                    "mean": round(st.mean(vals), 4),
                    "worst": round(min(vals), 4),
                    "best": round(max(vals), 4),
                }
        warn = False
        if "strong" in per_state and "weak" in per_state:
            warn = (per_state["strong"]["worst"] < -0.10) or (abs(per_state["strong"]["mean"] - per_state["weak"]["mean"]) > 0.02)
        out[factor] = {"by_state": per_state, "crowding_warning": warn}
    return out


def main() -> int:
    members = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))["members"]
    symbols = [m["symbol"] for m in members]
    panel = load_panel(symbols)
    pairs = r22.monthly_pairs(panel)
    reb = r22.leg_columns(r22.build_monthly(panel, pairs))
    regime = (
        pl.read_parquet(DATA / "regime_history" / "part.parquet")
        .select(["date", "state", "score", "above_ma20_pct"])
        .with_columns(pl.col("date").cast(pl.Date))
    )
    reb = reb.join(regime, on="date", how="left")
    print(f"panel rows={panel.height}, rebalances={len(pairs)}", flush=True)

    gates = {}
    for factor in FACTORS:
        ic = r22.ic_summary(reb, factor)
        if ic is None:
            gates[factor] = {"F1_ic": False, "note": "IC 样本不足"}
            continue
        good_high = ic["ic_mean"] >= 0
        grp = r22.offline_groups(reb, factor, good_high=good_high)
        corr_v2 = max(
            (v for v in (r22.mean_abs_corr(reb, factor, leg) for leg in ("leg_low_amount", "leg_small_mv", "leg_low_vol")) if v is not None),
            default=None,
        )
        rho_ok = bool(grp and ((grp["monotonic_rho"] >= 0.7) if good_high else (grp["monotonic_rho"] <= -0.7)))
        inter = interact(factor, good_high, reb)
        strong_states = [k for k, v in inter.items() if abs(v["oriented_ic"]) >= 0.03]
        sign_consistent = len({1 if v["oriented_ic"] >= 0 else -1 for k, v in inter.items() if k in strong_states}) <= 1
        if len(strong_states) >= 2 and sign_consistent:
            interaction_class = "跨状态稳定"
        elif len(strong_states) == 1:
            interaction_class = "条件化候选"
        elif len(strong_states) >= 2:
            interaction_class = "状态依赖反转"
        else:
            interaction_class = "无状态有效性"
        gates[factor] = {
            "F1_ic": bool(abs(ic["ic_mean"]) >= 0.03 and r22.yearly_same_sign(ic["yearly"], ic["ic_mean"]) >= 4),
            "F1_ic_mean": ic["ic_mean"],
            "F1_yearly": ic["yearly"],
            "direction": "high" if good_high else "low",
            "F2_monotonic_spread": bool(rho_ok and grp and grp["spread_month"] > 0),
            "F2_group_monthly": grp["group_monthly"] if grp else None,
            "F3_spread_2x": bool(grp and grp["spread_annual_net2x"] > 0),
            "F3_spread_annual_net2x": grp["spread_annual_net2x"] if grp else None,
            "F4_max_abs_corr_v2": corr_v2,
            "F4_ok": bool(corr_v2 is not None and corr_v2 < 0.7),
            "I1_interaction": interaction_class,
            "I1_by_state": inter,
        }
        gates[factor]["all_pass"] = bool(
            gates[factor]["F1_ic"] and gates[factor]["F2_monotonic_spread"]
            and gates[factor]["F3_spread_2x"] and gates[factor]["F4_ok"]
        )

    result = {
        "window": {"start": str(START), "end": str(END), "n_rebalances": len(pairs)},
        "gates": gates,
        "all_pass_factors": sorted(k for k, v in gates.items() if v.get("all_pass")),
        "conditional_factors": sorted(k for k, v in gates.items() if v.get("I1_interaction") == "条件化候选"),
        "crowding": crowding(reb),
    }
    out = EV / "r25_sentiment_interaction.json"
    out.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", out)
    for name, g in gates.items():
        print(f"{name:22s} IC={g.get('F1_ic_mean')} dir={g.get('direction')} F1={g.get('F1_ic')} F2={g.get('F2_monotonic_spread')} F3={g.get('F3_spread_2x')} F4={g.get('F4_max_abs_corr_v2')} I1={g.get('I1_interaction')}")
    print("all_pass:", result["all_pass_factors"], "| conditional:", result["conditional_factors"])
    print("crowding warnings:", {k: v["crowding_warning"] for k, v in result["crowding"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
