# 输入说明: R88 — 股权激励计划草案/首次授予事件漂移检验。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r88_equity_incentive_screen.py
# 结果 JSON: 同目录 r88_equity_incentive_screen.json。原始公告与事件缓存 data/research/r88_equity_incentive/ (不提交)。
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
r88_probe = _load("r88_probe", "script_r88_equity_incentive_probe.py")
DATA = r42.DATA
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
PERMUTATIONS = r42.PERMUTATIONS
ROUND_TRIP_2X = 0.0028
HORIZONS = (5, 20, 60)
PRIMARY_HORIZON = 20
EVENT_TYPES = ("equity_plan_launch", "equity_first_grant")
MIN_EVENTS_TOTAL = 50
MIN_SYMBOLS_TOTAL = 30
MIN_EVENTS_SEGMENT = 15
MIN_SYMBOLS_SEGMENT = 10
MIN_COVERAGE = {5: 0.95, 20: 0.95, 60: 0.85}


def _membership_filter(events: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    snapshots = pl.read_parquet(DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet").select([
        pl.col("query_date").alias("membership_date"),
        pl.col("symbol"),
    ]).sort(["symbol", "membership_date"])
    joined = (
        events.sort(["symbol", "notice_date"])
        .join_asof(
            snapshots,
            left_on="notice_date",
            right_on="membership_date",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        )
        .with_columns(pl.col("membership_date").is_not_null().alias("in_hs300"))
    )
    analysis = joined.filter(
        pl.col("in_hs300")
        & (pl.col("notice_date") >= DISCOVERY[0])
        & (pl.col("notice_date") <= VALIDATION[1])
    )
    audit = {
        "classified_events": events.height,
        "membership_matched": int(joined["in_hs300"].sum()),
        "analysis_events": analysis.height,
        "analysis_symbols": analysis["symbol"].n_unique() if analysis.height else 0,
        "events_before_discovery": joined.filter(pl.col("in_hs300") & (pl.col("notice_date") < DISCOVERY[0])).height,
        "events_after_validation": joined.filter(pl.col("notice_date") > VALIDATION[1]).height,
    }
    return analysis, audit


def _event_returns(events: pl.DataFrame, panel: pl.DataFrame) -> pl.DataFrame:
    index_paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    index = (
        pl.scan_parquet([str(path) for path in index_paths], extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
        .unique(subset=["date"], keep="last")
        .with_row_index("market_idx")
        .rename({"close": "index_close"})
    )
    entry_market = index.select([
        pl.col("date").alias("entry_search_date"),
        pl.col("date").alias("entry_date"),
        "market_idx",
        pl.col("index_close").alias("index_entry_close"),
    ]).sort("entry_search_date")
    prepared = events.with_columns(
        pl.col("notice_date").dt.offset_by("1d").alias("entry_search_date")
    ).sort("entry_search_date")
    joined = prepared.join_asof(
        entry_market,
        on="entry_search_date",
        strategy="forward",
        check_sortedness=False,
    )
    stock = panel.select(["symbol", "date", "close"]).unique(
        subset=["symbol", "date"], keep="last"
    )
    joined = joined.join(
        stock.rename({"date": "entry_date", "close": "stock_entry_close"}),
        on=["symbol", "entry_date"],
        how="left",
    )
    for horizon in HORIZONS:
        exits = index.select([
            (pl.col("market_idx") - horizon).alias("entry_market_idx"),
            pl.col("date").alias(f"exit_date_{horizon}"),
            pl.col("index_close").alias(f"index_exit_close_{horizon}"),
        ])
        joined = joined.join(
            exits,
            left_on="market_idx",
            right_on="entry_market_idx",
            how="left",
        ).join(
            stock.select([
                "symbol",
                pl.col("date").alias(f"exit_date_{horizon}"),
                pl.col("close").alias(f"stock_exit_close_{horizon}"),
            ]),
            on=["symbol", f"exit_date_{horizon}"],
            how="left",
        )
    joined = joined.with_columns([
        pl.when(
            pl.col("stock_entry_close").is_not_null()
            & pl.col(f"stock_exit_close_{h}").is_not_null()
            & pl.col(f"index_exit_close_{h}").is_not_null()
        )
        .then(pl.col(f"stock_exit_close_{h}") / pl.col("stock_entry_close") - 1.0)
        .otherwise(None)
        .alias(f"stock_car_{h}")
        for h in HORIZONS
    ]).with_columns([
        pl.when(pl.col(f"stock_car_{h}").is_not_null())
        .then(
            (1.0 + pl.col(f"stock_car_{h}"))
            / (1.0 + (pl.col(f"index_exit_close_{h}") / pl.col("index_entry_close") - 1.0))
            - 1.0
        )
        .otherwise(None)
        .alias(f"market_adj_car_{h}")
        for h in HORIZONS
    ]).with_columns([
        (pl.col(f"stock_car_{h}") - (pl.col(f"index_exit_close_{h}") / pl.col("index_entry_close") - 1.0)).alias(f"index_car_{h}")
        for h in HORIZONS
    ])
    return joined.sort(["event_type", "notice_date", "symbol"])


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


def _horizon_stats(frame: pl.DataFrame, event_type: str, horizon: int, seed: int) -> dict:
    part = frame.filter(pl.col("event_type") == event_type)
    col = f"market_adj_car_{horizon}"
    values = part[col].drop_nulls().to_list()
    raw_values = part[f"stock_car_{horizon}"].drop_nulls().to_list()
    index_values = part[f"index_car_{horizon}"].drop_nulls().to_list()
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
        "index_mean": round(float(mean(index_values)), 8) if index_values else None,
        "mean_net_2x": round(float(mean(values)) - ROUND_TRIP_2X, 8) if values else None,
        "sign_flip_p": _sign_flip_p(values, seed),
    }


def _year_report(frame: pl.DataFrame, event_type: str) -> list[dict]:
    part = frame.filter(
        (pl.col("event_type") == event_type)
        & pl.col(f"market_adj_car_{PRIMARY_HORIZON}").is_not_null()
    ).with_columns(pl.col("notice_date").dt.year().alias("year"))
    if part.is_empty():
        return []
    return (
        part.group_by("year").agg([
            pl.len().alias("events"),
            pl.col(f"market_adj_car_{PRIMARY_HORIZON}").mean().alias("mean_car"),
            pl.col(f"market_adj_car_{PRIMARY_HORIZON}").median().alias("median_car"),
        ])
        .sort("year").to_dicts()
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


def _bh_qvalues(pvalues: list[float | None]) -> list[float | None]:
    clean = [(index, value) for index, value in enumerate(pvalues) if value is not None]
    out: list[float | None] = [None] * len(pvalues)
    if not clean:
        return out
    ordered = sorted(clean, key=lambda item: item[1])
    running = 1.0
    count = len(ordered)
    for rank in range(count, 0, -1):
        index, value = ordered[rank - 1]
        running = min(running, value * count / rank)
        out[index] = running
    return out


def main() -> int:
    symbols = sorted(
        pl.read_parquet(DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet")["symbol"].unique().to_list()
    )
    raw, manifest = r88_probe._ensure_cache(symbols)
    classified = r88_probe._classify_frame(raw)
    events = r88_probe._event_frame(classified)
    analysis_events, membership_audit = _membership_filter(events)
    panel, _legacy_reb, _legacy_memberships = r42.build_panel()
    returns = _event_returns(analysis_events, panel)
    returns.write_parquet(DATA / "research" / "r88_equity_incentive" / "event_returns.parquet")

    banned = ("修订", "调整", "说明", "登记", "完成", "解锁", "行权", "归属", "条件成就", "解除限售", "上市流通", "注销", "取消", "终止", "结果", "更正", "自查")
    classifier_audit = {
        "plan_banned_titles": events.filter(
            (pl.col("event_type") == "equity_plan_launch")
            & pl.col("first_title").str.contains("|".join(banned))
        ).height,
        "grant_banned_titles": events.filter(
            (pl.col("event_type") == "equity_first_grant")
            & pl.col("first_title").str.contains("|".join(banned))
        ).height,
        "manual_template_corrections": 6,
    }
    field_completeness = {
        "rows": raw.height,
        "title_nonnull": int(raw["title"].is_not_null().sum()) if raw.height else 0,
        "notice_date_nonnull": int(raw["notice_date"].is_not_null().sum()) if raw.height else 0,
        "url_nonnull": int(raw["url"].is_not_null().sum()) if raw.height else 0,
    }
    source_ok = bool(
        manifest["failure_rate"] <= 0.10
        and raw.height > 0
        and field_completeness["title_nonnull"] / raw.height >= 0.99
        and field_completeness["notice_date_nonnull"] / raw.height >= 0.99
        and field_completeness["url_nonnull"] / raw.height >= 0.99
        and raw["notice_date"].max() >= date(2026, 8, 31)
    )

    event_records = {}
    discovery_pvalues = []
    for event_index, event_type in enumerate(EVENT_TYPES):
        disc = _segment(returns, "discovery").filter(pl.col("event_type") == event_type)
        val = _segment(returns, "validation").filter(pl.col("event_type") == event_type)
        stats = {}
        for segment_name, frame in (("discovery", disc), ("validation", val)):
            stats[segment_name] = {}
            for horizon_index, horizon in enumerate(HORIZONS):
                stats[segment_name][str(horizon)] = _horizon_stats(
                    frame,
                    event_type,
                    horizon,
                    RNG_SEED + event_index * 100 + horizon_index + (0 if segment_name == "discovery" else 1000),
                )
        total = returns.filter(pl.col("event_type") == event_type)
        d1 = bool(
            source_ok
            and total.height >= MIN_EVENTS_TOTAL
            and total["symbol"].n_unique() >= MIN_SYMBOLS_TOTAL
            and disc.height >= MIN_EVENTS_SEGMENT
            and disc["symbol"].n_unique() >= MIN_SYMBOLS_SEGMENT
            and val.height >= MIN_EVENTS_SEGMENT
            and val["symbol"].n_unique() >= MIN_SYMBOLS_SEGMENT
            and all(
                stats[segment][str(horizon)]["coverage"] >= MIN_COVERAGE[horizon]
                for segment in ("discovery", "validation")
                for horizon in HORIZONS
            )
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
        concentration = _concentration(returns, event_type)
        f4 = bool(
            concentration["max_symbol_share"] is not None
            and concentration["max_symbol_share"] <= 0.20
            and concentration["max_month_share"] is not None
            and concentration["max_month_share"] <= 0.25
        )
        d1_checks = {
            "source_ok": source_ok,
            "total_events_ge_50": total.height >= MIN_EVENTS_TOTAL,
            "total_symbols_ge_30": total["symbol"].n_unique() >= MIN_SYMBOLS_TOTAL,
            "discovery_events_ge_15": disc.height >= MIN_EVENTS_SEGMENT,
            "discovery_symbols_ge_10": disc["symbol"].n_unique() >= MIN_SYMBOLS_SEGMENT,
            "validation_events_ge_15": val.height >= MIN_EVENTS_SEGMENT,
            "validation_symbols_ge_10": val["symbol"].n_unique() >= MIN_SYMBOLS_SEGMENT,
            "coverage": {
                f"{segment}_{horizon}d": bool(
                    stats[segment][str(horizon)]["coverage"] >= MIN_COVERAGE[horizon]
                )
                for segment in ("discovery", "validation")
                for horizon in HORIZONS
            },
        }
        event_records[event_type] = {
            "total_events": total.height,
            "total_symbols": total["symbol"].n_unique() if total.height else 0,
            "discovery_events": disc.height,
            "discovery_symbols": disc["symbol"].n_unique() if disc.height else 0,
            "validation_events": val.height,
            "validation_symbols": val["symbol"].n_unique() if val.height else 0,
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

    qvalues = _bh_qvalues(discovery_pvalues)
    passed = []
    for event_type, qvalue in zip(EVENT_TYPES, qvalues, strict=True):
        record = event_records[event_type]
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
            "cost_round_trip_2x": ROUND_TRIP_2X,
            "permutations": PERMUTATIONS,
            "seed": RNG_SEED,
        },
        "data": {
            "fetch": manifest,
            "raw_announcements": raw.height,
            "classified_documents": classified.height,
            "unique_events": events.height,
            "membership": membership_audit,
            "fields": field_completeness,
            "classifier_audit": classifier_audit,
            "latest_notice_date": str(raw["notice_date"].max()) if raw.height else None,
        },
        "events": event_records,
        "passed": passed,
        "adopted_for_r89": passed,
        "decision": {
            "source_ok": source_ok,
            "D1_count": sum(bool(event_records[name]["D1"]) for name in EVENT_TYPES),
            "F1_count": sum(bool(event_records[name]["F1"]) for name in EVENT_TYPES),
            "F2_count": sum(bool(event_records[name]["F2"]) for name in EVENT_TYPES),
            "F3_count": sum(bool(event_records[name]["F3"]) for name in EVENT_TYPES),
            "F4_count": sum(bool(event_records[name]["F4"]) for name in EVENT_TYPES),
            "all_pass_count": len(passed),
        },
        "production_state": "pause_production",
        "recommendation": "R89 event overlay validation" if passed else "negative knowledge; equity-incentive event dimension closed; ESOP remains data gap",
        "caveats": [
            "公告分类依赖 CNInfo 标题，事件日在公告日之后第一个交易日收盘，未使用盘中成交",
            "未做行业中性和 beta 调整；主 CAR 只对 HS300 做市场调整",
            "部分事件窗口在 2026-10-14 前不完整，按覆盖门槛处理，不回填",
            "员工持股计划没有稳定历史分类覆盖，本轮不能下结论",
            "逐日成员来自 BaoStock 查询，不是中证指数原始调样公告",
        ],
    }
    compact = r42.r22.compact(result)
    canonical = json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    compact["sha256_canonical"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = EV / "r88_equity_incentive_screen.json"
    path.write_text(json.dumps(compact, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("decision", compact["decision"], "passed", passed, "sha", compact["sha256_canonical"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
