#!/usr/bin/env python3
"""R501: industry/size-neutral attribution for frozen R499 legs."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r501_r499_industry_size_neutral_attribution_preregistration.json"
R499 = E / "r499_weak_state_value_quality_tilt_2026-11-22.json"
R500 = E / "r500_r499_recent_tail_attribution_2026-11-22.json"
OUTPUT = E / "r501_r499_industry_size_neutral_attribution_2026-11-22.json"
RESULT = E / "r501_r499_industry_size_neutral_attribution_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R501 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def window_ic(
    panel: pl.DataFrame, factor: str, return_column: str, *, minimum: int
) -> dict[str, Any]:
    required = {"date", "symbol", factor, return_column}
    missing = sorted(required - set(panel.columns))
    if missing:
        raise ValueError(f"window_ic missing columns: {missing}")
    work = panel.select(["date", "symbol", factor, return_column]).filter(
        pl.col(factor).is_not_null()
        & pl.col(factor).is_finite()
        & pl.col(return_column).is_not_null()
        & pl.col(return_column).is_finite()
    )
    work = work.with_columns(
        pl.col(factor).rank("average").over("date").alias("_rank_factor"),
        pl.col(return_column).rank("average").over("date").alias("_rank_return"),
    )
    daily = (
        work.group_by("date")
        .agg(
            pl.len().alias("n"),
            pl.corr(pl.col("_rank_factor"), pl.col("_rank_return")).alias("ic"),
        )
        .filter((pl.col("n") >= minimum) & pl.col("ic").is_not_null())
        .sort("date")
    )
    values = daily["ic"].to_numpy() if daily.height else np.array([], dtype=float)
    return {
        "n_dates": len(values),
        "mean_ic": round(float(values.mean()), 8) if values.size else None,
        "positive_rate": round(float((values > 0).mean()), 6) if values.size else None,
    }


def yearly_sign_consistency(daily: pl.DataFrame) -> float | None:
    if daily.is_empty():
        return None
    yearly = (
        daily.with_columns(pl.col("date").dt.year().alias("year"))
        .group_by("year")
        .agg(pl.col("ic").mean().alias("mean"))
        .sort("year")
    )
    values = yearly["mean"].to_numpy()
    total = float(values.mean()) if values.size else 0.0
    sign = float(np.sign(total)) if total else 0.0
    if not values.size or not sign:
        return None
    return round(float((np.sign(values) == sign).mean()), 6)


def ic_detail(
    panel: pl.DataFrame, factor: str, return_column: str, *, minimum: int
) -> dict[str, Any]:
    work = panel.select(["date", "symbol", factor, return_column]).filter(
        pl.col(factor).is_not_null()
        & pl.col(factor).is_finite()
        & pl.col(return_column).is_not_null()
        & pl.col(return_column).is_finite()
    )
    work = work.with_columns(
        pl.col(factor).rank("average").over("date").alias("_rank_factor"),
        pl.col(return_column).rank("average").over("date").alias("_rank_return"),
    )
    daily = (
        work.group_by("date")
        .agg(
            pl.len().alias("n"),
            pl.corr(pl.col("_rank_factor"), pl.col("_rank_return")).alias("ic"),
        )
        .filter((pl.col("n") >= minimum) & pl.col("ic").is_not_null())
        .sort("date")
    )
    values = daily["ic"].to_numpy() if daily.height else np.array([], dtype=float)
    return {
        "n_dates": len(values),
        "mean_ic": round(float(values.mean()), 8) if values.size else None,
        "positive_rate": round(float((values > 0).mean()), 6) if values.size else None,
        "yearly_sign_consistency": yearly_sign_consistency(daily),
    }


def build_panel(p5: Any) -> tuple[pl.DataFrame, list[str]]:
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.factor_research.incremental import rank_score_frame
    from app.hs300.current import current_symbols

    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    panel = attach_fundamental_factors(
        panel, snapshot, ["pb_latest", "ocf_debt", "debt_ratio_latest"]
    )
    ext = load_existing_ext_factors(
        DATA,
        {
            "ext_alt_hs300_cash_flow_stability_4q_low": "cash_flow_stability_4q_low",
            "ext_alt_hs300_dividend_yield": "dividend_yield",
            "ext_alt_hs300_fund_holding_ratio_low": "fund_holding_ratio_low",
            "ext_alt_hs300_idio_vol_40d_low": "idio_vol_40d_low",
        },
    )
    panel = panel.join(ext, on=["symbol", "date"], how="left").sort(["symbol", "date"])
    for member, direction in (
        ("pb_latest", "low"),
        ("ocf_debt", "high"),
        ("debt_ratio_latest", "low"),
    ):
        panel = rank_score_frame(panel, member, direction, output_column=f"_r501_vq_{member}")
    panel = panel.with_columns(
        (
            pl.col("_r501_vq_pb_latest").fill_null(0.0)
            + pl.col("_r501_vq_ocf_debt").fill_null(0.0)
            + pl.col("_r501_vq_debt_ratio_latest").fill_null(0.0)
        ).alias("value_quality"),
        (
            pl.col("_r501_vq_pb_latest").is_not_null()
            & pl.col("_r501_vq_ocf_debt").is_not_null()
            & pl.col("_r501_vq_debt_ratio_latest").is_not_null()
        ).alias("_r501_vq_valid"),
    ).filter(pl.col("_r501_vq_valid"))
    panel = panel.with_columns(
        (pl.col("close").shift(-5).over("symbol") / pl.col("close") - 1.0).alias(
            "forward_return_5d"
        )
    )
    return panel, sorted(panel["symbol"].unique().to_list())


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    verify_sidecar(R499)
    verify_sidecar(R500)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r499 = json.loads(R499.read_text(encoding="utf-8"))
    r500 = json.loads(R500.read_text(encoding="utf-8"))
    if float(r499["selected"]["weak_value_quality"]) != 0.05:
        raise RuntimeError("R499 selected sample changed")
    if r500["source_round"] != "R499":
        raise RuntimeError("R500 source changed")

    r453 = load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r501_r453")
    from app.strategy.scoring import neutralize_scoring_columns

    p5 = r453.load_p5()
    panel, symbols = build_panel(p5)
    del symbols
    windows = prereg["windows"]
    output: dict[str, Any] = {}
    for window in ("train", "validation", "test", "recent"):
        start = windows[f"{window}_start"]
        end = windows["test_end"] if window == "recent" else windows[f"{window}_end"]
        scoped = panel.filter(
            (pl.col("date") >= date.fromisoformat(start))
            & (pl.col("date") <= date.fromisoformat(end))
        )
        output[window] = {}
        for factor in prereg["legs"]:
            raw = ic_detail(
                scoped,
                factor,
                prereg["return_column"],
                minimum=int(prereg["minimum_cross_section"]),
            )
            neutral_frame = neutralize_scoring_columns(
                scoped.select(
                    [
                        "date",
                        "symbol",
                        factor,
                        prereg["return_column"],
                        "industry_l1",
                        "log_float_mv",
                    ]
                ),
                [factor],
                prereg["neutralization"],
            )
            neutral = ic_detail(
                neutral_frame,
                factor,
                prereg["return_column"],
                minimum=int(prereg["minimum_cross_section"]),
            )
            output[window][factor] = {
                "raw": raw,
                "neutral": neutral,
                "raw_minus_neutral": round(
                    float(raw["mean_ic"] or 0.0) - float(neutral["mean_ic"] or 0.0), 8
                ),
                "neutral_sign_matches_raw": bool(
                    raw["mean_ic"] is not None
                    and neutral["mean_ic"] is not None
                    and np.sign(raw["mean_ic"]) == np.sign(neutral["mean_ic"])
                ),
            }

    payload = {
        "round": "R501",
        "classification": prereg["classification"],
        "source_rounds": prereg["source_rounds"],
        "source_r499_sha256": sha(R499),
        "source_r500_sha256": sha(R500),
        "selection_performed": False,
        "neutralization": prereg["neutralization"],
        "windows": output,
        "decision": {**prereg["output_policy"]},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )

    lines = [
        "# R501 R499 行业+规模中性归因",
        "",
        "本轮只做冻结腿的行业+规模中性诊断，不重新选样。",
        "",
        "| 窗口 | 因子 | 原始IC | 中性IC | 差 | 方向保持 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for window in ("train", "validation", "test", "recent"):
        for factor, row in output[window].items():
            raw_ic = row["raw"]["mean_ic"]
            neutral_ic = row["neutral"]["mean_ic"]
            lines.append(
                f"| {window} | `{factor}` | {raw_ic if raw_ic is not None else '-'} | "
                f"{neutral_ic if neutral_ic is not None else '-'} | "
                f"{row['raw_minus_neutral']:+.6f} | "
                f"{'是' if row['neutral_sign_matches_raw'] else '否'} |"
            )
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii"
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
