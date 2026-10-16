# 输入说明: 进程内直接调用 FactorBacktestService (与 /api/backtest/factor/* 同一代码路径), 无需启动后端。
# 原因: dev 服务 `uvicorn --reload` 会在 data/ 被写入时热重载, 使 HTTP 回测请求被中断 (2026-10-17 实测);
#      研究脚本改为进程内调用, 完全避开服务器与 reload, 结果与 API 路径一致。
# 结果 JSON: 与本脚本同目录的 r22_value_factors.json (compact: 只存摘要, 不存净值曲线)。
# 预注册见 LEDGER R22: 窗口 2021-06-01~2026-10-14, 门槛 D0/F1~F5, 2× 成本 = 佣金 4bp + 滑点 10bp。
import json
import math
import statistics as st
from dataclasses import asdict
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
REPO = EV.parents[5]
DATA = REPO / "data"
BASE = "http://127.0.0.1:3018"
START, END = date(2021, 6, 1), date(2026, 10, 14)

# 候选 -> 高值是否好 (经济学先验方向, 非拟合)
DIRECTION = {
    "pb_latest": False,
    "roe_latest": True,
    "gross_margin_latest": True,
    "net_margin_latest": True,
    "revenue_yoy_latest": True,
    "net_income_yoy_latest": True,
    "debt_ratio_latest": False,
    "uf_ep_roe_pb": True,
}
OFFLINE_DIRECTION = {
    "pb": False,
    "roe": True,
    "gross_margin": True,
    "net_margin": True,
    "revenue_yoy": True,
    "net_income_yoy": True,
    "debt_to_asset_ratio": False,
    "ep": True,
    "pb_pct": False,
}
FACTOR_TO_OFFLINE = {
    "pb_latest": "pb",
    "roe_latest": "roe",
    "gross_margin_latest": "gross_margin",
    "net_margin_latest": "net_margin",
    "revenue_yoy_latest": "revenue_yoy",
    "net_income_yoy_latest": "net_income_yoy",
    "debt_ratio_latest": "debt_to_asset_ratio",
    "uf_ep_roe_pb": "ep",
}
FEE_2X, SLIP_2X = 0.0004, 10.0
FEE_1X, SLIP_1X = 0.0002, 5.0
ROUND_TRIP_2X = 2.0 * FEE_2X + 2.0 * SLIP_2X / 10_000.0  # 与引擎 _round_trip_cost 同口径(印花税 0)


def build_service():
    """进程内构造与 API 相同的因子回测服务, 并载入磁盘上的自定义因子。"""
    from app.backtest.engine import BacktestEngine
    from app.backtest.factor import FactorBacktestService
    from app.factors.store import load_into_registry
    from app.tickflow.repository import DataStore, KlineRepository

    loaded = load_into_registry(DATA)
    service = FactorBacktestService(BacktestEngine(KlineRepository(DataStore())))
    return service, loaded


def run_factor(service, name: str, fee: float, slip: float) -> dict:
    from app.backtest.factor import FactorConfig

    return asdict(service.run(FactorConfig(
        factor_name=name, symbols=None, start=START, end=END,
        n_groups=5, rebalance="monthly", weight="equal",
        fees_pct=fee, slippage_bps=slip, asset_type="stock", hs300=True,
    )))


def run_batch(service, names: list[str], fee: float, slip: float) -> dict:
    from app.backtest.factor import FactorBatchConfig

    return asdict(service.run_batch(FactorBatchConfig(
        factor_names=names, symbols=None, start=START, end=END,
        n_groups=5, rebalance="monthly", weight="equal",
        fees_pct=fee, slippage_bps=slip, asset_type="stock", hs300=True,
    )))


def spearman(xs: list, ys: list) -> float:
    """Spearman 秩相关 (无 scipy 依赖; 并列值取平均秩)。"""
    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=False))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den > 0 else 0.0


def compact(value):
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items() if k not in ("ic_series", "group_nav", "long_short_nav")}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


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
    from app.indicators.pipeline import compute_indicators

    return compute_indicators(panel, needed={"annual_vol_20d"})


def attach_financials(panel: pl.DataFrame, symbols: list[str]) -> pl.DataFrame:
    fin = pl.read_parquet(DATA / "financials" / "metrics" / "part.parquet")
    fin = (
        fin.filter(pl.col("symbol").is_in(symbols) & pl.col("announce_date").is_not_null())
        .select(["symbol", "announce_date", "bps", "roe", "gross_margin", "net_margin",
                 "revenue_yoy", "net_income_yoy", "debt_to_asset_ratio"])
        .with_columns(
            pl.col("announce_date").cast(pl.Utf8).str.slice(0, 10).str.to_date().alias("_ann")
        )
        .with_columns(pl.col("_ann").dt.offset_by("1d").alias("_eff"))
        .sort(["symbol", "_eff"])
    )
    joined = panel.sort(["symbol", "date"]).join_asof(
        fin,
        left_on="date",
        right_on="_eff",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    )
    return joined.with_columns(
        pl.when((pl.col("bps") > 0) & pl.col("close").is_not_null())
        .then(pl.col("close") / pl.col("bps"))
        .otherwise(None)
        .alias("pb")
    ).with_columns(
        pl.when(pl.col("pb") > 0)
        .then(pl.col("roe") / (100.0 * pl.col("pb")))
        .otherwise(None)
        .alias("ep")
    )


def monthly_pairs(panel: pl.DataFrame) -> list[tuple[date, date]]:
    dates = sorted(panel["date"].unique().to_list())
    firsts, seen = [], set()
    for d in dates:
        key = str(d)[:7]
        if key not in seen:
            seen.add(key)
            firsts.append(d)
    return [(firsts[i], firsts[i + 1]) for i in range(len(firsts) - 1)]


def build_monthly(panel: pl.DataFrame, pairs: list[tuple[date, date]]) -> pl.DataFrame:
    reb = panel.filter(pl.col("date").is_in([a for a, _ in pairs]))
    nxt = panel.select([
        pl.col("symbol"),
        pl.col("date").alias("date_next"),
        pl.col("close").alias("close_next"),
    ])
    pairs_df = pl.DataFrame({
        "date": [a for a, _ in pairs],
        "date_next": [b for _, b in pairs],
    })
    reb = reb.join(pairs_df, on="date", how="inner").join(nxt, on=["symbol", "date_next"], how="left")
    return reb.with_columns((pl.col("close_next") / pl.col("close") - 1.0).alias("fwd"))


def add_pb_pct(reb: pl.DataFrame) -> pl.DataFrame:
    rows = reb.select(["symbol", "date", "pb"]).sort(["symbol", "date"])
    out: dict[tuple[str, date], float] = {}
    for keys, group in rows.group_by(["symbol"], maintain_order=True):
        vals = group["pb"].to_list()
        dates = group["date"].to_list()
        for i, value in enumerate(vals):
            if value is None:
                continue
            window = [x for x in vals[max(0, i - 35): i + 1] if x is not None]
            if len(window) >= 24:
                out[(keys[0], dates[i])] = sum(1 for x in window if x <= value) / len(window)
    if not out:
        return reb.with_columns(pl.lit(None, dtype=pl.Float64).alias("pb_pct"))
    pct = pl.DataFrame({
        "symbol": [k[0] for k in out],
        "date": [k[1] for k in out],
        "pb_pct": list(out.values()),
    })
    return reb.join(pct, on=["symbol", "date"], how="left")


def leg_columns(reb: pl.DataFrame) -> pl.DataFrame:
    return reb.with_columns([
        (0.0 - (pl.col("amount") + 1.0).log()).alias("leg_low_amount"),
        pl.when(pl.col("turnover_rate") > 0)
        .then(0.0 - (pl.col("close") * pl.col("volume") / pl.col("turnover_rate")).log())
        .otherwise(None)
        .alias("leg_small_mv"),
        (0.0 - pl.col("annual_vol_20d")).alias("leg_low_vol"),
    ])


def ic_frame(frame: pl.DataFrame, col: str) -> pl.DataFrame:
    d = frame.filter(pl.col(col).is_not_null() & pl.col("fwd").is_not_null())
    if d.is_empty():
        return pl.DataFrame({"date": [], "ic": [], "n": []})
    d = d.with_columns([
        pl.col(col).rank().over("date").alias("_x"),
        pl.col("fwd").rank().over("date").alias("_y"),
    ])
    return (
        d.group_by("date")
        .agg(pl.corr("_x", "_y").alias("ic"), pl.len().alias("n"))
        .filter(pl.col("n") >= 20)
        .sort("date")
    )


def ic_summary(frame: pl.DataFrame, col: str) -> dict | None:
    g = ic_frame(frame, col)
    ics = g["ic"].drop_nulls().to_list() if not g.is_empty() else []
    if len(ics) < 12:
        return None
    mean = st.mean(ics)
    sd = st.pstdev(ics)
    yearly: dict[int, list[float]] = {}
    for d, ic in zip(g["date"].to_list(), g["ic"].to_list(), strict=False):
        if ic is not None:
            yearly.setdefault(d.year, []).append(ic)
    return {
        "ic_mean": round(mean, 4),
        "ir": round(mean / sd, 4) if sd > 0 else None,
        "n_dates": len(ics),
        "yearly": {str(y): round(st.mean(v), 4) for y, v in sorted(yearly.items())},
    }


def mean_abs_corr(frame: pl.DataFrame, col: str, other: str) -> float | None:
    d = frame.filter(pl.col(col).is_not_null() & pl.col(other).is_not_null() & pl.col("fwd").is_not_null())
    if d.is_empty():
        return None
    d = d.with_columns([
        pl.col(col).rank().over("date").alias("_x"),
        pl.col(other).rank().over("date").alias("_y"),
    ])
    g = d.group_by("date").agg(pl.corr("_x", "_y").alias("r"), pl.len().alias("n")).filter(pl.col("n") >= 20)
    vals = [abs(v) for v in g["r"].drop_nulls().to_list()]
    return round(st.mean(vals), 4) if vals else None


def offline_groups(frame: pl.DataFrame, col: str, good_high: bool) -> dict | None:
    d = frame.filter(pl.col(col).is_not_null() & pl.col("fwd").is_not_null())
    if d.is_empty():
        return None
    d = d.with_columns(
        (
            pl.lit("Q")
            + ((pl.col(col).rank().over("date") - 1.0) * 5.0 / pl.len().over("date")).floor().clip(0, 4).cast(pl.Int64).add(1).cast(pl.Utf8)
        ).alias("g")
    )
    per_date = d.group_by(["date", "g"]).agg(pl.col("fwd").mean().alias("r")).sort(["date", "g"])
    means = per_date.group_by("g").agg(pl.col("r").mean().alias("m")).sort("g")
    if means.height < 5:
        return None
    monthly = means["m"].to_list()
    order = list(range(len(monthly)))
    rho = spearman(order, monthly) if len(set(monthly)) > 1 else 0.0
    oriented = monthly if good_high else list(reversed(monthly))
    spread_month = oriented[-1] - oriented[0]
    return {
        "group_monthly": [round(x, 5) for x in monthly],
        "monotonic_rho": round(rho, 3),
        "spread_month": round(spread_month, 5),
        "spread_annual_net2x": round((spread_month - ROUND_TRIP_2X) * 12.0, 4),
    }


def yearly_same_sign(yearly: dict, overall: float) -> int:
    sign = 1 if (overall or 0) >= 0 else -1
    return sum(
        1
        for y in ("2022", "2023", "2024", "2025", "2026")
        if yearly.get(y) is not None and (1 if yearly[y] >= 0 else -1) == sign
    )


def api_gates(item: dict, single_1x: dict, single_2x: dict, good_high: bool, max_corr: float | None) -> dict:
    yearly = {str(e["year"]): e["ic_mean"] for e in item.get("yearly_ic", [])}
    ic = item.get("ic_mean")
    f1 = bool(ic is not None and abs(ic) >= 0.03 and yearly_same_sign(yearly, ic) >= 4)

    def groups(result: dict) -> list[float] | None:
        stats = result.get("group_stats") or []
        by_group = {s["group"]: s["annual_return"] for s in stats}
        seq = [by_group.get(i) for i in range(1, 6)]
        if any(x is None for x in seq):
            return None
        return seq if good_high else list(reversed(seq))

    g1x, g2x = groups(single_1x), groups(single_2x)
    f2 = False
    if g1x is not None:
        rho = spearman(list(range(5)), g1x) if len(set(g1x)) > 1 else 0.0
        f2 = bool(rho >= 0.7 and (g1x[-1] - g1x[0]) > 0)
    f3 = bool(g2x is not None and (g2x[-1] - g2x[0]) > 0)
    f4 = bool(max_corr is not None and max_corr < 0.7)
    return {
        "F1_ic": f1,
        "F1_ic_mean": ic,
        "F1_yearly": yearly,
        "F2_monotonic_spread": f2,
        "F2_group_annual_1x": [round(x, 4) for x in g1x] if g1x else None,
        "F3_spread_2x": f3,
        "F3_group_annual_2x": [round(x, 4) for x in g2x] if g2x else None,
        "F4_max_abs_corr_v2": max_corr,
        "F4_corr_ok": f4,
        "all_pass": bool(f1 and f2 and f3 and f4),
    }


def main() -> int:
    members = json.loads((DATA / "hs300" / "current_members.json").read_text(encoding="utf-8"))["members"]
    symbols = [m["symbol"] for m in members]
    panel = attach_financials(load_panel(symbols), symbols)
    pairs = monthly_pairs(panel)
    reb = leg_columns(add_pb_pct(build_monthly(panel, pairs)))
    print(f"panel rows={panel.height}, rebalances={len(pairs)}, window={pairs[0][0]}~{pairs[-1][0]}", flush=True)

    service, loaded = build_service()
    print(f"in-process service ready, custom factors loaded: {len(loaded)}", flush=True)
    cf = DATA / "user_data" / "custom_factors" / "uf_ep_roe_pb.json"
    if not cf.exists():
        raise RuntimeError("缺少自定义因子 uf_ep_roe_pb.json (R22 前置), 先创建该文件")

    from app.factors.registry import get_factor

    names = list(DIRECTION)
    missing = [n for n in names if get_factor(n) is None]
    if missing:
        raise RuntimeError(f"因子未注册: {missing}")

    batch = run_batch(service, names, FEE_2X, SLIP_2X)
    items = {i["factor_name"]: i for i in batch.get("results", [])}
    singles = {}
    for name in names:
        for tag, fee, slip in (("1x", FEE_1X, SLIP_1X), ("2x", FEE_2X, SLIP_2X)):
            singles.setdefault(name, {})[tag] = run_factor(service, name, fee, slip)
        print(f"singles done: {name}", flush=True)

    # ---- 离线上限: 与 v2 三腿相关性 (财务列离线复刻) ----
    corr = {}
    for col in OFFLINE_DIRECTION:
        per_leg = {
            leg: mean_abs_corr(reb, col, leg)
            for leg in ("leg_low_amount", "leg_small_mv", "leg_low_vol")
        }
        corr[col] = {"per_leg": per_leg, "max": max((v for v in per_leg.values() if v is not None), default=None)}

    # ---- 离线上限: pb_pct IC 与分组 ----
    offline = {
        "pb_pct": {
            "ic": ic_summary(reb, "pb_pct"),
            "groups": offline_groups(reb, "pb_pct", good_high=False),
        },
        "ep": {
            "ic": ic_summary(reb, "ep"),
            "groups": offline_groups(reb, "ep", good_high=True),
        },
    }

    # ---- 离线上限: F5 低 PB vs 低 PB×高 ROE ----
    d = reb.filter(
        pl.col("pb").is_not_null() & (pl.col("pb") > 0) & pl.col("roe").is_not_null() & pl.col("fwd").is_not_null()
    )

    def portfolio_return(expr) -> tuple[float, int]:
        port = d.with_columns([
            pl.col("pb").rank().over("date").alias("_pr"),
            pl.col("roe").rank().over("date").alias("_rr"),
            pl.len().over("date").alias("_n"),
        ]).filter(expr)
        monthly = port.group_by("date").agg(pl.col("fwd").mean().alias("r")).sort("date")
        vals = monthly["r"].to_list()
        if not vals:
            return 0.0, 0
        total = 1.0
        for v in vals:
            total *= 1.0 + v
        return total ** (12.0 / len(vals)) - 1.0, len(vals)

    low_pb = pl.col("_pr") <= (pl.col("_n") / 3.0)
    high_roe = pl.col("_rr") >= (pl.col("_n") * 2.0 / 3.0)
    pb_annual, pb_n = portfolio_return(low_pb)
    qv_annual, qv_n = portfolio_return(low_pb & high_roe)
    f5 = bool(pb_n >= 24 and qv_n >= 24 and qv_annual > pb_annual)

    gates = {}
    for name, good_high in DIRECTION.items():
        item = items.get(name) or {}
        gates[name] = api_gates(item, singles[name]["1x"], singles[name]["2x"], good_high, corr.get(FACTOR_TO_OFFLINE.get(name, ""), {}).get("max"))
    def offline_gate(ic: dict | None, grp: dict | None, good_high: bool, max_corr: float | None, note: str) -> dict:
        rho_ok = bool(grp and ((grp["monotonic_rho"] >= 0.7) if good_high else (grp["monotonic_rho"] <= -0.7)))
        return {
            "F1_ic": bool(ic and abs(ic["ic_mean"]) >= 0.03 and yearly_same_sign(ic["yearly"], ic["ic_mean"]) >= 4),
            "F1_ic_mean": ic["ic_mean"] if ic else None,
            "F1_yearly": ic["yearly"] if ic else None,
            "F2_monotonic_spread": bool(rho_ok and grp and grp["spread_month"] > 0),
            "F2_group_monthly": grp["group_monthly"] if grp else None,
            "F3_spread_2x": bool(grp and grp["spread_annual_net2x"] > 0),
            "F3_spread_annual_net2x": grp["spread_annual_net2x"] if grp else None,
            "F4_max_abs_corr_v2": max_corr,
            "F4_corr_ok": bool(max_corr is not None and max_corr < 0.7),
            "offline_note": note,
        }

    for key, good_high, note in (
        ("pb_pct", False, "离线时间序列因子, F1~F3 用同一月度调仓口径离线计算"),
        ("ep", True, "引擎自定义因子引用 PIT 财务列失败(平台限制), 本行改用离线口径"),
    ):
        g = offline_gate(
            offline[key]["ic"], offline[key]["groups"], good_high,
            corr.get(key, {}).get("max"), note,
        )
        g["all_pass"] = bool(g["F1_ic"] and g["F2_monotonic_spread"] and g["F3_spread_2x"] and g["F4_corr_ok"])
        if key == "ep":
            gates["uf_ep_roe_pb"] = g
        else:
            gates["pb_pct"] = g

    result = {
        "window": {"start": str(START), "end": str(END), "n_rebalances": len(pairs)},
        "universe": {"hs300_members": len(symbols), "rebalances_first": str(pairs[0][0])},
        "batch": {
            "n_symbols": batch.get("n_symbols"),
            "n_dates": batch.get("n_dates"),
            "cost": {"fees_pct": FEE_2X, "slippage_bps": SLIP_2X},
            "items": {
                k: {
                    "ic_mean": v.get("ic_mean"), "ir": v.get("ir"), "ic_win_rate": v.get("ic_win_rate"),
                    "long_short_return_raw_q5q1": v.get("long_short_return"),
                    "long_short_sharpe_raw": v.get("long_short_sharpe"),
                    "long_short_max_drawdown_raw": v.get("long_short_max_drawdown"),
                    "coverage": v.get("coverage"), "turnover": v.get("turnover"), "error": v.get("error"),
                    "n_dates": v.get("n_dates"), "yearly_ic": v.get("yearly_ic"),
                }
                for k, v in items.items()
            },
        },
        "corr_v2": corr,
        "offline_pb_pct": offline["pb_pct"],
        "F5_portfolios": {
            "low_pb_annual": round(pb_annual, 4), "low_pb_months": pb_n,
            "low_pb_high_roe_annual": round(qv_annual, 4), "qv_months": qv_n,
            "F5_pass": f5,
        },
        "gates": gates,
        "all_pass_factors": sorted(k for k, v in gates.items() if v.get("all_pass")),
    }
    out = EV / "r22_value_factors.json"
    out.write_text(json.dumps(compact(result), ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", out)
    for name, g in gates.items():
        print(f"{name}: F1={g['F1_ic']} F2={g['F2_monotonic_spread']} F3={g['F3_spread_2x']} F4={g['F4_corr_ok']} all={g['all_pass']}")
    print("F5:", f5, "| lowPB", round(pb_annual, 4), "vs QV", round(qv_annual, 4))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
