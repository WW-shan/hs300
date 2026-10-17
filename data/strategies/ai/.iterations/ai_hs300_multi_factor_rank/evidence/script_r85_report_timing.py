# 输入说明: R85 — 财报披露早晚因子复筛。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r85_report_timing.py
# 结果 JSON: 同目录 r85_report_timing.json。
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import polars as pl

EV = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r40 = _load("r40", "script_r40_survivorship.py")
r42 = _load("r42", "script_r42_historical_factor_screen.py")
r79 = _load("r79", "script_r79_exact_membership_b80.py")
r80 = _load("r80", "script_r80_financial_notice_audit.py")
DATA = r40.DATA
R80_JSON = EV / "r80_financial_notice_audit.json"
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
FACTORS = {
    "report_delay_pct": False,
    "report_delay_change_yoy": False,
}
MAX_AGE_DAYS = 200


def _timing_table() -> tuple[pl.DataFrame, dict]:
    universe = sorted(pl.read_parquet(r80.DAILY_MEMBERSHIP)["symbol"].unique().to_list())
    notices, _ = r80._ensure_cache(universe)
    periods, _ = r80._period_table(notices)
    periods = periods.filter(pl.col("first_notice_date").is_not_null()).with_columns([
        (pl.col("first_notice_date") - pl.col("period_end")).dt.total_days().alias("delay_days"),
        (pl.col("period_end").dt.year() * 4 + pl.col("period_end").dt.quarter()).alias("_period_index"),
    ]).sort(["symbol", "period_end"])
    periods = periods.with_columns(
        pl.when(pl.len().over("period_end") > 1)
        .then((pl.col("delay_days").rank().over("period_end") - 1.0) / (pl.len().over("period_end") - 1.0))
        .otherwise(None)
        .alias("report_delay_pct")
    ).with_columns([
        pl.col("delay_days").shift(4).over("symbol").alias("_delay_lag4"),
        pl.col("_period_index").shift(4).over("symbol").alias("_period_lag4"),
    ]).with_columns(
        pl.when((pl.col("_period_index") - pl.col("_period_lag4") == 4) & pl.col("_delay_lag4").is_not_null())
        .then(pl.col("delay_days") - pl.col("_delay_lag4"))
        .otherwise(None)
        .alias("report_delay_change_yoy")
    )
    audit = {
        "rows": periods.height,
        "symbols": periods["symbol"].n_unique(),
        "periods": periods["period_end"].n_unique(),
        "delay_median": float(periods["delay_days"].median()),
        "delay_p10": float(periods["delay_days"].quantile(0.1)),
        "delay_p90": float(periods["delay_days"].quantile(0.9)),
        "non_null": {factor: int(periods[factor].is_not_null().sum()) for factor in FACTORS},
        "by_type": periods.group_by("report_type").agg([
            pl.len().alias("rows"),
            pl.col("delay_days").median().alias("delay_median"),
        ]).sort("report_type").to_dicts(),
    }
    return periods, audit


def _base_rebalances() -> tuple[pl.DataFrame, dict]:
    from app.backtest.fundamentals import load_fundamental_snapshot

    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("财务快照缺失")
    membership, membership_audit = r79._exact_membership_frame()
    original = r40.membership_frame
    r40.membership_frame = lambda: (membership, membership_audit)
    try:
        _panel, reb, _memberships = r42.build_panel()
    finally:
        r40.membership_frame = original
    return reb, membership_audit


def _attach_timing(reb: pl.DataFrame, periods: pl.DataFrame) -> pl.DataFrame:
    out = reb.sort(["symbol", "date"])
    for factor in FACTORS:
        right = periods.select(["symbol", "first_notice_date", factor]).filter(
            pl.col(factor).is_not_null() & pl.col("first_notice_date").is_not_null()
        ).with_columns(pl.col("first_notice_date").dt.offset_by("1d").alias("_effective")).sort(
            ["symbol", "_effective"]
        )
        out = out.join_asof(
            right,
            left_on="date",
            right_on="_effective",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        ).with_columns(
            pl.when(
                pl.col("first_notice_date").is_not_null()
                & ((pl.col("date") - pl.col("first_notice_date")).dt.total_days() <= MAX_AGE_DAYS)
            ).then(pl.col(factor)).otherwise(None).alias(factor)
        ).drop(["first_notice_date", "_effective"])
    return out


def _screen(reb: pl.DataFrame) -> dict[str, Any]:
    discovery = r42._period_frame(reb, DISCOVERY)
    validation = r42._period_frame(reb, VALIDATION)
    records = {}
    pvalues = []
    for index, (factor, good_high) in enumerate(FACTORS.items()):
        sign = 1.0 if good_high else -1.0
        disc = discovery.with_columns((pl.col(factor) * sign).alias("_signed"))
        val = validation.with_columns((pl.col(factor) * sign).alias("_signed"))
        full = reb.with_columns((pl.col(factor) * sign).alias("_signed"))
        disc_stats = r42._ic_stats(disc, "_signed", RNG_SEED + index, one_sided=False)
        val_stats = r42._ic_stats(val, "_signed", RNG_SEED + 1000 + index, one_sided=True)
        full_stats = r42._ic_stats(full, "_signed", RNG_SEED + 2000 + index, one_sided=True)
        disc_group = r42._group_stats(disc, "_signed")
        val_group = r42._group_stats(val, "_signed")
        corr = max(
            (
                value for value in (
                    r42.r22.mean_abs_corr(full, "_signed", "pb_latest"),
                    r42.r22.mean_abs_corr(full, "_signed", "momentum_20d"),
                    r42.r22.mean_abs_corr(full, "_signed", "annual_vol_20d"),
                    r42.r22.mean_abs_corr(full, "_signed", "amount"),
                )
                if value is not None
            ),
            default=None,
        )
        d1 = bool(
            disc_stats["coverage"]["mean"] is not None and disc_stats["coverage"]["mean"] >= 0.70
            and val_stats["coverage"]["mean"] is not None and val_stats["coverage"]["mean"] >= 0.70
            and disc_stats["coverage"]["min_valid"] >= 80
            and val_stats["coverage"]["min_valid"] >= 80
        )
        f1 = bool(
            disc_stats["mean"] is not None and disc_stats["mean"] >= 0.03
            and val_stats["mean"] is not None and val_stats["mean"] >= 0.02
            and disc_stats["mean"] > 0 and val_stats["mean"] > 0
            and r42.r22.yearly_same_sign(full_stats["yearly"], full_stats["mean"] or 0) >= 4
        )
        f2 = bool(
            disc_group and val_group
            and disc_group["monotonic_rho"] >= 0.7 and val_group["monotonic_rho"] >= 0.7
            and disc_group["spread_annual_net2x"] > 0 and val_group["spread_annual_net2x"] > 0
        )
        records[factor] = {
            "direction": "high" if good_high else "low",
            "discovery": disc_stats,
            "validation": val_stats,
            "full": full_stats,
            "discovery_groups": disc_group,
            "validation_groups": val_group,
            "existing_corr_max": corr,
            "D1": d1,
            "F1": f1,
            "F2": f2,
        }
        pvalues.append(disc_stats["perm_p"])
    qvalues = r42._bh_qvalues(pvalues)
    passed = []
    for factor, q in zip(FACTORS, qvalues, strict=True):
        record = records[factor]
        record["discovery_q"] = q
        record["F3"] = bool(
            q is not None and q <= 0.10
            and record["validation"]["perm_p"] is not None
            and record["validation"]["perm_p"] <= 0.05
        )
        record["all_pass"] = bool(record["D1"] and record["F1"] and record["F2"] and record["F3"])
        if record["all_pass"]:
            passed.append(factor)
    return {
        "records": records,
        "passed": passed,
        "decision": {
            "D1_count": sum(bool(record["D1"]) for record in records.values()),
            "F1_count": sum(bool(record["F1"]) for record in records.values()),
            "F2_count": sum(bool(record["F2"]) for record in records.values()),
            "F3_count": sum(bool(record["F3"]) for record in records.values()),
            "all_pass_count": len(passed),
        },
    }


def main() -> int:
    r80_evidence = json.loads(R80_JSON.read_text(encoding="utf-8"))
    if not r80_evidence["decision"]["all_pass"]:
        raise RuntimeError("R80 公告日数据门槛未通过")
    periods, timing_audit = _timing_table()
    reb, membership_audit = _base_rebalances()
    reb = _attach_timing(reb, periods)
    result_screen = _screen(reb)
    result = {
        "window": {
            "discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])],
            "validation": [str(VALIDATION[0]), str(VALIDATION[1])],
            "factors": list(FACTORS),
            "permutations": r42.PERMUTATIONS,
            "seed": RNG_SEED,
            "max_age_days": MAX_AGE_DAYS,
        },
        "data": {"timing": timing_audit, "membership": membership_audit, "rebalances": reb.height, "rebalance_dates": reb.select("date").n_unique()},
        **result_screen,
        "adopted_for_r86": result_screen["passed"],
        "recommendation": "R86 portfolio/risk integration" if result_screen["passed"] else "negative knowledge; report timing line closed",
        "caveats": [
            "首次披露日来自东方财富公告大全, 不是交易所原始公告文件",
            "披露早晚只测信息时点, 不使用财报数值",
            "200 天有效期固定, 超期不向前填充",
            "历史成员为逐日精确 HS300 快照",
        ],
    }
    path = EV / "r85_report_timing.json"
    path.write_text(json.dumps(r40.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("timing", timing_audit, "membership", membership_audit, flush=True)
    print("decision", result_screen["decision"], "passed", result_screen["passed"], flush=True)
    print("records", {k: {"disc": v["discovery"]["mean"], "val": v["validation"]["mean"], "rho": [v["discovery_groups"]["monotonic_rho"] if v["discovery_groups"] else None, v["validation_groups"]["monotonic_rho"] if v["validation_groups"] else None], "F1": v["F1"], "F2": v["F2"], "F3": v["F3"]} for k, v in result_screen["records"].items()}, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
