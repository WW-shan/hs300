# 输入说明: R94 — 10Y-2Y 期限利差对下月 HS300 收益的预测检验。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r94_term_spread_screen.py
# 结果 JSON: 同目录 r94_term_spread_screen.json。原始缓存复用 data/research/macro/bond_10y.parquet。
from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
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
r84 = _load("r84", "script_r84_if_basis.py")
r94_probe = _load("r94_probe", "script_r94_term_spread_probe.py")
DATA = r42.DATA
DISCOVERY = (date(2021, 6, 1), date(2023, 12, 31))
VALIDATION = (date(2024, 1, 1), date(2026, 10, 14))
SIGNALS = ("term_spread_10y_2y_level", "term_spread_10y_2y_chg3m")
COST_2X = 0.0028


def _monthly_frame() -> tuple[pl.DataFrame, dict]:
    raw = pl.read_parquet(r94_probe.BOND_CACHE).with_columns([
        pl.col("日期").cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False),
        pl.col("中国国债收益率10年").cast(pl.Float64, strict=False).alias("y10"),
        pl.col("中国国债收益率2年").cast(pl.Float64, strict=False).alias("y2"),
    ]).filter(
        pl.col("日期").is_not_null() & pl.col("y10").is_not_null() & pl.col("y2").is_not_null()
    ).sort("日期").unique(subset=["日期"], keep="last")
    bond_month = (
        raw.with_columns(pl.col("日期").dt.strftime("%Y-%m").alias("month"))
        .group_by("month").agg([
            pl.col("日期").max().alias("date"),
            (pl.col("y10").last() - pl.col("y2").last()).alias("term_spread_10y_2y_level"),
        ]).sort("date")
        .with_columns(
            (pl.col("term_spread_10y_2y_level") - pl.col("term_spread_10y_2y_level").shift(3)).alias("term_spread_10y_2y_chg3m")
        )
    )
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    index = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"]).collect()
        .with_columns(pl.col("date").cast(pl.Date), pl.col("close").cast(pl.Float64))
        .sort("date").unique(subset=["date"], keep="last")
    )
    equity_month = (
        index.with_columns(pl.col("date").dt.strftime("%Y-%m").alias("ym"))
        .group_by("ym", maintain_order=True)
        .agg([pl.col("date").max().alias("date"), pl.col("close").last().alias("close")])
        .sort("date")
        .with_columns((pl.col("close").shift(-1) / pl.col("close") - 1.0).alias("fwd"))
        .drop("close")
    )
    monthly = equity_month.sort("date").join_asof(
        bond_month.sort("date"),
        on="date",
        strategy="backward",
        check_sortedness=False,
    )
    analysis = monthly.filter((pl.col("date") >= DISCOVERY[0]) & (pl.col("date") <= VALIDATION[1]))
    coverage = {
        signal: {
            "valid_months": int(analysis[signal].is_not_null().sum()),
            "total_months": analysis.height,
            "coverage": round(float(analysis[signal].is_not_null().mean()), 6),
        }
        for signal in SIGNALS
    }
    audit = {"daily_rows": raw.height, "monthly_rows": bond_month.height, "coverage": coverage}
    return monthly, audit


def main() -> int:
    monthly, audit = _monthly_frame()
    discovery = monthly.filter((pl.col("date") >= DISCOVERY[0]) & (pl.col("date") <= DISCOVERY[1]))
    validation = monthly.filter((pl.col("date") >= VALIDATION[0]) & (pl.col("date") <= VALIDATION[1]))
    records = {}
    pvalues = []
    for signal in SIGNALS:
        disc_stats = r84._stats(discovery, signal, one_sided=False)
        val_stats = r84._stats(validation, signal, one_sided=True)
        disc_groups = r84._groups(discovery, signal)
        val_groups = r84._groups(validation, signal)
        d1 = bool(
            audit["daily_rows"] >= 1500
            and audit["monthly_rows"] >= 80
            and audit["coverage"][signal]["valid_months"] >= 56
            and disc_stats["n"] >= 24 and val_stats["n"] >= 24
        )
        overall_sign = 1 if (disc_stats["ic"] or 0) >= 0 else -1
        yearly_same_sign = sum(
            1 for year in ("2022", "2023", "2024", "2025", "2026")
            if disc_stats["yearly"].get(year) is not None
            and ((1 if disc_stats["yearly"][year] >= 0 else -1) == overall_sign)
        )
        f1 = bool(
            disc_stats["ic"] is not None and disc_stats["ic"] >= 0.20
            and val_stats["ic"] is not None and val_stats["ic"] >= 0.15
            and disc_stats["ic"] > 0 and val_stats["ic"] > 0
            and yearly_same_sign >= 4
        )
        f2 = bool(
            disc_groups["monotonic_rho"] is not None and val_groups["monotonic_rho"] is not None
            and disc_groups["monotonic_rho"] >= 0.7 and val_groups["monotonic_rho"] >= 0.7
            and disc_groups["spread_annual_net2x"] > 0 and val_groups["spread_annual_net2x"] > 0
        )
        records[signal] = {
            "discovery": disc_stats,
            "validation": val_stats,
            "discovery_groups": disc_groups,
            "validation_groups": val_groups,
            "D1": d1,
            "F1": f1,
            "F2": f2,
            "yearly_same_sign": yearly_same_sign,
        }
        pvalues.append(disc_stats["perm_p"])
        print(signal, "D1", d1, "IC", disc_stats["ic"], val_stats["ic"], "p", disc_stats["perm_p"], val_stats["perm_p"], flush=True)
    qvalues = r42._bh_qvalues(pvalues)
    passed = []
    for signal, q in zip(SIGNALS, qvalues, strict=True):
        record = records[signal]
        record["discovery_q"] = q
        record["F3"] = bool(
            q is not None and q <= 0.10
            and record["validation"]["perm_p"] is not None
            and record["validation"]["perm_p"] <= 0.05
        )
        record["all_pass"] = bool(record["D1"] and record["F1"] and record["F2"] and record["F3"])
        if record["all_pass"]:
            passed.append(signal)
    result = {
        "window": {
            "discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])],
            "validation": [str(VALIDATION[0]), str(VALIDATION[1])],
            "signals": list(SIGNALS),
            "direction": "steep / steepening favorable",
            "cost_round_trip_2x": COST_2X,
            "permutations": r42.PERMUTATIONS,
            "seed": r42.RNG_SEED,
        },
        "data": audit,
        "monthly": monthly.filter(
            (pl.col("date") >= DISCOVERY[0]) & (pl.col("date") <= VALIDATION[1])
        ).select(["date", "fwd", *SIGNALS]).to_dicts(),
        "records": records,
        "passed": passed,
        "adopted_for_r95": passed,
        "decision": {
            "D1_count": sum(bool(v["D1"]) for v in records.values()),
            "F1_count": sum(bool(v["F1"]) for v in records.values()),
            "F2_count": sum(bool(v["F2"]) for v in records.values()),
            "F3_count": sum(bool(v["F3"]) for v in records.values()),
            "all_pass_count": len(passed),
        },
        "recommendation": "R95 term-spread timing overlay" if passed else "negative knowledge; term-spread line closed",
        "caveats": [
            "期限利差来自中国国债 10Y 与 2Y 收益率曲线",
            "市场级月度样本有限，IC 门槛高于个股横截面研究",
            "只做预测检验，未模拟债券/股指期货或仓位切换",
            "月末日期按最近可得债券曲线 asof 对齐",
        ],
    }
    compact = r42.r22.compact(result)
    canonical = json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    compact["sha256_canonical"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = EV / "r94_term_spread_screen.json"
    path.write_text(json.dumps(compact, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("decision", compact["decision"], "passed", passed, "sha", compact["sha256_canonical"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
