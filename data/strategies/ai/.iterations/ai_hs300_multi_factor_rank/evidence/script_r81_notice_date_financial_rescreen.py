# 输入说明: R81 — 真实首次披露/更正日 + 逐日成员下的 10 个财务因子复筛。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r81_notice_date_financial_rescreen.py
# 结果 JSON: 同目录 r81_notice_date_financial_rescreen.json。
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
r78 = _load("r78", "script_r78_daily_membership_deletions.py")
r79 = _load("r79", "script_r79_exact_membership_b80.py")
r80 = _load("r80", "script_r80_financial_notice_audit.py")
DATA = r40.DATA
R80_JSON = EV / "r80_financial_notice_audit.json"
R42_JSON = EV / "r42_historical_factor_screen.json"
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
FACTORS = (
    "pb_latest",
    "roe_latest",
    "gross_margin_latest",
    "net_margin_latest",
    "revenue_yoy_latest",
    "net_income_yoy_latest",
    "debt_ratio_latest",
    "ocf_ni",
    "accrual",
    "ocf_debt",
)
ANNOUNCE_COLUMNS = (
    "_announce",
    "_announce_metrics",
    "_announce_balance",
    "_announce_cash",
    "_announce_debt_ratio",
    "_announce_ocf_ni",
    "_announce_accrual",
    "_announce_ocf_debt",
)


def _notice_periods() -> tuple[pl.DataFrame, dict]:
    universe = sorted(pl.read_parquet(r80.DAILY_MEMBERSHIP)["symbol"].unique().to_list())
    notices, api_audit = r80._ensure_cache(universe)
    periods, period_audit = r80._period_table(notices)
    return periods, {"api": api_audit, "periods": period_audit}


def _patched_snapshot(periods: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    from app.backtest.fundamentals import load_fundamental_snapshot

    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("财务快照缺失")
    effective = periods.select(["symbol", "period_end", "effective_date"])
    joined = snapshot.join(effective, on=["symbol", "period_end"], how="left")
    original_fallback = pl.max_horizontal([
        pl.col(column).fill_null(pl.date(1900, 1, 1))
        for column in ("_announce_metrics", "_announce_balance", "_announce_cash")
    ])
    joined = joined.with_columns(original_fallback.alias("_fallback_effective")).with_columns(
        pl.when(pl.col("_fallback_effective") == pl.date(1900, 1, 1))
        .then(None)
        .otherwise(pl.col("_fallback_effective"))
        .alias("_fallback_effective")
    ).with_columns(
        pl.coalesce([pl.col("effective_date"), pl.col("_fallback_effective")]).alias("_period_effective")
    ).with_columns(
        pl.col("_period_effective").cast(pl.Int64).alias("_period_effective_i")
    ).with_columns(
        pl.col("_period_effective_i").rolling_max(window_size=4, min_samples=4).over("symbol").alias("_ttm_effective_i")
    ).with_columns(
        pl.col("_ttm_effective_i").cast(pl.Date).alias("_ttm_effective")
    )
    patched = joined.with_columns([
        pl.coalesce([pl.col("_period_effective"), pl.col(column)]).alias(column)
        for column in ANNOUNCE_COLUMNS
    ]).with_columns([
        pl.coalesce([pl.col("_ttm_effective"), pl.col("_announce_ocf_ni")]).alias("_announce_ocf_ni"),
        pl.coalesce([pl.col("_ttm_effective"), pl.col("_announce_accrual")]).alias("_announce_accrual"),
        pl.coalesce([pl.col("_ttm_effective"), pl.col("_announce_ocf_debt")]).alias("_announce_ocf_debt"),
    ]).drop(["_period_effective", "_fallback_effective", "_period_effective_i", "_ttm_effective_i", "_ttm_effective", "effective_date"])
    universe = set(periods["symbol"].unique().to_list())
    universe_joined = joined.filter(pl.col("symbol").is_in(universe))
    mapped = universe_joined.filter(pl.col("effective_date").is_not_null()).height
    audit = {
        "snapshot_rows_total": snapshot.height,
        "snapshot_rows_universe": universe_joined.height,
        "mapped_rows": mapped,
        "mapped_coverage": round(mapped / universe_joined.height, 6) if universe_joined.height else None,
        "ttm_rolling_max_effective_dates": True,
        "unmapped_keep_fallback": True,
    }
    return patched, audit


def _build_rebalances(snapshot: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    from app.backtest import fundamentals as fundamentals_module

    membership, membership_audit = r79._exact_membership_frame()
    original_membership = r40.membership_frame
    original_loader = fundamentals_module.load_fundamental_snapshot
    r40.membership_frame = lambda: (membership, membership_audit)
    fundamentals_module.load_fundamental_snapshot = lambda _data_dir: snapshot
    try:
        _panel, reb, _memberships = r42.build_panel()
    finally:
        r40.membership_frame = original_membership
        fundamentals_module.load_fundamental_snapshot = original_loader
    return reb, membership_audit


def _screen(reb: pl.DataFrame) -> dict[str, Any]:
    discovery = r42._period_frame(reb, DISCOVERY)
    validation = r42._period_frame(reb, VALIDATION)
    records = {}
    pvalues = []
    for index, factor in enumerate(FACTORS):
        good_high = r42.FACTORS[factor]
        sign = 1.0 if good_high else -1.0
        disc = discovery.with_columns((pl.col(factor) * sign).alias("_signed"))
        val = validation.with_columns((pl.col(factor) * sign).alias("_signed"))
        full = reb.with_columns((pl.col(factor) * sign).alias("_signed"))
        disc_stats = r42._ic_stats(disc, "_signed", RNG_SEED + index, one_sided=False)
        val_stats = r42._ic_stats(val, "_signed", RNG_SEED + 1000 + index, one_sided=True)
        full_stats = r42._ic_stats(full, "_signed", RNG_SEED + 2000 + index, one_sided=True)
        disc_group = r42._group_stats(disc, "_signed")
        val_group = r42._group_stats(val, "_signed")
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


def _comparison(primary: dict, baseline: dict) -> dict:
    out = {}
    for factor in FACTORS:
        left = primary["records"][factor]
        right = baseline["records"][factor]
        out[factor] = {
            "disc_ic_primary": left["discovery"]["mean"],
            "disc_ic_baseline": right["discovery"]["mean"],
            "disc_ic_delta": (
                round(left["discovery"]["mean"] - right["discovery"]["mean"], 6)
                if left["discovery"]["mean"] is not None and right["discovery"]["mean"] is not None else None
            ),
            "val_ic_primary": left["validation"]["mean"],
            "val_ic_baseline": right["validation"]["mean"],
            "val_ic_delta": (
                round(left["validation"]["mean"] - right["validation"]["mean"], 6)
                if left["validation"]["mean"] is not None and right["validation"]["mean"] is not None else None
            ),
            "primary_pass": left["all_pass"],
            "baseline_pass": right["all_pass"],
        }
    return out


def main() -> int:
    r80_evidence = json.loads(R80_JSON.read_text(encoding="utf-8"))
    if not r80_evidence["decision"]["all_pass"]:
        raise RuntimeError("R80 公告日数据门槛未通过")
    periods, data_audit = _notice_periods()
    patched, snapshot_audit = _patched_snapshot(periods)
    from app.backtest.fundamentals import load_fundamental_snapshot

    baseline_snapshot = load_fundamental_snapshot(DATA)
    if baseline_snapshot is None:
        raise RuntimeError("财务快照缺失")
    baseline_reb, membership_audit = _build_rebalances(baseline_snapshot)
    primary_reb, primary_membership_audit = _build_rebalances(patched)
    baseline = _screen(baseline_reb)
    primary = _screen(primary_reb)

    r42_evidence = json.loads(R42_JSON.read_text(encoding="utf-8"))
    result = {
        "window": {
            "discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])],
            "validation": [str(VALIDATION[0]), str(VALIDATION[1])],
            "factors": list(FACTORS),
            "permutations": r42.PERMUTATIONS,
            "seed": RNG_SEED,
        },
        "data": {
            "notices": data_audit,
            "snapshot_patch": snapshot_audit,
            "membership": primary_membership_audit,
            "membership_baseline": membership_audit,
        },
        "primary": primary,
        "baseline_statutory_fallback": baseline,
        "primary_minus_fallback": _comparison(primary, baseline),
        "r42_old_window": {
            factor: {
                "discovery_ic": r42_evidence["factors"][factor]["discovery"]["mean"],
                "validation_ic": r42_evidence["factors"][factor]["validation"]["mean"],
                "all_pass": r42_evidence["factors"][factor]["all_pass"],
            }
            for factor in FACTORS
        },
        "recommendation": "R82 portfolio/risk integration" if primary["passed"] else "negative knowledge; financial notice-date line closed",
        "caveats": [
            "财务数值仍来自本地当前快照, 公告日修复只解决披露时点, 不消除数值重述风险",
            "有更正时使用最晚更正日; TTM 因子使用四期滚动最晚生效日",
            "未映射的 1.73% 本地期保留原法定截止日回退",
            "逐日成员来自 BaoStock, 不是中证指数原始公告",
        ],
    }
    path = EV / "r81_notice_date_financial_rescreen.json"
    path.write_text(json.dumps(r40.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("snapshot", snapshot_audit, "membership", primary_membership_audit, flush=True)
    print("primary decision", primary["decision"], "passed", primary["passed"], flush=True)
    print("baseline decision", baseline["decision"], "passed", baseline["passed"], flush=True)
    print("comparison", result["primary_minus_fallback"], "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
