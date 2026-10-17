# 输入说明: R90 — 董监高真实竞价增持/减持事件漂移。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r90_insider_trade_screen.py
# 结果 JSON: 同目录 r90_insider_trade_screen.json。原始缓存 data/research/r90_insider_trade/ (不提交)。
from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path
from statistics import mean, median, pstdev

import numpy as np
import polars as pl

EV = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r42 = _load("r42", "script_r42_historical_factor_screen.py")
r88_screen = _load("r88_screen", "script_r88_equity_incentive_screen.py")
r90_probe = _load("r90_probe", "script_r90_insider_trade_probe.py")
DATA = r42.DATA
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
PERMUTATIONS = r42.PERMUTATIONS
ROUND_TRIP_2X = 0.0028
HORIZONS = (5, 20, 60)
PRIMARY_HORIZON = 20
EVENT_TYPES = ("insider_open_market_buy", "insider_open_market_sell")
MIN_COVERAGE = {5: 0.95, 20: 0.95, 60: 0.85}


def _event_frame(raw: pl.DataFrame) -> pl.DataFrame:
    events, audit = r90_probe._membership_events(raw)
    return (
        events.with_columns([
            pl.when(pl.col("direction") == "buy")
            .then(pl.lit("insider_open_market_buy"))
            .otherwise(pl.lit("insider_open_market_sell"))
            .alias("event_type"),
            pl.col("announce_date").alias("notice_date"),
        ])
        .sort(["event_type", "notice_date", "symbol"]),
        audit,
    )


def _segment(frame: pl.DataFrame, segment: str) -> pl.DataFrame:
    if segment == "discovery":
        return frame.filter(
            (pl.col("notice_date") >= DISCOVERY[0])
            & (pl.col("notice_date") <= DISCOVERY[1])
        )
    return frame.filter(
        (pl.col("notice_date") >= VALIDATION[0])
        & (pl.col("notice_date") <= VALIDATION[1])
    )


def _sign_flip_p(values: list[float], seed: int) -> float | None:
    if len(values) < 10:
        return None
    arr = np.asarray(values, dtype=float)
    observed = float(arr.mean())
    rng = np.random.default_rng(seed)
    signs = rng.choice(np.array([-1.0, 1.0]), size=(PERMUTATIONS, len(arr)))
    means = (signs * arr).mean(axis=1)
    return float((1 + np.sum(means >= observed)) / (PERMUTATIONS + 1))


def _winsorized_mean(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = np.asarray(sorted(values), dtype=float)
    lo = float(np.quantile(ordered, 0.05))
    hi = float(np.quantile(ordered, 0.95))
    return float(np.clip(ordered, lo, hi).mean())


def _stats(frame: pl.DataFrame, event_type: str, horizon: int, seed: int) -> dict:
    part = frame.filter(pl.col("event_type") == event_type)
    sign = 1.0 if event_type == "insider_open_market_buy" else -1.0
    values = [
        sign * value
        for value in part[f"market_adj_car_{horizon}"].drop_nulls().to_list()
    ]
    raw_values = [
        sign * value
        for value in part[f"stock_car_{horizon}"].drop_nulls().to_list()
    ]
    return {
        "events": part.height,
        "valid": len(values),
        "coverage": round(len(values) / part.height, 6) if part.height else 0.0,
        "mean": round(float(mean(values)), 8) if values else None,
        "median": round(float(median(values)), 8) if values else None,
        "std": round(float(pstdev(values)), 8) if len(values) > 1 else None,
        "winsorized_mean": round(_winsorized_mean(values), 8) if values else None,
        "positive_rate": round(sum(value > 0 for value in values) / len(values), 6) if values else None,
        "raw_mean": round(float(mean(raw_values)), 8) if raw_values else None,
        "mean_net_2x": round(float(mean(values)) - ROUND_TRIP_2X, 8) if values else None,
        "sign_flip_p": _sign_flip_p(values, seed),
    }


def _year_report(frame: pl.DataFrame, event_type: str) -> list[dict]:
    sign = 1.0 if event_type == "insider_open_market_buy" else -1.0
    part = frame.filter(
        (pl.col("event_type") == event_type)
        & pl.col(f"market_adj_car_{PRIMARY_HORIZON}").is_not_null()
    ).with_columns([
        pl.col("notice_date").dt.year().alias("year"),
        (pl.col(f"market_adj_car_{PRIMARY_HORIZON}") * sign).alias("_oriented"),
    ])
    if part.is_empty():
        return []
    return (
        part.group_by("year").agg([
            pl.len().alias("events"),
            pl.col("_oriented").mean().alias("mean_car"),
            pl.col("_oriented").median().alias("median_car"),
        ]).sort("year").to_dicts()
    )


def _concentration(frame: pl.DataFrame, event_type: str) -> dict:
    part = frame.filter(pl.col("event_type") == event_type)
    if part.is_empty():
        return {"events": 0, "max_symbol_share": None, "max_month_share": None}
    symbol_counts = part.group_by("symbol").len().sort("len", descending=True)
    month_counts = (
        part.with_columns(pl.col("notice_date").dt.strftime("%Y-%m").alias("month"))
        .group_by("month").len().sort("len", descending=True)
    )
    return {
        "events": part.height,
        "symbols": part["symbol"].n_unique(),
        "max_symbol": symbol_counts.row(0, named=True),
        "max_symbol_share": round(symbol_counts["len"][0] / part.height, 6),
        "max_month": month_counts.row(0, named=True),
        "max_month_share": round(month_counts["len"][0] / part.height, 6),
    }


def main() -> int:
    raw, manifest = r90_probe._ensure_cache()
    events, membership_audit = _event_frame(raw)
    panel, _legacy_reb, _memberships = r42.build_panel()
    returns = r88_screen._event_returns(events, panel)
    returns.write_parquet(DATA / "research" / "r90_insider_trade" / "event_returns.parquet")
    field_completeness = {
        "rows": raw.height,
        "avg_price_nonnull": int(raw["avg_price"].is_not_null().sum()) if raw.height else 0,
        "change_qty_nonnull": int(raw["change_qty"].is_not_null().sum()) if raw.height else 0,
        "transaction_date_nonnull": int(raw["transaction_date"].is_not_null().sum()) if raw.height else 0,
    }
    source_ok = bool(
        manifest["failure_rate"] <= 0.10
        and raw.height > 0
        and field_completeness["avg_price_nonnull"] / raw.height >= 0.99
        and field_completeness["change_qty_nonnull"] / raw.height >= 0.99
    )

    records = {}
    discovery_pvalues = []
    for event_index, event_type in enumerate(EVENT_TYPES):
        disc = _segment(returns, "discovery").filter(pl.col("event_type") == event_type)
        val = _segment(returns, "validation").filter(pl.col("event_type") == event_type)
        total = returns.filter(pl.col("event_type") == event_type)
        stats = {}
        for segment_name, frame in (("discovery", disc), ("validation", val)):
            stats[segment_name] = {}
            for horizon_index, horizon in enumerate(HORIZONS):
                stats[segment_name][str(horizon)] = _stats(
                    frame,
                    event_type,
                    horizon,
                    RNG_SEED + event_index * 100 + horizon_index + (0 if segment_name == "discovery" else 1000),
                )
        concentration = _concentration(returns, event_type)
        d1_checks = {
            "source_ok": source_ok,
            "total_events_ge_500": total.height >= 500,
            "total_symbols_ge_100": total["symbol"].n_unique() >= 100,
            "discovery_events_ge_200": disc.height >= 200,
            "discovery_symbols_ge_50": disc["symbol"].n_unique() >= 50,
            "validation_events_ge_200": val.height >= 200,
            "validation_symbols_ge_50": val["symbol"].n_unique() >= 50,
            "coverage": {
                f"{segment}_{horizon}d": bool(
                    stats[segment][str(horizon)]["coverage"] >= MIN_COVERAGE[horizon]
                )
                for segment in ("discovery", "validation")
                for horizon in HORIZONS
            },
        }
        d1 = all(
            [d1_checks[key] for key in d1_checks if key != "coverage"]
            + list(d1_checks["coverage"].values())
        )
        disc20 = stats["discovery"][str(PRIMARY_HORIZON)]
        val20 = stats["validation"][str(PRIMARY_HORIZON)]
        f1 = bool(
            disc20["mean"] is not None and disc20["mean"] > ROUND_TRIP_2X
            and val20["mean"] is not None and val20["mean"] > ROUND_TRIP_2X
            and disc20["median"] is not None and disc20["median"] > 0
            and val20["median"] is not None and val20["median"] > 0
            and disc20["sign_flip_p"] is not None and disc20["sign_flip_p"] <= 0.05
            and val20["sign_flip_p"] is not None and val20["sign_flip_p"] <= 0.05
        )
        f2 = bool(
            all(
                stats[segment][str(horizon)]["mean"] is not None
                and stats[segment][str(horizon)]["mean"] > 0
                for segment in ("discovery", "validation")
                for horizon in HORIZONS
            )
            and disc20["mean_net_2x"] is not None and disc20["mean_net_2x"] > 0
            and val20["mean_net_2x"] is not None and val20["mean_net_2x"] > 0
            and disc20["winsorized_mean"] is not None and disc20["winsorized_mean"] > 0
            and val20["winsorized_mean"] is not None and val20["winsorized_mean"] > 0
        )
        f4 = bool(
            concentration["max_symbol_share"] is not None
            and concentration["max_symbol_share"] <= 0.10
            and concentration["max_month_share"] is not None
            and concentration["max_month_share"] <= 0.20
        )
        amount_values = total["estimated_amount"].drop_nulls().to_list()
        records[event_type] = {
            "direction": "high" if event_type == "insider_open_market_buy" else "low",
            "total_events": total.height,
            "total_symbols": total["symbol"].n_unique() if total.height else 0,
            "discovery_events": disc.height,
            "discovery_symbols": disc["symbol"].n_unique() if disc.height else 0,
            "validation_events": val.height,
            "validation_symbols": val["symbol"].n_unique() if val.height else 0,
            "event_amount": {
                "median": round(float(median(amount_values)), 4) if amount_values else None,
                "p90": round(float(np.quantile(amount_values, 0.90)), 4) if amount_values else None,
            },
            "stats": stats,
            "yearly_20d": _year_report(returns, event_type),
            "concentration": concentration,
            "D1": d1,
            "D1_checks": d1_checks,
            "F1": f1,
            "F2": f2,
            "F4": f4,
        }
        discovery_pvalues.append(disc20["sign_flip_p"])
        print(
            f"{event_type}: D1={d1} events={total.height}/{disc.height}/{val.height} "
            f"20d mean={disc20['mean']}/{val20['mean']} median={disc20['median']}/{val20['median']} "
            f"p={disc20['sign_flip_p']}/{val20['sign_flip_p']}",
            flush=True,
        )

    qvalues = r42._bh_qvalues(discovery_pvalues)
    passed = []
    for event_type, qvalue in zip(EVENT_TYPES, qvalues, strict=True):
        record = records[event_type]
        record["discovery_q"] = qvalue
        record["F3"] = bool(
            qvalue is not None and qvalue <= 0.10
            and record["stats"]["validation"][str(PRIMARY_HORIZON)]["sign_flip_p"] is not None
            and record["stats"]["validation"][str(PRIMARY_HORIZON)]["sign_flip_p"] <= 0.05
        )
        record["all_pass"] = bool(
            record["D1"] and record["F1"] and record["F2"] and record["F3"] and record["F4"]
        )
        if record["all_pass"]:
            passed.append(event_type)

    result = {
        "window": {
            "discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])],
            "validation": [str(VALIDATION[0]), str(VALIDATION[1])],
            "events": list(EVENT_TYPES),
            "horizons": list(HORIZONS),
            "primary_horizon": PRIMARY_HORIZON,
            "entry": "first market trading day after announcement; close",
            "car": "market-adjusted geometric: (1+stock_car)/(1+hs300_car)-1",
            "sell_orientation": "sell CAR multiplied by -1",
            "cost_round_trip_2x": ROUND_TRIP_2X,
            "permutations": PERMUTATIONS,
            "seed": RNG_SEED,
        },
        "data": {
            "fetch": manifest,
            "fields": field_completeness,
            "membership": membership_audit,
            "event_rows": events.height,
            "event_symbols": events["symbol"].n_unique() if events.height else 0,
        },
        "events": records,
        "passed": passed,
        "adopted_for_r91": passed,
        "decision": {
            "source_ok": source_ok,
            "D1_count": sum(bool(records[name]["D1"]) for name in EVENT_TYPES),
            "F1_count": sum(bool(records[name]["F1"]) for name in EVENT_TYPES),
            "F2_count": sum(bool(records[name]["F2"]) for name in EVENT_TYPES),
            "F3_count": sum(bool(records[name]["F3"]) for name in EVENT_TYPES),
            "F4_count": sum(bool(records[name]["F4"]) for name in EVENT_TYPES),
            "all_pass_count": len(passed),
        },
        "production_state": "pause_production",
        "recommendation": "R91 insider event overlay validation" if passed else "negative knowledge; insider open-market event dimension closed",
        "caveats": [
            "公告日为信息可得日，统一下一交易日收盘进入，未使用盘中成交",
            "主 CAR 只对 HS300 做市场调整，未做行业/beta 匹配",
            "董监高交易可能包含计划性减持、继承/司法处置等非信息交易，但已排除非竞价方式",
            "事件窗口末端不足的数据不回填",
            "逐日成员来自 BaoStock 查询，不是中证指数原始调样公告",
        ],
    }
    compact = r42.r22.compact(result)
    canonical = json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    compact["sha256_canonical"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = EV / "r90_insider_trade_screen.json"
    path.write_text(json.dumps(compact, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("decision", compact["decision"], "passed", passed, "sha", compact["sha256_canonical"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
