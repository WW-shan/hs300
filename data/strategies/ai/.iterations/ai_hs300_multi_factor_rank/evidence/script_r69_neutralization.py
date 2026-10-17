# 输入说明: R69 — 历史 HS300 市值 + 行业横截面中性化复筛。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r69_neutralization.py
# 结果 JSON: 同目录 r69_neutralization.json。
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
DISCOVERY = r42.DISCOVERY
VALIDATION = r42.VALIDATION
RNG_SEED = r42.RNG_SEED
FACTORS = {
    "annual_vol_20d": False,
    "turnover_z_60d": False,
    "amount": False,
    "momentum_20d": True,
    "pb_latest": False,
    "ocf_ni": True,
}
MIN_VALID = 80


def _neutralize(reb: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    from app.services.industry_map import load_industry_l1

    industry = load_industry_l1(DATA).select(["symbol", "industry_l1"])
    frame = reb.join(industry, on="symbol", how="left").with_columns([
        pl.when(pl.col("float_mv") > 0).then(pl.col("float_mv").log()).otherwise(None).alias("_log_mv"),
    ])
    industry_values = sorted(frame["industry_l1"].drop_nulls().unique().to_list())
    industry_index = {name: i for i, name in enumerate(industry_values[1:])}
    out = frame
    reports = {}
    for factor, good_high in FACTORS.items():
        rows = []
        r2_values = []
        for keys, group in frame.group_by("date", maintain_order=True):
            day = keys[0]
            work = group.filter(
                pl.col(factor).is_not_null() & pl.col("_log_mv").is_not_null() & pl.col("industry_l1").is_not_null()
            ).select(["symbol", factor, "_log_mv", "industry_l1"])
            if work.height < MIN_VALID:
                continue
            y = work[factor].to_numpy().astype(float)
            if good_high is False:
                y = -y
            x = np.ones((work.height, 2 + len(industry_index)), dtype=float)
            x[:, 1] = work["_log_mv"].to_numpy()
            industries = work["industry_l1"].to_list()
            for row, name in enumerate(industries):
                idx = industry_index.get(name)
                if idx is not None:
                    x[row, 2 + idx] = 1.0
            beta, *_ = np.linalg.lstsq(x, y, rcond=None)
            residual = y - x @ beta
            total_var = float(np.var(y))
            r2_values.append(1.0 - float(np.var(residual)) / total_var if total_var > 0 else 0.0)
            rows.extend({"symbol": symbol, "date": day, "_neutral": float(value)} for symbol, value in zip(work["symbol"].to_list(), residual, strict=False))
        rank_col = f"_{factor}_neutral_rank"
        neutral = pl.DataFrame(rows).with_columns(
            (pl.col("_neutral").rank().over("date") / pl.len().over("date")).alias(rank_col)
        ).select(["symbol", "date", rank_col])
        out = out.join(neutral, on=["symbol", "date"], how="left")
        reports[factor] = {
            "monthly_r2_mean": round(float(np.mean(r2_values)), 4) if r2_values else None,
            "monthly_r2_min": round(float(np.min(r2_values)), 4) if r2_values else None,
            "months": len(r2_values),
        }
    coverage = float(frame["industry_l1"].is_not_null().mean())
    return out, {
        "industry_coverage": round(coverage, 6),
        "industries": len(industry_values),
        "factors": reports,
    }


def _coverage(frame: pl.DataFrame, col: str) -> dict:
    grouped = frame.group_by("date").agg([pl.len().alias("eligible"), pl.col(col).is_not_null().sum().alias("valid")])
    if grouped.is_empty():
        return {"mean": None, "min_valid": 0, "months": 0, "months_ge80": 0}
    return {
        "mean": round(float((grouped["valid"] / grouped["eligible"]).mean()), 6),
        "min_valid": int(grouped["valid"].min()), "months": grouped.height,
        "months_ge80": int((grouped["valid"] >= MIN_VALID).sum()),
    }


def _usable(frame: pl.DataFrame, col: str) -> pl.DataFrame:
    dates = frame.group_by("date").agg(pl.col(col).is_not_null().sum().alias("valid")).filter(pl.col("valid") >= MIN_VALID)["date"].to_list()
    return frame.filter(pl.col("date").is_in(dates))


def main() -> int:
    _panel, reb, _memberships = r42.build_panel()
    reb = r42._scores(reb)
    joined, neutral_audit = _neutralize(reb)
    print("reb", reb.shape, "neutral", neutral_audit, flush=True)

    records, discovery_p = {}, []
    for index, (factor, good_high) in enumerate(FACTORS.items()):
        signed_expr = pl.col(factor) if good_high else -pl.col(factor)
        original = joined.with_columns(signed_expr.alias("_signed"))
        disc_orig, val_orig = r42._period_frame(original, DISCOVERY), r42._period_frame(original, VALIDATION)
        neutral_col = f"_{factor}_neutral_rank"
        disc = _usable(r42._period_frame(joined, DISCOVERY), neutral_col)
        val = _usable(r42._period_frame(joined, VALIDATION), neutral_col)
        disc_cov = _coverage(r42._period_frame(joined, DISCOVERY), neutral_col)
        val_cov = _coverage(r42._period_frame(joined, VALIDATION), neutral_col)
        disc_stats = r42._ic_stats(disc, neutral_col, RNG_SEED + index, one_sided=False)
        val_stats = r42._ic_stats(val, neutral_col, RNG_SEED + 1000 + index, one_sided=True)
        full_stats = r42._ic_stats(joined, neutral_col, RNG_SEED + 2000 + index, one_sided=True)
        disc_group = r42._group_stats(disc, neutral_col)
        val_group = r42._group_stats(val, neutral_col)
        original_disc = r42._ic_stats(disc_orig, "_signed", RNG_SEED + 3000 + index, one_sided=False)
        original_val = r42._ic_stats(val_orig, "_signed", RNG_SEED + 4000 + index, one_sided=True)
        size_corr = r22.mean_abs_corr(joined, neutral_col, "_log_mv")
        original_corr = r22.mean_abs_corr(original, neutral_col, "_signed")
        d1 = bool(
            neutral_audit["industry_coverage"] >= 0.90
            and disc_cov["mean"] is not None and disc_cov["mean"] >= 0.70
            and val_cov["mean"] is not None and val_cov["mean"] >= 0.70
            and disc_cov["months_ge80"] >= 24 and val_cov["months_ge80"] >= 24
            and disc_cov["months_ge80"] + val_cov["months_ge80"] >= 48
        )
        f1 = bool(
            disc_stats["mean"] is not None and disc_stats["mean"] >= 0.03
            and val_stats["mean"] is not None and val_stats["mean"] >= 0.02
            and disc_stats["mean"] > 0 and val_stats["mean"] > 0
            and r22.yearly_same_sign(full_stats["yearly"], full_stats["mean"] or 0) >= 4
        )
        f2 = bool(
            disc_group and val_group and disc_group["monotonic_rho"] >= 0.7 and val_group["monotonic_rho"] >= 0.7
            and disc_group["spread_annual_net2x"] > 0 and val_group["spread_annual_net2x"] > 0
        )
        f4 = bool(size_corr is not None and size_corr < 0.10 and original_corr is not None and original_corr < 0.95)
        records[factor] = {
            "direction": "high" if good_high else "low",
            "coverage_discovery": disc_cov, "coverage_validation": val_cov,
            "original": {"discovery": original_disc, "validation": original_val},
            "neutralized": {"discovery": disc_stats, "validation": val_stats, "full": full_stats},
            "discovery_groups": disc_group, "validation_groups": val_group,
            "size_corr": size_corr, "original_corr": original_corr,
            "neutralization": neutral_audit["factors"][factor],
            "D1": d1, "F1": f1, "F2": f2, "F4": f4,
        }
        discovery_p.append(disc_stats["perm_p"])
        print(f"{factor}: D1={d1} cov={disc_cov['mean']}/{val_cov['mean']} origIC={original_disc['mean']}/{original_val['mean']} neutIC={disc_stats['mean']}/{val_stats['mean']} rho={disc_group['monotonic_rho'] if disc_group else None}/{val_group['monotonic_rho'] if val_group else None} sizeCorr={size_corr} origCorr={original_corr}", flush=True)

    qvalues = r42._bh_qvalues(discovery_p)
    passed = []
    for (factor, _), q in zip(FACTORS.items(), qvalues, strict=False):
        rec = records[factor]
        rec["discovery_q"] = q
        rec["F3"] = bool(q is not None and q <= 0.10 and rec["neutralized"]["validation"]["perm_p"] is not None and rec["neutralized"]["validation"]["perm_p"] <= 0.05)
        rec["all_pass"] = bool(rec["D1"] and rec["F1"] and rec["F2"] and rec["F3"] and rec["F4"])
        if rec["all_pass"]:
            passed.append(factor)
    result = {
        "window": {"discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])], "validation": [str(VALIDATION[0]), str(VALIDATION[1])], "factors": list(FACTORS), "permutations": r42.PERMUTATIONS, "seed": RNG_SEED},
        "rules": {"regression": "signed_factor ~ 1 + log(float_mv) + industry_l1", "industry_snapshot": True, "rank": "residual monthly rank", "min_valid": MIN_VALID},
        "audit": neutral_audit,
        "factors": records,
        "passed": passed,
        "adopted_for_r70": passed,
        "decision": {"D1_count": sum(bool(records[n]["D1"]) for n in FACTORS), "F1_count": sum(bool(records[n]["F1"]) for n in FACTORS), "F2_count": sum(bool(records[n]["F2"]) for n in FACTORS), "F3_count": sum(bool(records[n]["F3"]) for n in FACTORS), "F4_count": sum(bool(records[n]["F4"]) for n in FACTORS), "all_pass_count": len(passed)},
        "recommendation": "R70 parameter optimization" if passed else "negative knowledge; neutralization line closed",
        "caveats": ["行业分类为当前快照, 非 PIT", "float_mv 使用当前 instruments 流通股本", "OLS 残差只保证线性中性, 不保证非线性独立", "历史名单为月度快照"],
    }
    path = EV / "r69_neutralization.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("passed", passed, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
