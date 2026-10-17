# 输入说明: R42 — 历史有效 HS300 宇宙因子重筛 (discovery/validation + FDR)。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r42_historical_factor_screen.py
# 结果 JSON: 同目录 r42_historical_factor_screen.json。
from __future__ import annotations

import importlib.util
import json
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
r22 = r40.r22
r28 = r40.r28
r30 = r40.r30
r33 = r40.r33
DATA = r40.DATA
END = r40.END
DISCOVERY = (date(2021, 6, 1), date(2023, 12, 31))
VALIDATION = (date(2024, 1, 1), END)
PERMUTATIONS = 1000
RNG_SEED = 20261017

# 方向: True=高值好, False=低值好; 全部来自 R22~R26 的经济先验, 不按结果改。
FACTORS: dict[str, bool] = {
    "pb_latest": False,
    "roe_latest": True,
    "gross_margin_latest": True,
    "net_margin_latest": True,
    "revenue_yoy_latest": True,
    "net_income_yoy_latest": True,
    "debt_ratio_latest": False,
    "ocf_ni": True,
    "accrual": False,
    "ocf_debt": True,
    "overnight_ret_20d": True,
    "amplitude_trend_20_60": True,
    "momentum_20d": True,
    "momentum_60d": True,
    "momentum_5d": False,
    "rsi_14_delta_5d": False,
    "kdj_kd_diff": False,
    "annual_vol_20d": False,
    "downside_vol_20d": False,
    "max_ret_20d": False,
    "beta_60d": False,
    "close_position": True,
    "vol_ratio_5d": False,
    "turnover_z_60d": False,
    "amount": False,
    "float_mv": False,
}
PRICE_FACTORS = {
    "overnight_ret_20d", "amplitude_trend_20_60", "momentum_20d", "momentum_60d",
    "momentum_5d", "rsi_14_delta_5d", "kdj_kd_diff", "annual_vol_20d",
    "downside_vol_20d", "max_ret_20d", "close_position", "vol_ratio_5d",
    "turnover_z_60d",
}
BASE_DEPS = {
    "prev_close", "amplitude", "rsi_14", "kdj_k", "kdj_d", "turnover_rate",
    "close", "open", "high", "low", "volume", "amount",
}


def _market_beta(panel: pl.DataFrame) -> pl.DataFrame:
    index_paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    index = (
        pl.scan_parquet([str(path) for path in index_paths], extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
        .with_columns((pl.col("close") / pl.col("close").shift(1) - 1.0).alias("mkt_ret"))
        .select(["date", "mkt_ret"])
    )
    out = panel.sort(["symbol", "date"]).join(index, on="date", how="left")
    out = out.with_columns((pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("_ret"))
    out = out.with_columns([
        pl.col("_ret").rolling_mean(60).over("symbol").alias("_stock_mean"),
        pl.col("mkt_ret").rolling_mean(60).over("symbol").alias("_mkt_mean"),
    ]).with_columns([
        ((pl.col("_ret") - pl.col("_stock_mean")) * (pl.col("mkt_ret") - pl.col("_mkt_mean")))
        .rolling_mean(60).over("symbol").alias("_cov"),
        ((pl.col("mkt_ret") - pl.col("_mkt_mean")) ** 2)
        .rolling_mean(60).over("symbol").alias("_var"),
    ]).with_columns(
        pl.when(pl.col("_var") > 0).then(pl.col("_cov") / pl.col("_var")).otherwise(None).alias("beta_60d")
    )
    return out.drop(["_stock_mean", "_mkt_mean", "_cov", "_var"])


def build_panel() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    memberships, _ = r40.membership_frame()
    symbols = sorted(memberships["symbol"].unique().to_list())
    combined, _ = r40.load_combined_prices(symbols)
    instruments = pl.read_parquet(DATA / "instruments" / "instruments.parquet").select(["symbol", "float_shares"])
    panel = combined.join(instruments, on="symbol", how="left")
    panel = panel.with_columns((pl.col("raw_close") * pl.col("float_shares")).alias("float_mv"))
    panel = _market_beta(panel)
    from app.indicators.pipeline import compute_indicators
    from app.strategy.scoring import materialize_scoring_columns

    panel = compute_indicators(panel, needed=FACTORS.keys() | BASE_DEPS)
    panel = materialize_scoring_columns(panel, set(FACTORS))
    financial = [
        "pb_latest", "roe_latest", "gross_margin_latest", "net_margin_latest",
        "revenue_yoy_latest", "net_income_yoy_latest", "debt_ratio_latest", "ocf_ni",
        "accrual", "ocf_debt",
    ]
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot

    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("财务快照缺失")
    panel = attach_fundamental_factors(panel, snapshot, financial)
    pairs = r22.monthly_pairs(panel)
    reb = r22.build_monthly(panel, pairs)
    from app.backtest.strategy import _hs300_panel_row_mask

    reb = reb.filter(_hs300_panel_row_mask(reb, memberships).fill_null(False))
    reb = reb.filter(pl.col("date") >= DISCOVERY[0])
    return panel, reb, memberships


def _scores(reb: pl.DataFrame) -> pl.DataFrame:
    out = reb.with_columns([
        pl.col("overnight_ret_20d").rank().over("date").alias("_v20_a"),
        pl.col("amplitude_trend_20_60").rank().over("date").alias("_v20_b"),
        pl.col("pb_latest").rank(descending=True).over("date").alias("_d1_a"),
        pl.col("roe_latest").rank().over("date").alias("_d1_b"),
        pl.col("ocf_ni").rank().over("date").alias("_d1_c"),
        pl.col("debt_ratio_latest").rank(descending=True).over("date").alias("_d4_a"),
        pl.col("ocf_debt").rank().over("date").alias("_d4_b"),
    ])
    return out.with_columns([
        pl.mean_horizontal("_v20_a", "_v20_b", ignore_nulls=True).alias("v20_score"),
        pl.mean_horizontal("_d1_a", "_d1_b", "_d1_c", ignore_nulls=True).alias("d1_score"),
        pl.mean_horizontal("_d4_a", "_d4_b", ignore_nulls=True).alias("d4_score"),
    ]).drop(["_v20_a", "_v20_b", "_d1_a", "_d1_b", "_d1_c", "_d4_a", "_d4_b"])


def _period_frame(reb: pl.DataFrame, bounds: tuple[date, date]) -> pl.DataFrame:
    return reb.filter((pl.col("date") >= bounds[0]) & (pl.col("date") <= bounds[1]))


def _perm_p(values: list[float], seed: int, *, one_sided: bool) -> float | None:
    if len(values) < 12:
        return None
    arr = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.choice(np.array([-1.0, 1.0]), size=(PERMUTATIONS, len(arr)))
    means = (draws * arr).mean(axis=1)
    observed = float(arr.mean())
    if one_sided:
        return float((1 + np.sum(means >= observed)) / (PERMUTATIONS + 1))
    return float((1 + np.sum(np.abs(means) >= abs(observed))) / (PERMUTATIONS + 1))


def _coverage(frame: pl.DataFrame, col: str) -> dict:
    grouped = frame.group_by("date").agg([
        pl.len().alias("eligible"),
        pl.col(col).is_not_null().sum().alias("valid"),
    ])
    if grouped.is_empty():
        return {"mean": None, "min_valid": 0, "months": 0}
    return {
        "mean": round(float((grouped["valid"] / grouped["eligible"]).mean()), 6),
        "min_valid": int(grouped["valid"].min()),
        "months": grouped.height,
    }


def _ic_stats(frame: pl.DataFrame, col: str, seed: int, *, one_sided: bool) -> dict:
    ic = r22.ic_summary(frame, col)
    series = r22.ic_frame(frame, col)
    values = series["ic"].drop_nulls().to_list() if not series.is_empty() else []
    p = _perm_p(values, seed, one_sided=one_sided)
    return {
        "mean": ic["ic_mean"] if ic else None,
        "ir": ic["ir"] if ic else None,
        "n_dates": ic["n_dates"] if ic else 0,
        "yearly": ic["yearly"] if ic else {},
        "perm_p": p,
        "coverage": _coverage(frame, col),
    }


def _group_stats(frame: pl.DataFrame, col: str) -> dict | None:
    return r22.offline_groups(frame, col, good_high=True)


def _bh_qvalues(pvalues: list[float | None]) -> list[float | None]:
    clean = [(i, p) for i, p in enumerate(pvalues) if p is not None]
    out: list[float | None] = [None] * len(pvalues)
    if not clean:
        return out
    ordered = sorted(clean, key=lambda item: item[1])
    m = len(ordered)
    running = 1.0
    for rank in range(m, 0, -1):
        index, p = ordered[rank - 1]
        running = min(running, p * m / rank)
        out[index] = running
    return out


def benchmark_metrics(reb: pl.DataFrame) -> dict:
    phases = r33.phase_dates(r40.PHASE_TARGETS)
    equal_weight = (
        reb.filter(pl.col("fwd").is_not_null())
        .group_by("date").agg(pl.col("fwd").mean().alias("r"))
        .sort("date")
        .with_columns(pl.col("date").dt.strftime("%Y-%m").alias("ym"))
        .select(["ym", "r"])
    )
    idx_paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    idx = (
        pl.scan_parquet([str(path) for path in idx_paths], extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"]).collect().sort("date")
        .with_columns(pl.col("date").dt.strftime("%Y-%m").alias("ym"))
    )
    idx_first = idx.group_by("ym", maintain_order=True).agg(pl.col("close").first()).with_columns(
        (pl.col("close") / pl.col("close").shift(1) - 1.0).alias("r")
    ).select(["ym", "r"]).drop_nulls()
    ew_frames = [equal_weight.filter(pl.col("ym") >= phase.strftime("%Y-%m")) for phase in phases]
    idx_frames = [idx_first.filter(pl.col("ym") >= phase.strftime("%Y-%m")) for phase in phases]
    return {
        "phases": [str(phase) for phase in phases],
        "equal_weight": {
            "metrics": r30.phase_metrics(ew_frames),
            "rolling": r30.rolling_windows(ew_frames),
        },
        "hs300_index": {
            "metrics": r30.phase_metrics(idx_frames),
            "rolling": r30.rolling_windows(idx_frames),
        },
    }


def main() -> int:
    panel, reb, _memberships = build_panel()
    reb = _scores(reb)
    print("panel", panel.shape, "reb", reb.shape, "dates", reb["date"].n_unique(), flush=True)
    discovery = _period_frame(reb, DISCOVERY)
    validation = _period_frame(reb, VALIDATION)
    records = {}
    discovery_p = []
    for index, (name, good_high) in enumerate(FACTORS.items()):
        if name not in reb.columns:
            records[name] = {"error": "factor_missing", "direction": "high" if good_high else "low"}
            discovery_p.append(None)
            continue
        sign = 1.0 if good_high else -1.0
        disc = discovery.with_columns((pl.col(name) * sign).alias("_signed"))
        val = validation.with_columns((pl.col(name) * sign).alias("_signed"))
        full = reb.with_columns((pl.col(name) * sign).alias("_signed"))
        disc_stats = _ic_stats(disc, "_signed", RNG_SEED + index, one_sided=False)
        val_stats = _ic_stats(val, "_signed", RNG_SEED + 1000 + index, one_sided=True)
        full_stats = _ic_stats(full, "_signed", RNG_SEED + 2000 + index, one_sided=True)
        disc_group = _group_stats(disc, "_signed")
        val_group = _group_stats(val, "_signed")
        corr = max(
            (
                value for value in (
                    r22.mean_abs_corr(full, "_signed", "v20_score"),
                    r22.mean_abs_corr(full, "_signed", "d1_score"),
                    r22.mean_abs_corr(full, "_signed", "d4_score"),
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
            and r22.yearly_same_sign(full_stats["yearly"], full_stats["mean"] or 0) >= 4
        )
        f2 = bool(
            disc_group and val_group
            and disc_group["monotonic_rho"] >= 0.7 and val_group["monotonic_rho"] >= 0.7
            and disc_group["spread_annual_net2x"] > 0 and val_group["spread_annual_net2x"] > 0
        )
        records[name] = {
            "direction": "high" if good_high else "low",
            "discovery": disc_stats,
            "validation": val_stats,
            "full": full_stats,
            "discovery_groups": disc_group,
            "validation_groups": val_group,
            "corr_existing_max": corr,
            "D1": d1,
            "F1": f1,
            "F2": f2,
        }
        discovery_p.append(disc_stats["perm_p"])
        print(
            f"{name}: D1={d1} discIC={disc_stats['mean']} valIC={val_stats['mean']} "
            f"rho={disc_group['monotonic_rho'] if disc_group else None}/{val_group['monotonic_rho'] if val_group else None} "
            f"p={disc_stats['perm_p']}", flush=True,
        )

    qvalues = _bh_qvalues(discovery_p)
    passed = []
    for (name, _), q in zip(FACTORS.items(), qvalues, strict=False):
        rec = records[name]
        rec["discovery_q"] = q
        rec["F3"] = bool(q is not None and q <= 0.10 and rec["validation"]["perm_p"] is not None and rec["validation"]["perm_p"] <= 0.05)
        rec["all_pass"] = bool(rec.get("D1") and rec.get("F1") and rec.get("F2") and rec.get("F3"))
        if rec["all_pass"]:
            passed.append(name)

    result = {
        "window": {
            "discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])],
            "validation": [str(VALIDATION[0]), str(VALIDATION[1])],
            "factors": list(FACTORS),
            "permutations": PERMUTATIONS,
            "seed": RNG_SEED,
        },
        "benchmarks": benchmark_metrics(reb),
        "factors": records,
        "passed": passed,
        "adopted_for_r43": passed,
        "decision": {
            "D1_all": all(records[name].get("D1", False) for name in FACTORS),
            "F1_count": sum(bool(records[name].get("F1")) for name in FACTORS),
            "F2_count": sum(bool(records[name].get("F2")) for name in FACTORS),
            "F3_count": sum(bool(records[name].get("F3")) for name in FACTORS),
            "all_pass_count": len(passed),
        },
        "caveats": [
            "历史名单来自月度 BaoStock 快照, 不是逐日调样流水",
            "财务字段为本地当前快照 PIT, 存在报表重述风险",
            "float_mv 使用当前 instruments 流通股本, 8 只退市/缺维表标的不覆盖",
            "只筛因子, 不代表单因子可直接部署; R43 才做组合与增量检验",
        ],
    }
    path = EV / "r42_historical_factor_screen.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("saved", path, flush=True)
    print("passed:", passed, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
