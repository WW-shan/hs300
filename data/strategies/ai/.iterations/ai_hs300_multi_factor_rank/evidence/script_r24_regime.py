# 输入说明: 进程内运行 (无需后端)。运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r24_regime.py
# 作用: R24 预注册 — state 五态前瞻收益 / 状态×风格 IC / v1 动量闸门 G1~G3。
# 结果 JSON: 同目录 r24_regime_stats.json。
# 纪律: 状态/分数均为 T 日收盘可得; 前瞻从 T+1 起算; 闸门在月调仓日收盘生效, 关闭期间持现金。
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
LAST_YEAR_START = date(2025, 10, 14)

_spec = importlib.util.spec_from_file_location("r22", EV / "script_r22_scan.py")
r22 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r22)

STYLES = {
    "overnight_mom": True,
    "amplitude_trend": True,
    "low_amount": True,
    "small_mv": True,
    "low_vol": True,
    "reversal_10d": False,  # 低 momentum_10d 好
    "low_pb": False,
}
GATES = {
    "G1_state_strong": ("state", "in", ("strong", "lean_strong")),
    "G2_score_60": ("score", ">=", 60),
    "G3_breadth_50": ("above_ma20_pct", ">=", 0.5),
}


def load_panel(symbols: list[str]) -> pl.DataFrame:
    lf = pl.scan_parquet(DATA / "kline_daily_enriched" / "date=*" / "part.parquet", extra_columns="ignore")
    panel = (
        lf.filter(
            pl.col("symbol").is_in(symbols)
            & (pl.col("date") >= date(2021, 1, 4))
            & (pl.col("date") <= END)
        )
        .select(["symbol", "date", "open", "high", "low", "close", "volume", "amount", "turnover_rate"])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date))
    )
    from app.backtest.factor import FactorBacktestService

    return FactorBacktestService._compute_missing_factors(
        panel, {"overnight_ret_20d", "amplitude_trend_20_60", "momentum_10d", "annual_vol_20d"}
    )


def load_regime() -> pl.DataFrame:
    return (
        pl.read_parquet(DATA / "regime_history" / "part.parquet")
        .select(["date", "state", "score", "above_ma20_pct", "limit_up", "seal_rate", "ge2_count",
                 "profit_score", "speculation_score", "resilience_score", "trend_score", "phase"])
        .with_columns(pl.col("date").cast(pl.Date))
        .sort("date")
    )


def forward_index_stats(regime: pl.DataFrame) -> dict:
    idx = (
        pl.scan_parquet(DATA / "kline_index_daily" / "date=*" / "part.parquet", extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    dates = idx["date"].to_list()
    closes = idx["close"].to_list()
    pos = {d: i for i, d in enumerate(dates)}
    rows = []
    for d, state in regime.select(["date", "state"]).iter_rows():
        i = pos.get(d)
        if i is None:
            continue
        rec = {"state": state}
        for horizon in (5, 20):
            if i + horizon < len(closes):
                rec[f"fwd{horizon}"] = closes[i + horizon] / closes[i] - 1.0
        rows.append(rec)
    df = pl.DataFrame(rows)
    out = {}
    for state, group in df.group_by("state"):
        key = state[0] if isinstance(state, tuple) else state
        rec = {"n": group.height}
        for horizon in (5, 20):
            vals = [v for v in group[f"fwd{horizon}"].drop_nulls().to_list()]
            if vals:
                rec[f"fwd{horizon}"] = {
                    "mean": round(st.mean(vals), 4),
                    "median": round(st.median(vals), 4),
                    "win_rate": round(sum(v > 0 for v in vals) / len(vals), 3),
                    "n": len(vals),
                }
        out[key] = rec
    return out


def state_style_ic(reb: pl.DataFrame) -> dict:
    ic_by_date = {}
    for style in STYLES:
        frame = r22.ic_frame(reb, style)
        ic_by_date[style] = dict(zip(frame["date"].to_list(), frame["ic"].to_list(), strict=False))
    out = {}
    for state, group in reb.group_by("state"):
        key = state[0] if isinstance(state, tuple) else state
        dates = group["date"].to_list()
        rec = {}
        for style, good_high in STYLES.items():
            vals = [ic for d, ic in ((d, ic_by_date[style].get(d)) for d in dates) if ic is not None]
            if len(vals) >= 12:
                mean = st.mean(vals)
                rec[style] = {
                    "oriented_ic": round(mean if good_high else -mean, 4),
                    "n_dates": len(vals),
                }
        out[key] = rec
    return out


def momentum_gate_test(reb: pl.DataFrame) -> dict:
    base = reb.filter(
        pl.col("overnight_ret_20d").is_not_null()
        & pl.col("amplitude_trend_20_60").is_not_null()
        & pl.col("fwd").is_not_null()
        & pl.col("state").is_not_null()
    ).with_columns([
        (pl.col("overnight_ret_20d").rank().over("date") / pl.len().over("date")).alias("_r1"),
        (pl.col("amplitude_trend_20_60").rank().over("date") / pl.len().over("date")).alias("_r2"),
    ]).with_columns((pl.col("_r1") + pl.col("_r2")).alias("_score"))
    top = base.filter(pl.col("_score").rank(method="ordinal", descending=True).over("date") <= 20).sort("date")

    def stats(frame: pl.DataFrame, mask_col: str | None = None) -> dict:
        rows = []
        for row in frame.iter_rows(named=True):
            active = True if mask_col is None else bool(row[mask_col])
            if not active:
                rows.append({"date": row["date"], "r": 0.0, "active": False})
                continue
            rows.append({"date": row["date"], "r": row["fwd"], "active": True})
        frame = pl.DataFrame(rows).sort("date")
        monthly = frame.group_by("date").agg([pl.col("r").mean().alias("r"), pl.col("active").max().alias("active")]).sort("date")
        vals = monthly["r"].to_list()
        actives = monthly["active"].to_list()
        if len(vals) < 12:
            return {}
        total = 1.0
        for v in vals:
            total *= 1.0 + v
        n = len(vals)
        sharpe = (st.mean(vals) / st.pstdev(vals) * (12 ** 0.5)) if st.pstdev(vals) > 0 else None
        peak = value = 1.0
        mdd = 0.0
        for v in vals:
            value *= 1.0 + v
            peak = max(peak, value)
            mdd = min(mdd, value / peak - 1.0)
        yearly: dict[str, float] = {}
        gprod: dict[str, float] = {}
        for d, v in zip(monthly["date"].to_list(), vals, strict=False):
            y = str(d.year)
            gprod[y] = gprod.get(y, 1.0) * (1.0 + v)
        yearly = {k: round(v - 1.0, 4) for k, v in sorted(gprod.items())}
        last_year = sum(v for d, v in zip(monthly["date"].to_list(), vals, strict=False) if d >= LAST_YEAR_START)
        return {
            "annual": round(total ** (12.0 / n) - 1.0, 4),
            "sharpe": round(sharpe, 3) if sharpe is not None else None,
            "mdd": round(mdd, 4),
            "yearly": yearly,
            "positive_years": sum(1 for v in yearly.values() if v > 0),
            "last_year_return": round(last_year, 4),
            "months": n,
            "months_active": sum(1 for a in actives if a),
        }

    ungated = stats(top)
    result = {"ungated": ungated, "gates": {}}
    for name, (col, op, value) in GATES.items():
        expr = pl.col(col).is_in(list(value)) if op == "in" else pl.col(col) >= value
        gated_frame = top.with_columns(expr.alias("_active"))
        g = stats(gated_frame, "_active")
        passed = bool(
            g and ungated.get("sharpe") is not None and g.get("sharpe") is not None
            and g["sharpe"] >= ungated["sharpe"] + 0.10
            and g["last_year_return"] >= ungated["last_year_return"]
            and g["positive_years"] >= ungated["positive_years"]
        )
        result["gates"][name] = {"stats": g, "pass": passed}
    return result


def main() -> int:
    members = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))["members"]
    symbols = [m["symbol"] for m in members]
    panel = r22.attach_financials(load_panel(symbols), symbols)
    panel = panel.with_columns([
        pl.col("overnight_ret_20d").alias("overnight_mom"),
        pl.col("amplitude_trend_20_60").alias("amplitude_trend"),
        pl.col("momentum_10d").alias("reversal_10d"),
        pl.when(pl.col("bps") > 0).then(pl.col("close") / pl.col("bps")).otherwise(None).alias("low_pb"),
    ])
    pairs = r22.monthly_pairs(panel)
    reb = r22.leg_columns(r22.build_monthly(panel, pairs))
    reb = reb.rename({"leg_low_amount": "low_amount", "leg_small_mv": "small_mv", "leg_low_vol": "low_vol"})
    regime = load_regime()
    reb = reb.join(regime, on="date", how="left")
    print(f"panel rows={panel.height}, rebalances={len(pairs)}, regime rows={regime.height}", flush=True)

    phase_counts = {
        str(k[0] if isinstance(k, tuple) else k): int(v)
        for k, v in zip(*[regime["phase"].value_counts().get_column(c) for c in ("phase", "count")], strict=False)
    } if False else {
        row["phase"]: row["count"] for row in regime["phase"].value_counts().sort("count", descending=True).iter_rows(named=True)
    }
    corr_score_breadth = float(
        regime.select(pl.corr("score", "above_ma20_pct")).item() or 0.0
    )

    result = {
        "window": {"start": str(START), "end": str(END), "n_rebalances": len(pairs)},
        "data_findings": {
            "phase_counts": phase_counts,
            "phase_usable": False,
            "state_counts": {row["state"]: row["count"] for row in regime["state"].value_counts().sort("count", descending=True).iter_rows(named=True)},
            "score_breadth_corr": round(corr_score_breadth, 3),
        },
        "state_forward_returns": forward_index_stats(regime),
        "state_style_ic": state_style_ic(reb),
        "momentum_gates": momentum_gate_test(reb),
    }
    out = EV / "r24_regime_stats.json"
    out.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", out)
    for state, rec in sorted(result["state_forward_returns"].items()):
        f5 = rec.get("fwd5", {})
        f20 = rec.get("fwd20", {})
        print(f"{state:12s} n={rec['n']:4d} fwd5 med={f5.get('median')} win={f5.get('win_rate')} | fwd20 med={f20.get('median')} win={f20.get('win_rate')}")
    mg = result["momentum_gates"]
    print("ungated:", mg["ungated"])
    for name, rec in mg["gates"].items():
        print(name, "pass=", rec["pass"], rec["stats"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
