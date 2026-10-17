# 输入说明: R86 — 研发/商誉/无形资产/资本开支因子复筛。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r86_intangible_investment.py
# 原始紧凑缓存 data/research/r86_intangible/ (不入库); 结果 JSON 同目录 r86_intangible_investment.json。
from __future__ import annotations

import concurrent.futures as cf
import importlib.util
import json
import os
import time
from pathlib import Path
from typing import Any

import polars as pl

os.environ.setdefault("TQDM_DISABLE", "1")
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
CACHE_DIR = DATA / "research" / "r86_intangible"
R80_JSON = EV / "r80_financial_notice_audit.json"
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
FACTORS = {
    "rd_intensity": True,
    "goodwill_ratio": False,
    "intangible_ratio": False,
    "capex_intensity": False,
}
MAX_AGE_DAYS = 500


def _fetch_one(symbol: str) -> dict:
    import akshare as ak
    import pandas as pd

    path = CACHE_DIR / f"{symbol}.parquet"
    if path.exists():
        return {"symbol": symbol, "ok": True, "cached": True}
    code = ("SH" if symbol.endswith(".SH") else "SZ") + symbol[:6]
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            profit = ak.stock_profit_sheet_by_report_em(symbol=code)
            balance = ak.stock_balance_sheet_by_report_em(symbol=code)
            cash = ak.stock_cash_flow_sheet_by_report_em(symbol=code)
            p = profit[[c for c in ("REPORT_DATE", "NOTICE_DATE", "TOTAL_OPERATE_INCOME", "OPERATE_INCOME", "RESEARCH_EXPENSE") if c in profit.columns]].copy()
            b = balance[[c for c in ("REPORT_DATE", "NOTICE_DATE", "GOODWILL", "INTANGIBLE_ASSET", "TOTAL_ASSETS") if c in balance.columns]].copy()
            c = cash[[c for c in ("REPORT_DATE", "NOTICE_DATE", "CONSTRUCT_LONG_ASSET") if c in cash.columns]].copy()
            for frame in (p, b, c):
                frame["REPORT_DATE"] = pd.to_datetime(frame["REPORT_DATE"], errors="coerce")
                frame["NOTICE_DATE"] = pd.to_datetime(frame["NOTICE_DATE"], errors="coerce")
            merged = p.merge(b, on="REPORT_DATE", how="inner", suffixes=("_profit", "_balance")).merge(
                c, on="REPORT_DATE", how="left"
            )
            merged = merged[merged["REPORT_DATE"].dt.month == 12].copy()
            merged["period_end"] = merged["REPORT_DATE"].dt.date
            merged["notice_date"] = merged["NOTICE_DATE_profit"].dt.date
            income = merged.get("TOTAL_OPERATE_INCOME")
            operating = merged.get("OPERATE_INCOME")
            if income is None:
                income = operating
            elif operating is not None:
                income = income.where(income.notna(), operating)
            out = pd.DataFrame({
                "symbol": symbol,
                "period_end": merged["period_end"],
                "notice_date": merged["notice_date"],
                "revenue": income,
                "rd_expense": merged.get("RESEARCH_EXPENSE"),
                "goodwill": merged.get("GOODWILL"),
                "intangible_asset": merged.get("INTANGIBLE_ASSET"),
                "total_assets": merged.get("TOTAL_ASSETS"),
                "capex": merged.get("CONSTRUCT_LONG_ASSET"),
            })
            out = out[out["period_end"].map(lambda value: 2019 <= value.year <= 2025)]
            if out.empty:
                raise RuntimeError("annual statement rows empty")
            out.to_parquet(path, index=False)
            return {"symbol": symbol, "ok": True, "cached": False, "rows": len(out)}
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1.0 + attempt)
    return {"symbol": symbol, "ok": False, "error": f"{type(last_error).__name__}: {str(last_error)[:180]}"}


def _ensure_statements(symbols: list[str]) -> tuple[pl.DataFrame, dict]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    with cf.ThreadPoolExecutor(max_workers=4) as executor:
        for index, result in enumerate(executor.map(_fetch_one, symbols), start=1):
            results.append(result)
            if index % 25 == 0 or index == len(symbols):
                print(f"statements {index}/{len(symbols)}", flush=True)
    frames = [pl.read_parquet(CACHE_DIR / f"{symbol}.parquet") for symbol in symbols if (CACHE_DIR / f"{symbol}.parquet").exists()]
    raw = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
    if raw.height:
        raw = raw.with_columns([
            pl.col("period_end").cast(pl.Date),
            pl.col("notice_date").cast(pl.Date),
            *[pl.col(c).cast(pl.Float64, strict=False) for c in ("revenue", "rd_expense", "goodwill", "intangible_asset", "total_assets", "capex")],
        ]).unique(subset=["symbol", "period_end"], keep="last").sort(["symbol", "period_end"])
    failures = [row for row in results if not row.get("ok")]
    audit = {
        "symbols": len(symbols),
        "success": len(symbols) - len(failures),
        "success_rate": round((len(symbols) - len(failures)) / len(symbols), 6),
        "failures": failures,
        "rows": raw.height,
        "symbols_with_rows": raw["symbol"].n_unique() if raw.height else 0,
        "period_min": str(raw["period_end"].min()) if raw.height else None,
        "period_max": str(raw["period_end"].max()) if raw.height else None,
        "non_null": {
            "revenue": int(raw["revenue"].is_not_null().sum()) if raw.height else 0,
            "rd_expense": int(raw["rd_expense"].is_not_null().sum()) if raw.height else 0,
            "goodwill": int(raw["goodwill"].is_not_null().sum()) if raw.height else 0,
            "intangible_asset": int(raw["intangible_asset"].is_not_null().sum()) if raw.height else 0,
            "total_assets": int(raw["total_assets"].is_not_null().sum()) if raw.height else 0,
            "capex": int(raw["capex"].is_not_null().sum()) if raw.height else 0,
        },
    }
    return raw, audit


def _factors(statements: pl.DataFrame, periods: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    fy = periods.filter(pl.col("report_type") == "FY").select(["symbol", "period_end", "first_notice_date", "effective_date"])
    joined = statements.join(fy, on=["symbol", "period_end"], how="left").with_columns(
        pl.coalesce([pl.col("effective_date"), pl.col("notice_date"), pl.col("first_notice_date")]).alias("available_date")
    ).with_columns([
        pl.when((pl.col("revenue") > 0) & (pl.col("rd_expense") >= 0))
        .then(pl.col("rd_expense") / pl.col("revenue")).otherwise(None).alias("rd_intensity"),
        pl.when((pl.col("total_assets") > 0) & pl.col("goodwill").is_not_null())
        .then(pl.col("goodwill") / pl.col("total_assets")).otherwise(None).alias("goodwill_ratio"),
        pl.when((pl.col("total_assets") > 0) & pl.col("intangible_asset").is_not_null())
        .then(pl.col("intangible_asset") / pl.col("total_assets")).otherwise(None).alias("intangible_ratio"),
        pl.when((pl.col("revenue") > 0) & (pl.col("capex") >= 0))
        .then(pl.col("capex") / pl.col("revenue")).otherwise(None).alias("capex_intensity"),
    ])
    audit = {
        "matched_rows": int(joined["available_date"].is_not_null().sum()),
        "non_null": {factor: int(joined[factor].is_not_null().sum()) for factor in FACTORS},
    }
    return joined, audit


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


def _attach(reb: pl.DataFrame, values: pl.DataFrame) -> pl.DataFrame:
    out = reb.sort(["symbol", "date"])
    for factor in FACTORS:
        right = values.select(["symbol", "available_date", factor]).filter(
            pl.col("available_date").is_not_null() & pl.col(factor).is_not_null()
        ).with_columns((pl.col("available_date") + pl.duration(days=1)).alias("_effective")).sort(
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
                pl.col("available_date").is_not_null()
                & ((pl.col("date") - pl.col("available_date")).dt.total_days() <= MAX_AGE_DAYS)
            ).then(pl.col(factor)).otherwise(None).alias(factor)
        ).drop(["available_date", "_effective"])
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
                    r42.r22.mean_abs_corr(full, "_signed", "roe_latest"),
                    r42.r22.mean_abs_corr(full, "_signed", "ocf_ni"),
                    r42.r22.mean_abs_corr(full, "_signed", "annual_vol_20d"),
                    r42.r22.mean_abs_corr(full, "_signed", "momentum_20d"),
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
            "D1_count": sum(bool(v["D1"]) for v in records.values()),
            "F1_count": sum(bool(v["F1"]) for v in records.values()),
            "F2_count": sum(bool(v["F2"]) for v in records.values()),
            "F3_count": sum(bool(v["F3"]) for v in records.values()),
            "all_pass_count": len(passed),
        },
    }


def main() -> int:
    r80_evidence = json.loads(R80_JSON.read_text(encoding="utf-8"))
    if not r80_evidence["decision"]["all_pass"]:
        raise RuntimeError("R80 公告日数据门槛未通过")
    universe = sorted(pl.read_parquet(r80.DAILY_MEMBERSHIP)["symbol"].unique().to_list())
    statements, fetch_audit = _ensure_statements(universe)
    notices, _ = r80._ensure_cache(universe)
    periods, _ = r80._period_table(notices)
    values, factor_audit = _factors(statements, periods)
    reb, membership_audit = _base_rebalances()
    reb = _attach(reb, values)
    result_screen = _screen(reb)
    result = {
        "window": {"discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])], "validation": [str(VALIDATION[0]), str(VALIDATION[1])], "factors": list(FACTORS), "permutations": r42.PERMUTATIONS, "seed": RNG_SEED, "max_age_days": MAX_AGE_DAYS},
        "data": {"fetch": fetch_audit, "factors": factor_audit, "membership": membership_audit, "rebalances": reb.height, "rebalance_dates": reb.select("date").n_unique()},
        **result_screen,
        "adopted_for_r87": result_screen["passed"],
        "recommendation": "R87 portfolio/risk integration" if result_screen["passed"] else "negative knowledge; intangible/investment line closed",
        "caveats": [
            "研发费用/商誉/无形资产/资本开支均来自当前财报快照, 可能有重述",
            "公告日优先使用 R80 首次披露/更正日, 缺失时使用报表接口 NOTICE_DATE",
            "缺失商誉/无形资产不补 0",
            "年度报告超过 500 天后不再使用",
        ],
    }
    path = EV / "r86_intangible_investment.json"
    path.write_text(json.dumps(r40.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("fetch", fetch_audit, "factors", factor_audit, "membership", membership_audit, flush=True)
    print("decision", result_screen["decision"], "passed", result_screen["passed"], flush=True)
    print("records", {k: {"disc": v["discovery"]["mean"], "val": v["validation"]["mean"], "cov": [v["discovery"]["coverage"]["mean"], v["validation"]["coverage"]["mean"]], "rho": [v["discovery_groups"]["monotonic_rho"] if v["discovery_groups"] else None, v["validation_groups"]["monotonic_rho"] if v["validation_groups"] else None], "F1": v["F1"], "F2": v["F2"], "F3": v["F3"]} for k, v in result_screen["records"].items()}, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
