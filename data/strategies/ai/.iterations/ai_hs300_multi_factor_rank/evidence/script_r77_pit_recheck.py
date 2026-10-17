# 输入说明: R77 — PIT 历史股本 + 申万行业变动后的中性化复筛。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r77_pit_recheck.py
# 结果 JSON: 同目录 r77_pit_recheck.json。行业原始文件缓存 data/research/r77_sw_industry/ (不提交)。
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

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
r22 = r42.r22
DATA = r42.DATA
SHARE_CACHE = DATA / "research" / "r68_equity_supply"
INDUSTRY_CACHE = DATA / "research" / "r77_sw_industry"
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
MIN_VALID = 80
FACTORS = {
    "annual_vol_20d": False,
    "turnover_z_60d_pit": False,
    "amount": False,
    "momentum_20d": True,
    "pb_latest": False,
    "ocf_ni": True,
    "float_mv_pit": False,
}


def _share_history(symbols: list[str]) -> tuple[pl.DataFrame, dict]:
    frames = []
    failures = []
    for symbol in symbols:
        path = SHARE_CACHE / f"{symbol}.parquet"
        if not path.exists():
            failures.append(symbol)
            continue
        raw = pl.read_parquet(path)
        required = {"公告日期", "变动日期", "总股本", "已流通股份"}
        if raw.height == 0 or not required.issubset(raw.columns):
            failures.append(symbol)
            continue
        frame = raw.select([
            pl.lit(symbol).alias("symbol"),
            pl.col("公告日期").cast(pl.Date, strict=False).alias("announce_date"),
            pl.col("变动日期").cast(pl.Date, strict=False).alias("change_date"),
            pl.col("总股本").cast(pl.Float64, strict=False).alias("total_shares_10k"),
            pl.col("已流通股份").cast(pl.Float64, strict=False).alias("float_shares_10k"),
        ]).filter(pl.col("announce_date").is_not_null() & pl.col("change_date").is_not_null())
        frame = frame.with_columns(pl.max_horizontal("announce_date", "change_date").alias("available_date")).filter(
            (pl.col("available_date").is_not_null()) & (pl.col("float_shares_10k") > 0) & (pl.col("total_shares_10k") > 0)
        ).sort(["symbol", "available_date", "change_date"])
        if frame.height:
            frames.append(frame)
    if not frames:
        raise RuntimeError("PIT 股本缓存为空")
    out = pl.concat(frames, how="diagonal_relaxed").with_columns([
        (pl.col("total_shares_10k") * 10000.0).alias("pit_total_shares"),
        (pl.col("float_shares_10k") * 10000.0).alias("pit_float_shares"),
    ]).sort(["symbol", "available_date", "change_date"]).unique(subset=["symbol", "available_date"], keep="last")
    audit = {
        "symbols": len(symbols), "symbols_with_share_history": out["symbol"].n_unique(),
        "failure_count": len(failures), "failure_rate": round(len(failures) / len(symbols), 6),
        "failures": failures, "rows": out.height,
        "available_min": str(out["available_date"].min()), "available_max": str(out["available_date"].max()),
    }
    return out, audit


def _industry_history() -> tuple[pl.DataFrame, dict]:
    import pandas as pd
    import requests

    INDUSTRY_CACHE.mkdir(parents=True, exist_ok=True)
    path = INDUSTRY_CACHE / "StockClassifyUse_stock.xls"
    if not path.exists() or path.stat().st_size == 0:
        url = "https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls"
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.swsresearch.com/"}, verify=False, timeout=30)
        response.raise_for_status()
        path.write_bytes(response.content)
    raw = pd.read_excel(path, dtype={"股票代码": "str", "行业代码": "str"})
    frame = pl.from_pandas(raw).select([
        pl.col("股票代码").cast(pl.Utf8).str.extract(r"(\d{6})", 1).str.zfill(6).alias("code"),
        pl.col("计入日期").cast(pl.Date).alias("start_date"),
        pl.col("行业代码").cast(pl.Utf8).alias("industry_code"),
    ]).filter(pl.col("code").is_not_null() & pl.col("start_date").is_not_null() & pl.col("industry_code").is_not_null())
    frame = frame.with_columns(pl.col("industry_code").str.slice(0, 2).alias("industry_pit")).filter(pl.col("industry_pit").str.len_chars() == 2)
    frame = frame.unique(subset=["code", "start_date", "industry_code"], keep="last").sort(["code", "start_date"])
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet").select(["symbol", "code"])
    out = frame.join(instruments, on="code", how="inner").select(["symbol", "start_date", "industry_pit"]).sort(["symbol", "start_date"])
    audit = {
        "rows": out.height, "symbols": out["symbol"].n_unique(), "industries": out["industry_pit"].n_unique(),
        "start_min": str(out["start_date"].min()), "start_max": str(out["start_date"].max()),
    }
    return out, audit


def _attach_pit(panel: pl.DataFrame, reb: pl.DataFrame, shares: pl.DataFrame, industry: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    lookup = panel.with_columns((pl.col("date") - pl.duration(days=1)).alias("_lookup_date")).sort(["symbol", "_lookup_date"])
    pit = lookup.join_asof(
        shares.sort(["symbol", "available_date"]),
        left_on="_lookup_date", right_on="available_date", by="symbol", strategy="backward", check_sortedness=False,
    ).with_columns([
        pl.when(pl.col("pit_float_shares") > 0).then(pl.col("raw_close") * pl.col("pit_float_shares")).otherwise(None).alias("float_mv_pit"),
        pl.when(pl.col("pit_float_shares") > 0).then(pl.col("volume") * 10000.0 / pl.col("pit_float_shares")).otherwise(None).alias("turnover_rate_pit"),
    ])
    pit = pit.with_columns(pl.col("turnover_rate_pit").shift(1).over("symbol").alias("_turnover_prev"))
    pit = pit.with_columns([
        pl.col("_turnover_prev").rolling_mean(60, min_samples=60).over("symbol").alias("_turnover_mean60"),
        pl.col("_turnover_prev").rolling_std(60, min_samples=60).over("symbol").alias("_turnover_std60"),
    ]).with_columns(
        pl.when(pl.col("_turnover_std60") > 0).then((pl.col("turnover_rate_pit") - pl.col("_turnover_mean60")) / pl.col("_turnover_std60")).otherwise(None).alias("turnover_z_60d_pit")
    ).select(["symbol", "date", "float_mv_pit", "pit_float_shares", "pit_total_shares", "turnover_rate_pit", "turnover_z_60d_pit"])
    reb_pit = reb.join(pit, on=["symbol", "date"], how="left")
    reb_pit = reb_pit.sort(["symbol", "date"]).join_asof(
        industry.sort(["symbol", "start_date"]),
        left_on="date", right_on="start_date", by="symbol", strategy="backward", check_sortedness=False,
    )
    audit = {
        "share_coverage": round(float(reb_pit["pit_float_shares"].is_not_null().mean()), 6),
        "industry_coverage": round(float(reb_pit["industry_pit"].is_not_null().mean()), 6),
        "float_mv_coverage": round(float(reb_pit["float_mv_pit"].is_not_null().mean()), 6),
        "turnover_z_coverage": round(float(reb_pit["turnover_z_60d_pit"].is_not_null().mean()), 6),
        "current_vs_pit_mv_median_abs_rel_diff": None,
        "current_vs_pit_turnover_median_abs_rel_diff": None,
    }
    comparable = reb_pit.filter(pl.col("float_mv_pit").is_not_null() & pl.col("float_mv").is_not_null() & (pl.col("float_mv") > 0))
    if comparable.height:
        audit["current_vs_pit_mv_median_abs_rel_diff"] = round(float(((comparable["float_mv_pit"] / comparable["float_mv"] - 1.0).abs()).median()), 6)
    comparable_t = reb_pit.filter(pl.col("turnover_rate_pit").is_not_null() & pl.col("turnover_rate").is_not_null() & (pl.col("turnover_rate") > 0))
    if comparable_t.height:
        audit["current_vs_pit_turnover_median_abs_rel_diff"] = round(float(((comparable_t["turnover_rate_pit"] / comparable_t["turnover_rate"] - 1.0).abs()).median()), 6)
    return reb_pit, audit


def _neutralize(frame: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    out = frame.with_columns(pl.when(pl.col("float_mv_pit") > 0).then(pl.col("float_mv_pit").log()).otherwise(None).alias("_log_mv_pit"))
    industries = sorted(out["industry_pit"].drop_nulls().unique().to_list())
    industry_index = {name: i for i, name in enumerate(industries[1:])}
    reports = {}
    for factor, good_high in FACTORS.items():
        if factor == "float_mv_pit":
            continue
        rows = []
        r2s = []
        for keys, group in out.group_by("date", maintain_order=True):
            work = group.filter(pl.col(factor).is_not_null() & pl.col("_log_mv_pit").is_not_null() & pl.col("industry_pit").is_not_null()).select(["symbol", factor, "_log_mv_pit", "industry_pit"])
            if work.height < MIN_VALID:
                continue
            y = work[factor].to_numpy().astype(float)
            if not good_high:
                y = -y
            x = np.ones((work.height, 2 + len(industry_index)), dtype=float)
            x[:, 1] = work["_log_mv_pit"].to_numpy()
            for row, name in enumerate(work["industry_pit"].to_list()):
                idx = industry_index.get(name)
                if idx is not None:
                    x[row, 2 + idx] = 1.0
            beta, *_ = np.linalg.lstsq(x, y, rcond=None)
            residual = y - x @ beta
            total = float(np.var(y))
            r2s.append(1.0 - float(np.var(residual)) / total if total > 0 else 0.0)
            rows.extend({"symbol": s, "date": keys[0], f"_{factor}_neutral": float(v)} for s, v in zip(work["symbol"].to_list(), residual, strict=False))
        rank_col = f"_{factor}_neutral_rank"
        neutral = pl.DataFrame(rows).with_columns((pl.col(f"_{factor}_neutral").rank().over("date") / pl.len().over("date")).alias(rank_col)).select(["symbol", "date", rank_col])
        out = out.join(neutral, on=["symbol", "date"], how="left")
        reports[factor] = {"r2_mean": round(float(np.mean(r2s)), 4) if r2s else None, "r2_min": round(float(np.min(r2s)), 4) if r2s else None, "months": len(r2s)}
    return out, {"industries": len(industries), "factors": reports}


def _coverage(frame: pl.DataFrame, col: str) -> dict:
    grouped = frame.group_by("date").agg([pl.len().alias("eligible"), pl.col(col).is_not_null().sum().alias("valid")])
    if grouped.is_empty():
        return {"mean": None, "min_valid": 0, "months": 0, "months_ge_min": 0}
    return {"mean": round(float((grouped["valid"] / grouped["eligible"]).mean()), 6), "min_valid": int(grouped["valid"].min()), "months": grouped.height, "months_ge_min": int((grouped["valid"] >= MIN_VALID).sum())}


def _usable(frame: pl.DataFrame, col: str) -> pl.DataFrame:
    dates = frame.group_by("date").agg(pl.col(col).is_not_null().sum().alias("valid")).filter(pl.col("valid") >= MIN_VALID)["date"].to_list()
    return frame.filter(pl.col("date").is_in(dates))


def main() -> int:
    panel, reb, memberships = r42.build_panel()
    symbols = sorted(memberships["symbol"].unique().to_list())
    shares, share_audit = _share_history(symbols)
    industry, industry_audit = _industry_history()
    joined, data_audit = _attach_pit(panel, reb, shares, industry)
    joined, neutral_audit = _neutralize(joined)
    print("share", share_audit, "industry", industry_audit, "data", data_audit, "neutral", neutral_audit, flush=True)
    records, discovery_p = {}, []
    for index, (factor, good_high) in enumerate(FACTORS.items()):
        signed_expr = pl.col(factor) if good_high else -pl.col(factor)
        original = joined.with_columns(signed_expr.alias("_signed"))
        neutral_col = f"_{factor}_neutral_rank"
        if factor == "float_mv_pit":
            use_col = "_signed"
            disc = _usable(r42._period_frame(original, DISCOVERY), use_col)
            val = _usable(r42._period_frame(original, VALIDATION), use_col)
            disc_cov, val_cov = _coverage(r42._period_frame(original, DISCOVERY), use_col), _coverage(r42._period_frame(original, VALIDATION), use_col)
            full = original
        else:
            use_col = neutral_col
            disc = _usable(r42._period_frame(joined, DISCOVERY), use_col)
            val = _usable(r42._period_frame(joined, VALIDATION), use_col)
            disc_cov, val_cov = _coverage(r42._period_frame(joined, DISCOVERY), use_col), _coverage(r42._period_frame(joined, VALIDATION), use_col)
            full = joined
        disc_stats = r42._ic_stats(disc, use_col, RNG_SEED + index, one_sided=False)
        val_stats = r42._ic_stats(val, use_col, RNG_SEED + 1000 + index, one_sided=True)
        full_stats = r42._ic_stats(full, use_col, RNG_SEED + 2000 + index, one_sided=True)
        disc_group, val_group = r42._group_stats(disc, use_col), r42._group_stats(val, use_col)
        corr = None
        if factor != "float_mv_pit":
            corr = max((v for v in (r22.mean_abs_corr(full, use_col, "float_mv_pit") for _ in [0]) if v is not None), default=None)
            original_corr = r22.mean_abs_corr(original, use_col, "_signed")
        else:
            original_corr = None
        d1 = bool(data_audit["share_coverage"] >= 0.95 and data_audit["industry_coverage"] >= 0.90 and disc_cov["mean"] is not None and disc_cov["mean"] >= 0.70 and val_cov["mean"] is not None and val_cov["mean"] >= 0.70 and disc_cov["months_ge_min"] >= 24 and val_cov["months_ge_min"] >= 24 and disc_cov["months_ge_min"] + val_cov["months_ge_min"] >= 48)
        f1 = bool(disc_stats["mean"] is not None and disc_stats["mean"] >= 0.03 and val_stats["mean"] is not None and val_stats["mean"] >= 0.02 and disc_stats["mean"] > 0 and val_stats["mean"] > 0 and r22.yearly_same_sign(full_stats["yearly"], full_stats["mean"] or 0) >= 4)
        f2 = bool(disc_group and val_group and disc_group["monotonic_rho"] >= 0.7 and val_group["monotonic_rho"] >= 0.7 and disc_group["spread_annual_net2x"] > 0 and val_group["spread_annual_net2x"] > 0)
        f4 = bool(factor == "float_mv_pit" or (corr is not None and corr < 0.10 and original_corr is not None and original_corr < 0.95))
        records[factor] = {"direction": "high" if good_high else "low", "coverage_discovery": disc_cov, "coverage_validation": val_cov, "discovery": disc_stats, "validation": val_stats, "full": full_stats, "discovery_groups": disc_group, "validation_groups": val_group, "size_corr": corr, "original_corr": original_corr, "neutralization": neutral_audit["factors"].get(factor), "D1": d1, "F1": f1, "F2": f2, "F4": f4}
        discovery_p.append(disc_stats["perm_p"])
        print(factor, "D1", d1, "IC", disc_stats["mean"], val_stats["mean"], "rho", disc_group["monotonic_rho"] if disc_group else None, val_group["monotonic_rho"] if val_group else None, "size", corr, "orig", original_corr, flush=True)
    qvalues = r42._bh_qvalues(discovery_p)
    passed = []
    for (factor, _), q in zip(FACTORS.items(), qvalues, strict=False):
        rec = records[factor]
        rec["discovery_q"] = q
        rec["F3"] = bool(q is not None and q <= 0.10 and rec["validation"]["perm_p"] is not None and rec["validation"]["perm_p"] <= 0.05)
        rec["all_pass"] = bool(rec["D1"] and rec["F1"] and rec["F2"] and rec["F3"] and rec["F4"])
        if rec["all_pass"]:
            passed.append(factor)
    result = {
        "window": {"discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])], "validation": [str(VALIDATION[0]), str(VALIDATION[1])], "factors": list(FACTORS), "permutations": r42.PERMUTATIONS, "seed": RNG_SEED},
        "rules": {"share_available": "max(announce_date, change_date) < T", "industry_available": "start_date <= T", "industry_group": "SW code first 2 digits", "regression": "signed_factor ~ 1 + log(float_mv_pit) + industry_pit", "min_valid": MIN_VALID},
        "data": {"shares": share_audit, "industry": industry_audit, "coverage": data_audit, "neutralization": neutral_audit},
        "factors": records, "passed": passed, "adopted_for_r78": passed,
        "decision": {"D1_count": sum(bool(records[n]["D1"]) for n in FACTORS), "F1_count": sum(bool(records[n]["F1"]) for n in FACTORS), "F2_count": sum(bool(records[n]["F2"]) for n in FACTORS), "F3_count": sum(bool(records[n]["F3"]) for n in FACTORS), "F4_count": sum(bool(records[n]["F4"]) for n in FACTORS), "all_pass_count": len(passed)},
        "recommendation": "R78 portfolio/risk integration" if passed else "negative knowledge; PIT recheck closed",
        "caveats": ["SW industry code first 2 digits is a proxy for L1, not official name", "industry start_date used as effective date; update_time not used", "share availability max(announce, change) strict before T", "no fallback to current shares/industry"],
    }
    path = EV / "r77_pit_recheck.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("passed", passed, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
