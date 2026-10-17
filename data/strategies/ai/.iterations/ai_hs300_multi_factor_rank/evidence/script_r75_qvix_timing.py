# 输入说明: R75 — 300ETF QVIX 风险开关择时。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r75_qvix_timing.py
# 结果 JSON: 同目录 r75_qvix_timing.json。QVIX 缓存 data/research/r75_qvix/ (不提交)。
from __future__ import annotations

import importlib.util
import json
import math
from datetime import date
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


r40 = _load("r40", "script_r40_survivorship.py")
r54 = _load("r54", "script_r54_long_only.py")
r56 = _load("r56", "script_r56_index_timing.py")
r30 = r56.r30
DATA = r40.DATA
CACHE = DATA / "research" / "r75_qvix"
END = date(2026, 10, 16)
START = date(2021, 6, 1)
COST = 0.0028


def _qvix() -> tuple[pl.DataFrame, dict]:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "qvix_300etf.parquet"
    if path.exists() and path.stat().st_size > 0:
        raw = pl.read_parquet(path)
    else:
        raw = pl.from_pandas(ak.index_option_300etf_qvix())
        tmp = path.with_suffix(".tmp")
        raw.write_parquet(tmp)
        tmp.replace(path)
    frame = raw.select([pl.col("date").cast(pl.Date), pl.col("close").cast(pl.Float64)]).sort("date")
    valid = frame.filter(pl.col("close").is_not_null())
    n = valid.height
    mean = valid["close"].rolling_mean(252, min_samples=252)
    std = valid["close"].rolling_std(252, min_samples=252)
    valid = valid.with_columns([
        mean.alias("qvix_mean_252"), std.alias("qvix_std_252")
    ]).with_columns(
        pl.when(pl.col("qvix_std_252") > 0).then((pl.col("close") - pl.col("qvix_mean_252")) / pl.col("qvix_std_252")).otherwise(None).alias("qvix_z_252")
    )
    first_valid = valid["date"].min() if n else None
    window_rows = frame.filter((pl.col("date") >= first_valid) & (pl.col("date") <= END)).height if first_valid is not None else 0
    audit = {
        "rows": frame.height, "valid_rows": n,
        "valid_min": str(first_valid) if first_valid is not None else None, "valid_max": str(valid["date"].max()) if n else None,
        "valid_rate": round(n / frame.height, 6) if frame.height else 0.0,
        "window_rows": window_rows,
        "window_valid_rate": round(n / window_rows, 6) if window_rows else 0.0,
        "z_nonnull": int(valid["qvix_z_252"].is_not_null().sum()),
        "z_first": str(valid.filter(pl.col("qvix_z_252").is_not_null())["date"].min()) if valid["qvix_z_252"].is_not_null().any() else None,
    }
    return valid, audit


def _index_with_qvix(qvix: pl.DataFrame):
    index = r56._index().sort("date")
    index = index.with_columns((pl.col("close") / pl.col("close").shift(1) - 1.0).alias("ret"))
    joined = index.join(qvix.select(["date", "qvix_z_252"]), on="date", how="left")
    joined = joined.with_columns(
        (pl.col("qvix_z_252") < 1.0).cast(pl.Float64).fill_null(0.0).alias("risk_on")
    )
    for col in ("qvix_z_252",):
        joined = joined.with_columns(pl.col(col).fill_null(pl.col(col).shift(1)).alias(col))
    return joined


def _future_20d(joined: pl.DataFrame) -> dict:
    frame = joined.select(["date", "close", "ret", "qvix_z_252"]).drop_nulls(["close", "ret"]).sort("date")
    dates = frame["date"].to_list()
    closes = frame["close"].to_numpy()
    rets = frame["ret"].to_numpy()
    zs = frame["qvix_z_252"].to_numpy()
    high_ret, low_ret, high_vol, low_vol = [], [], [], []
    for i in range(len(dates) - 20):
        z = zs[i]
        if not np.isfinite(z):
            continue
        fwd = closes[i + 20] / closes[i] - 1.0
        vol = float(np.std(rets[i + 1:i + 21], ddof=0) * math.sqrt(252.0))
        if z >= 1.0:
            high_ret.append(float(fwd))
            high_vol.append(vol)
        else:
            low_ret.append(float(fwd))
            low_vol.append(vol)
    return {
        "high_n": len(high_ret), "low_n": len(low_ret),
        "high_mean_ret": round(float(np.mean(high_ret)), 6) if high_ret else None,
        "low_mean_ret": round(float(np.mean(low_ret)), 6) if low_ret else None,
        "high_mean_vol": round(float(np.mean(high_vol)), 6) if high_vol else None,
        "low_mean_vol": round(float(np.mean(low_vol)), 6) if low_vol else None,
        "pass": bool(high_ret and low_ret and high_vol and low_vol and np.mean(high_ret) < np.mean(low_ret) and np.mean(high_vol) > np.mean(low_vol)),
    }


def main() -> int:
    qvix, qvix_audit = _qvix()
    joined = _index_with_qvix(qvix)
    index_after_start = joined.filter(pl.col("date") >= START).sort("date")
    index_after_start = index_after_start.with_columns(
        pl.col("risk_on").shift(1).fill_null(0.0).alias("position")
    ).with_columns(pl.col("position").shift(1).fill_null(0.0).alias("_prev_position")).with_columns(
        (pl.col("position") - pl.col("_prev_position")).abs().alias("turnover")
    ).with_columns(
        (pl.col("position") * pl.col("ret") - COST * pl.col("turnover")).alias("r"),
        pl.col("ret").alias("base_r"),
    )
    phases = [p.strftime("%Y-%m") for p in r40.r33.phase_dates(r40.PHASE_TARGETS)]
    base_daily = index_after_start.filter(pl.col("base_r").is_not_null())
    strat_daily = index_after_start.filter(pl.col("r").is_not_null())
    base_m, base_roll = r56._phase_metrics(base_daily, "base_r", phases)
    strat_m, strat_roll = r56._phase_metrics(strat_daily, "r", phases)
    base_full = r56._daily_metrics(base_daily, "base_r")
    strat_full = r56._daily_metrics(strat_daily, "r")
    base_month = r56._monthly(base_daily, "base_r")
    strat_month = r56._monthly(strat_daily, "r")
    excess = r54._excess(strat_month, base_month)
    phase_better = sum(1 for a, b in zip([p["sharpe"] for p in strat_m["phases"]], [p["sharpe"] for p in base_m["phases"]], strict=False) if a is not None and b is not None and a > b)
    future = _future_20d(joined.filter(pl.col("date") <= END))
    post = index_after_start.filter(pl.col("date") >= START)
    qvix_post = post["qvix_z_252"].is_not_null().mean()
    d1 = bool(qvix_audit["window_valid_rate"] >= 0.95 and qvix_audit["z_first"] is not None and date.fromisoformat(qvix_audit["z_first"]) <= START and qvix_post >= 0.95)
    t1 = bool(strat_m["sharpe_mean"] >= base_m["sharpe_mean"] + 0.10 and strat_m["annual_mean"] >= base_m["annual_mean"])
    t2 = bool(strat_m["mdd_mean"] >= base_m["mdd_mean"] - 0.03 and phase_better >= 5)
    t3 = bool(excess["validation"]["mean"] is not None and excess["validation"]["mean"] > 0 and excess["validation"]["t"] is not None and excess["validation"]["t"] > 1.0)
    t4 = bool(future["pass"])
    gates = {"D1": d1, "T1": t1, "T2": t2, "T3": t3, "T4": t4}
    gates["all_pass"] = all(gates.values())
    result = {
        "window": {"start": str(START), "end": str(END), "phases": phases, "cost": COST, "threshold": "qvix_z_252 < 1.0"},
        "data": {**qvix_audit, "post_start_qvix_coverage": round(float(qvix_post), 6)},
        "base": {"metrics": base_m, "rolling": base_roll, "full": base_full},
        "strategy": {"metrics": strat_m, "rolling": strat_roll, "full": strat_full, "excess": excess, "phase_better": phase_better, "turnover_annual": round(float(strat_daily["turnover"].sum() / max(1.0, strat_daily.height / 252.0)), 2)},
        "future_20d": future,
        "gates": gates,
        "passed": ["QVIX_TIMING"] if gates["all_pass"] else [],
        "recommendation": "R76 final risk/cost audit" if gates["all_pass"] else "negative knowledge; QVIX dimension closed",
        "caveats": ["QVIX 早期 2015~2019 为空, 只使用 2019-12 后有效区间", "现金收益按 0", "指数 close-to-close", "阈值/窗口预注册固定"],
    }
    path = EV / "r75_qvix_timing.json"
    path.write_text(json.dumps(r30.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("base", base_full, "strat", strat_full, "phase_better", phase_better, "excess", excess, "future", future, "gates", gates, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
