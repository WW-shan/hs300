#!/usr/bin/env python3
"""R441: research admission for margin_chg_1m_low."""
from __future__ import annotations

import hashlib
import importlib.util
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
PREREG = E / "r441_margin_research_admission_preregistration.json"
OUTPUT = E / "r441_margin_research_admission_2026-11-22.json"
RESULT = E / "r441_margin_research_admission_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r441_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def yearly_consistency(delta: pl.DataFrame) -> float | None:
    if delta.is_empty():
        return None
    yearly = delta.with_columns(pl.col("date").dt.year().alias("year")).group_by("year").agg(pl.col("delta").mean().alias("mean_delta")).sort("year")
    values = yearly["mean_delta"].to_numpy()
    overall = float(np.mean(values)) if values.size else 0.0
    if overall == 0:
        return None
    return round(float(np.mean(np.sign(values) == np.sign(overall))), 6)


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R441 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    p5 = load_p5()
    from app.backtest import stats_v2
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.factor_research.incremental import marginal_ic, rank_score_frame
    from app.factor_research.signal_scan import calculate_rank_ic
    from app.hs300.current import current_symbols

    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    panel = attach_fundamental_factors(panel, snapshot, [])
    panel = panel.join(
        load_existing_ext_factors(DATA, {"ext_alt_hs300_vol_of_vol_60d_low": "vol_of_vol_60d_low"}),
        on=["symbol", "date"],
        how="left",
    ).sort(["symbol", "date"])
    for horizon in prereg["horizons"]:
        panel = panel.with_columns((pl.col("close").shift(-horizon).over("symbol") / pl.col("close") - 1.0).alias(f"forward_return_{horizon}d"))
    work = panel
    for member in prereg["core_members"]:
        if member not in work.columns:
            raise RuntimeError(f"R441 missing core: {member}")
        work = rank_score_frame(work, member, "high", output_column=f"_core_{member}")
    work = work.with_columns(sum(pl.col(f"_core_{member}").fill_null(0.0) for member in prereg["core_members"]).alias("_core"))
    factor = prereg["candidate"]
    rows: dict[str, dict[str, Any]] = {}
    raw_p = []
    recent = date.fromisoformat(prereg["windows"]["recent_start"])
    for horizon in prereg["horizons"]:
        return_col = f"forward_return_{horizon}d"
        work = rank_score_frame(work, factor, "high", output_column=f"_cand_{factor}")
        marginal = marginal_ic(work, "_core", f"_cand_{factor}", return_col, recent_start=recent, minimum_symbols=30, horizon=horizon)
        base = calculate_rank_ic(work, "_core", return_col, minimum=30)
        combined_col = f"_comb_{horizon}"
        combined = work.with_columns((0.8 * pl.col("_core") + 0.2 * pl.col(f"_cand_{factor}")).alias(combined_col))
        combined_ic = calculate_rank_ic(combined, combined_col, return_col, minimum=30)
        paired = base.select(["date", pl.col("ic").alias("base")]).join(combined_ic.select(["date", pl.col("ic").alias("combined")]), on="date", how="inner").with_columns((pl.col("combined") - pl.col("base")).alias("delta"))
        rows[str(horizon)] = {
            "marginal": marginal,
            "yearly_delta_sign_consistency": yearly_consistency(paired),
            "recent_delta_positive": isinstance(marginal.get("recent_directional_delta_mean"), (int, float)) and marginal["recent_directional_delta_mean"] > 0,
        }
        raw_p.append(marginal.get("delta_p_value"))
    qvalues = stats_v2.bh_fdr_qvalues(raw_p)
    for (_horizon, item), q in zip(rows.items(), qvalues, strict=True):
        item["q_value"] = round(float(q), 8) if q is not None else None
        checks = {
            "marginal_ic_positive": isinstance(item["marginal"].get("directional_delta_mean"), (int, float)) and item["marginal"]["directional_delta_mean"] > 0,
            "nw_p_max": isinstance(item["marginal"].get("delta_p_value"), (int, float)) and item["marginal"]["delta_p_value"] <= 0.10,
            "bh_q_max": isinstance(q, (int, float)) and q <= 0.10,
            "yearly_delta_sign_consistency_min": isinstance(item.get("yearly_delta_sign_consistency"), (int, float)) and item["yearly_delta_sign_consistency"] >= 0.80,
            "recent_delta_positive": item["recent_delta_positive"],
            "minimum_n_dates": item["marginal"].get("n_dates", 0) >= 120,
        }
        item["gate"] = {"pass": all(checks.values()), "checks": checks}
    passing = [horizon for horizon, item in rows.items() if item["gate"]["pass"]]
    payload = {
        "round": "R441",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "candidate": factor,
        "already_rejected": prereg["already_rejected"],
        "rows": rows,
        "passing_horizons": passing,
        "admitted": bool(passing),
        "decision": {**prereg["output_policy"], "status": "margin_admission_complete_no_promotion"},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R441 margin_chg 研究准入", "", "| 周期 | 边际IC | NW p | q | 年度同号 | 结论 |", "| ---: | ---: | ---: | ---: | ---: | --- |"]
    for horizon, item in rows.items():
        lines.append(f"| {horizon} | {item['marginal'].get('directional_delta_mean')} | {item['marginal'].get('delta_p_value')} | {item['q_value']} | {item.get('yearly_delta_sign_consistency')} | {'通过' if item['gate']['pass'] else '拒绝'} |")
    lines += ["", f"- 准入：{'是' if passing else '否'}", "- holder_top10 已由 R418 拒绝，未重测。", "", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
