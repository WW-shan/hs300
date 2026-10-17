# 输入说明: R82 — 工作资本与投资类财报因子复筛。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r82_working_capital_factors.py
# 结果 JSON: 同目录 r82_working_capital_factors.json。
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
r81 = _load("r81", "script_r81_notice_date_financial_rescreen.py")
DATA = r40.DATA
SUPPLEMENT_CACHE = DATA / "research" / "r82_financial_2020"
R80_JSON = EV / "r80_financial_notice_audit.json"
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
FACTORS = {
    "gross_profitability": True,
    "asset_turnover": True,
    "asset_growth_yoy": False,
    "inventory_growth_yoy": False,
    "receivable_growth_yoy": False,
    "cash_conversion_cycle": False,
}


def _read_table(table: str, columns: list[str]) -> pl.DataFrame:
    return (
        pl.read_parquet(DATA / "financials" / table / "part.parquet")
        .select(["symbol", "period_end", "announce_date", *columns])
        .with_columns([
            pl.col("period_end").cast(pl.Utf8).str.slice(0, 10).str.to_date(),
            pl.col("announce_date").cast(pl.Utf8).str.slice(0, 10).str.to_date().alias("raw_announce_date"),
        ])
        .unique(subset=["symbol", "period_end"], keep="last")
    )



def _supplement_2020(universe: set[str]) -> tuple[pl.DataFrame, dict]:
    import akshare as ak

    SUPPLEMENT_CACHE.mkdir(parents=True, exist_ok=True)
    dates = ("20200331", "20200630", "20200930", "20201231")
    frames = []
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet").select(["symbol", "code"])
    for day in dates:
        paths = {
            "balance": SUPPLEMENT_CACHE / f"balance_{day}.parquet",
            "income": SUPPLEMENT_CACHE / f"income_{day}.parquet",
        }
        if not paths["balance"].exists():
            ak.stock_zcfz_em(date=day).to_parquet(paths["balance"], index=False)
        if not paths["income"].exists():
            ak.stock_lrb_em(date=day).to_parquet(paths["income"], index=False)
        balance = pl.read_parquet(paths["balance"]).select([
            pl.col("股票代码").cast(pl.Utf8).str.zfill(6).alias("code"),
            pl.col("资产-总资产").cast(pl.Float64, strict=False).alias("total_assets"),
            pl.col("资产-应收账款").cast(pl.Float64, strict=False).alias("accounts_receivable"),
            pl.col("资产-存货").cast(pl.Float64, strict=False).alias("inventory"),
            pl.col("负债-应付账款").cast(pl.Float64, strict=False).alias("accounts_payable"),
            pl.col("公告日期").cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False).alias("raw_announce_date_balance"),
        ]).unique(subset=["code"], keep="last")
        income = pl.read_parquet(paths["income"]).select([
            pl.col("股票代码").cast(pl.Utf8).str.zfill(6).alias("code"),
            pl.col("营业总收入").cast(pl.Float64, strict=False).alias("revenue"),
            pl.col("营业总支出-营业支出").cast(pl.Float64, strict=False).alias("operating_costs"),
        ]).unique(subset=["code"], keep="last")
        merged = balance.join(income, on="code", how="full").with_columns(
            pl.lit(day).str.to_date("%Y%m%d").alias("period_end")
        ).join(instruments, on="code", how="inner").filter(pl.col("symbol").is_in(universe))
        frames.append(merged.select([
            "symbol", "period_end", "raw_announce_date_balance",
            "revenue", "operating_costs", "total_assets",
            "accounts_receivable", "inventory", "accounts_payable",
        ]))
    combined = pl.concat(frames, how="diagonal_relaxed").with_columns(
        pl.lit(None, dtype=pl.Date).alias("raw_announce_date")
    ).unique(subset=["symbol", "period_end"], keep="last").sort(["symbol", "period_end"])
    audit = {
        "requests": 8,
        "rows": combined.height,
        "symbols": combined["symbol"].n_unique(),
        "period_min": str(combined["period_end"].min()) if combined.height else None,
        "period_max": str(combined["period_end"].max()) if combined.height else None,
    }
    return combined, audit


def _financial_panel(periods: pl.DataFrame, symbols: list[str]) -> tuple[pl.DataFrame, dict]:
    income = _read_table("income", ["revenue", "operating_costs"])
    balance = _read_table("balance_sheet", ["total_assets", "accounts_receivable", "inventory", "accounts_payable"])
    joined = income.join(
        balance,
        on=["symbol", "period_end"],
        how="full",
        suffix="_balance",
    ).filter(pl.col("symbol").is_in(symbols))
    supplement, supplement_audit = _supplement_2020(set(symbols))
    joined = pl.concat([joined, supplement], how="diagonal_relaxed").unique(
        subset=["symbol", "period_end"], keep="last"
    ).sort(["symbol", "period_end"])
    joined = joined.with_columns(
        pl.max_horizontal([
            pl.col("raw_announce_date").fill_null(pl.date(1900, 1, 1)),
            pl.col("raw_announce_date_balance").fill_null(pl.date(1900, 1, 1)),
        ]).replace(pl.date(1900, 1, 1), None).alias("_raw_fallback")
    ).drop("raw_announce_date_balance")
    effective = periods.select(["symbol", "period_end", "effective_date"])
    joined = joined.join(effective, on=["symbol", "period_end"], how="left").with_columns(
        pl.coalesce([pl.col("effective_date"), pl.col("_raw_fallback")]).alias("report_effective_date")
    ).drop(["_raw_fallback", "effective_date"]).sort(["symbol", "period_end"])
    period_index = (pl.col("period_end").dt.year() * 4 + pl.col("period_end").dt.quarter())
    joined = joined.with_columns([
        period_index.alias("_period_index"),
        period_index.shift(1).over("symbol").alias("_period_lag1"),
        period_index.shift(3).over("symbol").alias("_period_lag3"),
        period_index.shift(4).over("symbol").alias("_period_lag4"),
        pl.col("revenue").shift(1).over("symbol").alias("_revenue_prev"),
        pl.col("operating_costs").shift(1).over("symbol").alias("_cost_prev"),
        pl.col("total_assets").shift(4).over("symbol").alias("_assets_lag4"),
        pl.col("inventory").shift(4).over("symbol").alias("_inventory_lag4"),
        pl.col("accounts_receivable").shift(4).over("symbol").alias("_receivable_lag4"),
        pl.col("report_effective_date").shift(4).over("symbol").alias("_effective_lag4"),
    ]).with_columns([
        pl.when(pl.col("period_end").dt.quarter() == 1)
        .then(pl.col("revenue"))
        .when(
            (pl.col("_period_index") - pl.col("_period_lag1") == 1)
            & pl.col("_revenue_prev").is_not_null()
        )
        .then(pl.col("revenue") - pl.col("_revenue_prev"))
        .otherwise(None)
        .alias("_q_revenue"),
        pl.when(pl.col("period_end").dt.quarter() == 1)
        .then(pl.col("operating_costs"))
        .when(
            (pl.col("_period_index") - pl.col("_period_lag1") == 1)
            & pl.col("_cost_prev").is_not_null()
        )
        .then(pl.col("operating_costs") - pl.col("_cost_prev"))
        .otherwise(None)
        .alias("_q_cost"),
    ]).with_columns([
        pl.col("_q_revenue").rolling_sum(window_size=4, min_samples=4).over("symbol").alias("_ttm_revenue"),
        pl.col("_q_cost").rolling_sum(window_size=4, min_samples=4).over("symbol").alias("_ttm_cost"),
        pl.col("report_effective_date").cast(pl.Int64).rolling_max(window_size=4, min_samples=4).over("symbol").alias("_ttm_effective_i"),
        (pl.col("_period_index") - pl.col("_period_lag4") == 4).alias("_lag4_valid"),
    ]).with_columns(
        pl.col("_ttm_effective_i").cast(pl.Date).alias("_ttm_effective")
    ).with_columns([
        pl.when((pl.col("total_assets") > 0) & pl.col("_ttm_revenue").is_not_null() & pl.col("_ttm_cost").is_not_null())
        .then((pl.col("_ttm_revenue") - pl.col("_ttm_cost")) / pl.col("total_assets"))
        .otherwise(None)
        .alias("gross_profitability"),
        pl.when((pl.col("total_assets") > 0) & pl.col("_ttm_revenue").is_not_null())
        .then(pl.col("_ttm_revenue") / pl.col("total_assets"))
        .otherwise(None)
        .alias("asset_turnover"),
        pl.when(pl.col("_lag4_valid") & (pl.col("_assets_lag4") > 0) & (pl.col("total_assets") > 0))
        .then(pl.col("total_assets") / pl.col("_assets_lag4") - 1.0)
        .otherwise(None)
        .alias("asset_growth_yoy"),
        pl.when(pl.col("_lag4_valid") & (pl.col("_inventory_lag4") > 0) & (pl.col("inventory") >= 0))
        .then(pl.col("inventory") / pl.col("_inventory_lag4") - 1.0)
        .otherwise(None)
        .alias("inventory_growth_yoy"),
        pl.when(pl.col("_lag4_valid") & (pl.col("_receivable_lag4") > 0) & (pl.col("accounts_receivable") >= 0))
        .then(pl.col("accounts_receivable") / pl.col("_receivable_lag4") - 1.0)
        .otherwise(None)
        .alias("receivable_growth_yoy"),
        pl.when(
            (pl.col("_ttm_revenue") > 0)
            & (pl.col("_ttm_cost") > 0)
            & pl.col("accounts_receivable").is_not_null()
            & pl.col("inventory").is_not_null()
            & pl.col("accounts_payable").is_not_null()
        )
        .then(
            365.0 * pl.col("accounts_receivable") / pl.col("_ttm_revenue")
            + 365.0 * pl.col("inventory") / pl.col("_ttm_cost")
            - 365.0 * pl.col("accounts_payable") / pl.col("_ttm_cost")
        )
        .otherwise(None)
        .alias("cash_conversion_cycle"),
    ]).with_columns([
        pl.when(pl.col("_lag4_valid"))
        .then(pl.max_horizontal("report_effective_date", "_effective_lag4"))
        .otherwise(None)
        .alias("_growth_effective"),
        pl.when(pl.col("_ttm_effective").is_not_null())
        .then(pl.max_horizontal("_ttm_effective", "report_effective_date"))
        .otherwise(None)
        .alias("_ccc_effective"),
    ])
    effective_map = {
        "gross_profitability": "_ttm_effective",
        "asset_turnover": "_ttm_effective",
        "asset_growth_yoy": "_growth_effective",
        "inventory_growth_yoy": "_growth_effective",
        "receivable_growth_yoy": "_growth_effective",
        "cash_conversion_cycle": "_ccc_effective",
    }
    audit = {
        "rows": joined.height,
        "symbols": joined["symbol"].n_unique(),
        "period_min": str(joined["period_end"].min()),
        "period_max": str(joined["period_end"].max()),
        "non_null": {
            factor: int(joined[factor].is_not_null().sum())
            for factor in FACTORS
        },
        "supplement_2020": supplement_audit,
        "effective_date_rule": effective_map,
        "ttm_requires_four_consecutive_quarters": True,
        "yoy_requires_exact_four_quarter_gap": True,
    }
    return joined, audit


def _base_panel() -> tuple[pl.DataFrame, pl.DataFrame, dict]:
    from app.backtest.fundamentals import load_fundamental_snapshot

    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("财务快照缺失")
    membership, membership_audit = r79._exact_membership_frame()
    original = r40.membership_frame
    r40.membership_frame = lambda: (membership, membership_audit)
    try:
        panel, _reb, _memberships = r42.build_panel()
    finally:
        r40.membership_frame = original
    return panel, membership, membership_audit


def _attach_factors(panel: pl.DataFrame, financial: pl.DataFrame) -> pl.DataFrame:
    out = panel.sort(["symbol", "date"])
    for factor, record in {
        "gross_profitability": "_ttm_effective",
        "asset_turnover": "_ttm_effective",
        "asset_growth_yoy": "_growth_effective",
        "inventory_growth_yoy": "_growth_effective",
        "receivable_growth_yoy": "_growth_effective",
        "cash_conversion_cycle": "_ccc_effective",
    }.items():
        right = (
            financial.select(["symbol", record, factor])
            .filter(pl.col(record).is_not_null())
            .rename({record: "_announce", factor: "_value"})
            .with_columns(pl.col("_announce").dt.offset_by("1d").alias("_effective"))
            .sort(["symbol", "_effective"])
        )
        out = out.join_asof(
            right,
            left_on="date",
            right_on="_effective",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        ).with_columns(
            pl.when(pl.col("date") > pl.col("_announce"))
            .then(pl.col("_value"))
            .otherwise(None)
            .alias(factor)
        ).drop(["_announce", "_value", "_effective"])
    return out


def _exact_rebalances(panel: pl.DataFrame, membership: pl.DataFrame) -> pl.DataFrame:
    from app.backtest.strategy import _hs300_panel_row_mask

    pairs = r42.r22.monthly_pairs(panel)
    reb = r42.r22.build_monthly(panel, pairs)
    reb = reb.filter(_hs300_panel_row_mask(reb, membership).fill_null(False))
    return reb.filter(pl.col("date") >= DISCOVERY[0])


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
        existing_corr = max(
            (
                value for value in (
                    r42.r22.mean_abs_corr(full, "_signed", "pb_latest"),
                    r42.r22.mean_abs_corr(full, "_signed", "roe_latest"),
                    r42.r22.mean_abs_corr(full, "_signed", "ocf_ni"),
                    r42.r22.mean_abs_corr(full, "_signed", "annual_vol_20d"),
                    r42.r22.mean_abs_corr(full, "_signed", "momentum_20d"),
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
            "existing_corr_max": existing_corr,
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
    universe = sorted(pl.read_parquet(r80.DAILY_MEMBERSHIP)["symbol"].unique().to_list())
    notices, _api_audit = r80._ensure_cache(universe)
    periods, _period_audit = r80._period_table(notices)
    financial, financial_audit = _financial_panel(periods, universe)
    panel, membership, membership_audit = _base_panel()
    panel = _attach_factors(panel, financial)
    reb = _exact_rebalances(panel, membership)
    result_screen = _screen(reb)
    result = {
        "window": {
            "discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])],
            "validation": [str(VALIDATION[0]), str(VALIDATION[1])],
            "factors": list(FACTORS),
            "permutations": r42.PERMUTATIONS,
            "seed": RNG_SEED,
        },
        "data": {
            "financial": financial_audit,
            "membership": membership_audit,
            "rebalances": reb.height,
            "rebalance_dates": reb.select("date").n_unique(),
        },
        **result_screen,
        "adopted_for_r83": result_screen["passed"],
        "recommendation": "R83 portfolio/risk integration" if result_screen["passed"] else "negative knowledge; working-capital/investment line closed",
        "caveats": [
            "财务数值来自本地当前快照, 公告日已按 R80 修复但数值重述风险仍存在",
            "TTM 只使用连续 4 个季度; 同比只使用恰好相隔 4 个季度的资产负债项目",
            "银行/保险等无存货或应收口径的公司会自然缺失, 不手工补零",
            "成员为逐日精确 HS300 快照",
        ],
    }
    path = EV / "r82_working_capital_factors.json"
    path.write_text(json.dumps(r40.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("financial", financial_audit, flush=True)
    print("decision", result_screen["decision"], "passed", result_screen["passed"], flush=True)
    print(
        "factors",
        {
            factor: {
                "disc_ic": record["discovery"]["mean"],
                "val_ic": record["validation"]["mean"],
                "rho": [
                    record["discovery_groups"]["monotonic_rho"] if record["discovery_groups"] else None,
                    record["validation_groups"]["monotonic_rho"] if record["validation_groups"] else None,
                ],
                "D1": record["D1"],
                "F1": record["F1"],
                "F2": record["F2"],
                "F3": record["F3"],
            }
            for factor, record in result_screen["records"].items()
        },
        "saved",
        path,
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
