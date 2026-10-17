# 输入说明: 进程内运行 (无需后端)。运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r35_financial_extra.py
# 作用: R35 — 修正 PIT 后补筛 roe_std8 / rev_accel / ni_accel / gm_yoy / int_cover。
# 结果 JSON: 同目录 r35_financial_extra.json。
import importlib.util
import json
from datetime import date
from pathlib import Path

import polars as pl

from app.backtest.fundamentals import (
    attach_fundamental_factors,
    financial_pit_date,
    load_fundamental_snapshot,
)

EV = Path(__file__).resolve().parent
DATA = EV.parents[5] / "data"
START, END = date(2021, 6, 1), date(2026, 10, 14)

FACTORS = {
    "roe_std8": False,
    "rev_accel": True,
    "ni_accel": True,
    "gm_yoy": True,
    "int_cover": True,
}
FLOWS = ("revenue", "net_income", "total_profit", "financial_expenses", "net_operating_cash_flow")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EV / f"script_{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r22 = _load("r22_scan")
r23 = _load("r23_screen")


def _read_pit(symbols: list[str], table: str, columns: list[str]) -> pl.DataFrame:
    frame = r23.read_table(symbols, table, columns)
    return frame.with_columns(
        pl.struct(["period_end", "announce"])
        .map_elements(
            lambda row: financial_pit_date(row["period_end"], row["announce"]),
            return_dtype=pl.Date,
        )
        .alias("announce")
    )


def _join_full(left: pl.DataFrame, right: pl.DataFrame, suffix: str) -> pl.DataFrame:
    joined = left.join(right, on=["symbol", "period_end"], how="full", suffix=suffix)
    expressions = []
    for key in ("symbol", "period_end"):
        suffixed = f"{key}{suffix}"
        if suffixed in joined.columns:
            expressions.append(pl.coalesce([pl.col(key), pl.col(suffixed)]).alias(key))
    drop_columns = [f"{key}{suffix}" for key in ("symbol", "period_end") if f"{key}{suffix}" in joined.columns]
    return joined.with_columns(expressions).drop(drop_columns) if expressions else joined


def build_financial(symbols: list[str]) -> pl.DataFrame:
    metrics = _read_pit(symbols, "metrics", [
        "bps", "roe", "gross_margin", "net_margin", "revenue", "revenue_yoy", "net_income", "net_income_yoy",
    ]).rename({"announce": "announce_metrics"})
    income = _read_pit(symbols, "income", ["total_profit", "financial_expenses"]).rename({"announce": "announce_income"})
    balance = _read_pit(symbols, "balance_sheet", ["total_assets", "total_liabilities", "total_equity"]).rename({"announce": "announce_balance"})
    cash = _read_pit(symbols, "cash_flow", ["net_operating_cash_flow"]).rename({"announce": "announce_cash"})
    fin = _join_full(metrics, income, "_inc")
    fin = _join_full(fin, balance, "_bal")
    fin = _join_full(fin, cash, "_cash").sort(["symbol", "period_end"])

    quarter = pl.col("period_end").dt.quarter()
    period_index = pl.col("period_end").dt.year() * 4 + quarter
    previous = period_index.shift(1).over("symbol")
    consecutive = (period_index - previous) == 1
    expressions = []
    for column in FLOWS:
        diff = pl.col(column) - pl.col(column).shift(1).over("symbol")
        expressions.append(
            pl.when(quarter == 1).then(pl.col(column))
            .when(consecutive).then(diff)
            .otherwise(None)
            .alias(f"q_{column}")
        )
    fin = fin.with_columns(expressions).with_columns([
        pl.col(f"q_{column}").rolling_sum(window_size=4, min_samples=4).over("symbol").alias(f"ttm_{column}")
        for column in FLOWS
    ])
    q_roe = (
        pl.when(pl.col("total_equity") > 0)
        .then(pl.col("q_net_income") / pl.col("total_equity"))
        .otherwise(None)
    )
    fin = fin.with_columns([
        q_roe.alias("_q_roe"),
        pl.when(period_index - period_index.shift(1).over("symbol") == 1)
        .then(pl.col("revenue_yoy") - pl.col("revenue_yoy").shift(1).over("symbol"))
        .otherwise(None).alias("rev_accel"),
        pl.when(period_index - period_index.shift(1).over("symbol") == 1)
        .then(pl.col("net_income_yoy") - pl.col("net_income_yoy").shift(1).over("symbol"))
        .otherwise(None).alias("ni_accel"),
        pl.when(period_index - period_index.shift(4).over("symbol") == 4)
        .then(pl.col("gross_margin") - pl.col("gross_margin").shift(4).over("symbol"))
        .otherwise(None).alias("gm_yoy"),
        pl.when(pl.col("ttm_financial_expenses").abs() > 0.01 * pl.col("ttm_total_profit").abs())
        .then(pl.col("ttm_total_profit") / pl.col("ttm_financial_expenses").abs())
        .otherwise(None).alias("int_cover"),
        pl.max_horizontal("announce_metrics", "announce_balance").alias("_eff_roe_std8"),
        pl.col("announce_income").alias("_eff_int_cover"),
    ])
    eight_quarter_gap = period_index - period_index.shift(7).over("symbol") == 7
    fin = fin.with_columns(
        pl.when(eight_quarter_gap)
        .then(pl.col("_q_roe").rolling_std(window_size=8, min_samples=8).over("symbol"))
        .otherwise(None).alias("roe_std8")
    )
    return fin


def attach_factors(panel: pl.DataFrame, fin: pl.DataFrame) -> pl.DataFrame:
    groups = {
        "announce_metrics": ["rev_accel", "ni_accel", "gm_yoy"],
        "_eff_roe_std8": ["roe_std8"],
        "_eff_int_cover": ["int_cover"],
    }
    out = panel.sort(["symbol", "date"])
    for pit_column, names in groups.items():
        right = (
            fin.select(["symbol", pit_column, *names])
            .filter(pl.col(pit_column).is_not_null())
            .with_columns(pl.col(pit_column).dt.offset_by("1d").alias("_effective"))
            .sort(["symbol", "_effective"])
        )
        out = out.join_asof(
            right,
            left_on="date",
            right_on="_effective",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        )
        out = out.with_columns([
            pl.when(pl.col(pit_column).is_not_null() & (pl.col("date") > pl.col(pit_column)))
            .then(pl.col(name)).otherwise(None).alias(name)
            for name in names
        ]).drop("_effective", pit_column)
    return out


def main() -> int:
    members = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))["members"]
    symbols = [m["symbol"] for m in members]
    fin = build_financial(symbols)
    panel = r23.load_panel(symbols).sort(["symbol", "date"])
    panel = attach_factors(panel, fin)
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("财务快照缺失")
    panel = attach_fundamental_factors(panel, snapshot, ["pb_latest", "roe_latest"])
    panel = panel.with_columns([
        pl.col("pb_latest").alias("pb"),
        pl.col("roe_latest").alias("roe"),
    ]).with_columns(
        pl.when(pl.col("pb") > 0)
        .then(pl.col("roe") / (100.0 * pl.col("pb")))
        .otherwise(None)
        .alias("ep")
    )
    pairs = r22.monthly_pairs(panel)
    reb = r22.leg_columns(r22.build_monthly(panel, pairs))
    print(f"panel rows={panel.height}, rebalances={len(pairs)}", flush=True)

    gates = {}
    for name, good_high in FACTORS.items():
        ic = r22.ic_summary(reb, name)
        grp = r22.offline_groups(reb, name, good_high=good_high)
        corr_v2 = max(
            (v for v in (r22.mean_abs_corr(reb, name, leg) for leg in ("leg_low_amount", "leg_small_mv", "leg_low_vol")) if v is not None),
            default=None,
        )
        corr_value = max(
            (v for v in (r22.mean_abs_corr(reb, name, other) for other in ("pb", "ep")) if v is not None),
            default=None,
        )
        rho_ok = bool(grp and ((grp["monotonic_rho"] >= 0.7) if good_high else (grp["monotonic_rho"] <= -0.7)))
        gates[name] = {
            "direction": "high" if good_high else "low",
            "coverage": round(float(reb[name].is_not_null().mean()), 4),
            "F1_ic": bool(ic and abs(ic["ic_mean"]) >= 0.03 and r22.yearly_same_sign(ic["yearly"], ic["ic_mean"]) >= 4),
            "F1_ic_mean": ic["ic_mean"] if ic else None,
            "F1_yearly": ic["yearly"] if ic else None,
            "F2_monotonic_spread": bool(rho_ok and grp and grp["spread_month"] > 0),
            "F2_monotonic_rho": grp["monotonic_rho"] if grp else None,
            "F2_spread_month": grp["spread_month"] if grp else None,
            "F3_spread_2x": bool(grp and grp["spread_annual_net2x"] is not None and grp["spread_annual_net2x"] > 0),
            "F3_spread_annual_net2x": grp["spread_annual_net2x"] if grp else None,
            "F4_max_abs_corr_v2": corr_v2,
            "F4_ok": bool(corr_v2 is not None and corr_v2 < 0.7),
            "F6_max_abs_corr_value": corr_value,
            "F6_ok": bool(corr_value is not None and corr_value < 0.7),
        }
        gates[name]["all_pass"] = bool(
            gates[name]["F1_ic"] and gates[name]["F2_monotonic_spread"]
            and gates[name]["F3_spread_2x"] and gates[name]["F4_ok"] and gates[name]["F6_ok"]
        )
        print(f"{name}: IC={gates[name]['F1_ic_mean']} cov={gates[name]['coverage']} F1={gates[name]['F1_ic']} "
              f"F2={gates[name]['F2_monotonic_spread']} F3={gates[name]['F3_spread_2x']} "
              f"F4={gates[name]['F4_max_abs_corr_v2']} F6={gates[name]['F6_max_abs_corr_value']} all={gates[name]['all_pass']}", flush=True)

    old_json = json.loads((EV / "r23_quality_growth.json").read_text(encoding="utf-8"))
    old = {
        name: {
            "ic_mean": old_json.get("gates", {}).get(name, {}).get("F1_ic_mean"),
            "all_pass": old_json.get("gates", {}).get(name, {}).get("all_pass"),
        }
        for name in FACTORS
    }
    all_pass = sorted(name for name, gate in gates.items() if gate["all_pass"])
    result = {
        "window": {"start": str(START), "end": str(END), "n_rebalances": len(pairs)},
        "gates": gates,
        "all_pass_factors": all_pass,
        "old_pit_comparison": old,
    }
    path = EV / "r35_financial_extra.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", path, flush=True)
    print("all_pass:", all_pass, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
