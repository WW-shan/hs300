# 输入说明: R89 — TTM 分红增长/首次分红因子筛选。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r89_dividend_growth_screen.py
# 结果 JSON: 同目录 r89_dividend_growth_screen.json。原始逐股缓存 data/research/r89_dividend_growth/ (不提交)。
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r42 = _load("r42", "script_r42_historical_factor_screen.py")
r22 = r42.r22
r44 = _load("r44", "script_r44_holder.py")
r39 = _load("r39_external", "script_r39_external.py")
r87_screen = _load("r87_screen", "script_r87_holder_concentration_screen.py")
r89_probe = _load("r89_probe", "script_r89_dividend_growth_probe.py")
DATA = r42.DATA
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
FACTORS: dict[str, bool] = {
    "dividend_growth_ttm": True,
    "dividend_initiation_flag": True,
}
CONTROLS = [
    "dividend_yield",
    "pb_latest",
    "momentum_20d",
    "annual_vol_20d",
    "float_mv",
    "net_income_yoy_latest",
]
MIN_VALID_MONTH = 80
MIN_USABLE_MONTHS = 24
MIN_TOTAL_USABLE_MONTHS = 48
TTM_DAYS = 365


def _ttm_factor(events: list[dict], day: date) -> dict:
    window = [
        row for row in events
        if (day - timedelta(days=730)) < row["announce_date"] < day
    ]
    if not window:
        return {
            "dividend_ttm_cash": 0.0,
            "dividend_growth_ttm": None,
            "dividend_initiation_flag": 0.0,
        }
    current_cutoff = day - timedelta(days=TTM_DAYS)
    current = 0.0
    previous = 0.0
    for index, row in enumerate(window):
        normalizer = math.prod(
            later["split_multiplier"]
            for later in window[index:]
            if later["announce_date"] <= day
        )
        cash = float(row["cash_per10"]) / 10.0 / normalizer
        if row["announce_date"] > current_cutoff:
            current += cash
        else:
            previous += cash
    return {
        "dividend_ttm_cash": round(current, 10),
        "dividend_growth_ttm": round(current / previous - 1.0, 10) if previous > 0 else None,
        "dividend_initiation_flag": 1.0 if current > 0 and previous <= 0 else 0.0,
    }


def _factor_frame(raw: pl.DataFrame, reb: pl.DataFrame) -> pl.DataFrame:
    events_by_symbol = {
        keys[0] if isinstance(keys, tuple) else keys: group.sort("announce_date").to_dicts()
        for keys, group in raw.group_by("symbol", maintain_order=True)
    }
    rows = []
    for row in reb.select(["symbol", "date"]).iter_rows(named=True):
        factors = _ttm_factor(events_by_symbol.get(row["symbol"], []), row["date"])
        rows.append({"symbol": row["symbol"], "date": row["date"], **factors})
    return pl.DataFrame(rows)


def _coverage(frame: pl.DataFrame, col: str) -> dict:
    grouped = frame.group_by("date").agg([
        pl.len().alias("eligible"),
        pl.col(col).is_not_null().sum().alias("valid"),
    ])
    if grouped.is_empty():
        return {"mean": None, "min_valid": 0, "months": 0, "months_ge80": 0}
    coverage = grouped["valid"] / grouped["eligible"]
    return {
        "mean": round(float(coverage.mean()), 6),
        "min_valid": int(grouped["valid"].min()),
        "months": grouped.height,
        "months_ge80": int((grouped["valid"] >= MIN_VALID_MONTH).sum()),
    }


def _usable_months(frame: pl.DataFrame, col: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    grouped = frame.group_by("date").agg(pl.col(col).is_not_null().sum().alias("valid")).sort("date")
    usable = grouped.filter(pl.col("valid") >= MIN_VALID_MONTH)
    return frame.filter(pl.col("date").is_in(usable["date"].to_list())), usable


def _positive_structure(frame: pl.DataFrame, col: str) -> dict:
    positive = frame.filter(pl.col(col) > 0)
    return {
        "positive_rows": positive.height,
        "positive_symbols": positive["symbol"].n_unique() if positive.height else 0,
    }


def _digest(frame: pl.DataFrame) -> str:
    digest = hashlib.sha256()
    for row in frame.sort(["symbol", "date"]).iter_rows():
        digest.update(repr(row).encode())
    return digest.hexdigest()


def main() -> int:
    panel, _legacy_reb, memberships = r42.build_panel()
    symbols = sorted(memberships["symbol"].unique().to_list())
    raw, manifest = r89_probe._ensure_cache(symbols)
    reb_all = r87_screen._build_last_month_panel(panel)
    reb, membership_audit = r87_screen._exact_membership_reb(
        reb_all.filter(pl.col("date") >= DISCOVERY[0])
    )
    factors = _factor_frame(raw, reb)
    joined = reb.join(factors, on=["symbol", "date"], how="left")
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet").select(["symbol", "code", "float_shares"])
    dividend_yield, dividend_feasibility = r39.dividend_data(instruments, symbols)
    joined = r39.attach_dividend(joined, dividend_yield)
    for name in FACTORS:
        joined = joined.with_columns([
            pl.col(name).quantile(0.01).over("date").alias("_lo"),
            pl.col(name).quantile(0.99).over("date").alias("_hi"),
        ]).with_columns(
            pl.col(name).clip(pl.col("_lo"), pl.col("_hi")).alias(name)
        ).drop(["_lo", "_hi"])

    discovery = r42._period_frame(joined, DISCOVERY)
    validation = r42._period_frame(joined, VALIDATION)
    source_ok = bool(
        manifest["failure_rate"] <= 0.10
        and raw.height > 0
        and raw["announce_date"].is_not_null().sum() == raw.height
        and raw["cash_per10"].is_not_null().sum() == raw.height
        and (raw["split_multiplier"] > 0).sum() == raw.height
    )
    print("panel", panel.shape, "reb", reb.height, "raw", raw.shape, "membership", membership_audit, flush=True)

    records = {}
    discovery_pvalues = []
    for index, (name, good_high) in enumerate(FACTORS.items()):
        sign = 1.0 if good_high else -1.0
        raw_disc = discovery.with_columns((pl.col(name) * sign).alias("_signed"))
        raw_val = validation.with_columns((pl.col(name) * sign).alias("_signed"))
        disc_coverage = _coverage(raw_disc, name)
        val_coverage = _coverage(raw_val, name)
        disc, disc_months = _usable_months(raw_disc, name)
        val, val_months = _usable_months(raw_val, name)
        full = joined.with_columns((pl.col(name) * sign).alias("_signed"))
        disc_stats = r42._ic_stats(disc, "_signed", RNG_SEED + index, one_sided=False)
        val_stats = r42._ic_stats(val, "_signed", RNG_SEED + 1000 + index, one_sided=True)
        full_stats = r42._ic_stats(full, "_signed", RNG_SEED + 2000 + index, one_sided=True)
        disc_group = r42._group_stats(disc, "_signed")
        val_group = r42._group_stats(val, "_signed")
        corr_values = {
            control: r22.mean_abs_corr(full, "_signed", control)
            for control in CONTROLS
            if control in full.columns
        }
        corr_max = max((value for value in corr_values.values() if value is not None), default=None)
        disc_positive = _positive_structure(disc, name)
        val_positive = _positive_structure(val, name)
        d1 = bool(
            source_ok
            and disc_coverage["mean"] is not None and disc_coverage["mean"] >= 0.70
            and val_coverage["mean"] is not None and val_coverage["mean"] >= 0.70
            and disc_coverage["min_valid"] >= MIN_VALID_MONTH
            and val_coverage["min_valid"] >= MIN_VALID_MONTH
            and disc_months.height >= MIN_USABLE_MONTHS
            and val_months.height >= MIN_USABLE_MONTHS
            and disc_months.height + val_months.height >= MIN_TOTAL_USABLE_MONTHS
            and (name != "dividend_initiation_flag" or (
                disc_positive["positive_rows"] >= 20
                and disc_positive["positive_symbols"] >= 10
                and val_positive["positive_rows"] >= 20
                and val_positive["positive_symbols"] >= 10
            ))
        )
        f1 = bool(
            disc_stats["mean"] is not None and disc_stats["mean"] >= 0.03
            and val_stats["mean"] is not None and val_stats["mean"] >= 0.02
            and disc_stats["mean"] > 0 and val_stats["mean"] > 0
            and r22.yearly_same_sign(full_stats["yearly"], full_stats["mean"] or 0) >= 4
        )
        f2 = bool(
            disc_group and val_group
            and disc_group["monotonic_rho"] >= 0.7 and val_group["monotonic_rho"] >= 0.7
            and disc_group["spread_annual_net2x"] > 0 and val_group["spread_annual_net2x"] > 0
        )
        f4 = bool(corr_max is not None and corr_max < 0.70)
        records[name] = {
            "direction": "high" if good_high else "low",
            "coverage_discovery": disc_coverage,
            "coverage_validation": val_coverage,
            "usable_months": {"discovery": disc_months.height, "validation": val_months.height},
            "positive_structure": {"discovery": disc_positive, "validation": val_positive},
            "discovery": disc_stats,
            "validation": val_stats,
            "full": full_stats,
            "discovery_groups": disc_group,
            "validation_groups": val_group,
            "corr_existing": corr_values,
            "corr_existing_max": corr_max,
            "industry": r44._industry_report(full, name),
            "D1": d1,
            "F1": f1,
            "F2": f2,
            "F4": f4,
        }
        discovery_pvalues.append(disc_stats["perm_p"])
        print(
            f"{name}: D1={d1} cov={disc_coverage['mean']}/{val_coverage['mean']} "
            f"IC={disc_stats['mean']}/{val_stats['mean']} "
            f"rho={disc_group['monotonic_rho'] if disc_group else None}/{val_group['monotonic_rho'] if val_group else None} "
            f"corr={corr_max} p={disc_stats['perm_p']} pos={disc_positive}/{val_positive}",
            flush=True,
        )

    qvalues = r42._bh_qvalues(discovery_pvalues)
    passed = []
    for (name, _), qvalue in zip(FACTORS.items(), qvalues, strict=True):
        record = records[name]
        record["discovery_q"] = qvalue
        record["F3"] = bool(
            qvalue is not None and qvalue <= 0.10
            and record["validation"]["perm_p"] is not None
            and record["validation"]["perm_p"] <= 0.05
        )
        record["all_pass"] = bool(
            record["D1"] and record["F1"] and record["F2"] and record["F3"] and record["F4"]
        )
        if record["all_pass"]:
            passed.append(name)

    factor_corr = r22.mean_abs_corr(joined, "dividend_growth_ttm", "dividend_initiation_flag")
    if factor_corr is not None and not math.isfinite(factor_corr):
        factor_corr = None
    result = {
        "window": {
            "discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])],
            "validation": [str(VALIDATION[0]), str(VALIDATION[1])],
            "ttm_days": TTM_DAYS,
            "factors": list(FACTORS),
            "direction": {name: "high" for name in FACTORS},
            "permutations": r42.PERMUTATIONS,
            "seed": RNG_SEED,
            "rebalance": "last trading snapshot day of each month; close-to-next-month-end close",
            "cost_round_trip_2x": 0.0028,
        },
        "data": {
            "fetch": manifest,
            "raw_rows": raw.height,
            "latest_announce_date": str(raw["announce_date"].max()) if raw.height else None,
            "membership": membership_audit,
            "signals": factors.height,
            "signal_digest": _digest(factors),
            "dividend_yield_feasibility": dividend_feasibility,
        },
        "coverage": {
            name: {
                "discovery": records[name]["coverage_discovery"],
                "validation": records[name]["coverage_validation"],
            }
            for name in FACTORS
        },
        "factors": records,
        "factor_correlation": factor_corr,
        "benchmarks": r87_screen._benchmark_metrics(reb),
        "passed": passed,
        "adopted_for_r90": passed,
        "decision": {
            "source_ok": source_ok,
            "D1_count": sum(bool(records[name]["D1"]) for name in FACTORS),
            "F1_count": sum(bool(records[name]["F1"]) for name in FACTORS),
            "F2_count": sum(bool(records[name]["F2"]) for name in FACTORS),
            "F3_count": sum(bool(records[name]["F3"]) for name in FACTORS),
            "F4_count": sum(bool(records[name]["F4"]) for name in FACTORS),
            "all_pass_count": len(passed),
        },
        "production_state": "pause_production",
        "recommendation": "R90 dividend-growth portfolio validation" if passed else "negative knowledge; dividend-growth dimension closed",
        "caveats": [
            "新浪历史分红为供应商回填数据，公告日可能不是交易所原始首次公告时点",
            "TTM 现金按之后送转因子归一，但仍可能有配股/股本变化造成的口径误差",
            "首次分红信号在 365 天内持续为 1，不是单日事件",
            "R39 dividend_yield 年度数据在 2026H2 有缺口，仅用于独立相关性对照，不参与因子构建",
            "逐日成员来自 BaoStock 查询，不是中证指数原始调样公告",
        ],
    }
    compact = r22.compact(result)
    canonical = json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    compact["sha256_canonical"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = EV / "r89_dividend_growth_screen.json"
    path.write_text(json.dumps(compact, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("decision", compact["decision"], "passed", passed, "sha", compact["sha256_canonical"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
