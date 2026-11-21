#!/usr/bin/env python3
"""R431: marginal IC of financial quality factors against current 8-leg composite."""
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
PREREG = E / "r431_financial_marginal_ic_preregistration.json"
OUTPUT = E / "r431_financial_marginal_ic_2026-11-22.json"
RESULT = E / "r431_financial_marginal_ic_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r431_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def yearly_sign_consistency(delta: pl.DataFrame) -> float | None:
    if delta.is_empty():
        return None
    yearly = (
        delta.with_columns(pl.col("date").dt.year().alias("year"))
        .group_by("year")
        .agg(pl.col("delta").mean().alias("mean_delta"))
        .sort("year")
    )
    values = yearly["mean_delta"].to_numpy()
    if values.size == 0:
        return None
    overall = float(np.mean(values))
    if overall == 0:
        return None
    return round(float(np.mean(np.sign(values) == np.sign(overall))), 6)


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R431 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R431 preregistration checksum mismatch")
    p5 = load_p5()
    from app.backtest import stats_v2
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.incremental import marginal_ic, rank_score_frame
    from app.factor_research.signal_scan import calculate_rank_ic
    from app.hs300.current import current_symbols
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    panel = attach_fundamental_factors(panel, snapshot, prereg["candidates"])
    panel = panel.sort(["symbol", "date"])
    for horizon in prereg["horizons"]:
        panel = panel.with_columns((pl.col("close").shift(-horizon).over("symbol") / pl.col("close") - 1.0).alias(f"forward_return_{horizon}d"))
    work = panel
    for member in prereg["baseline_members"]:
        if member not in work.columns:
            raise RuntimeError(f"R431 missing baseline: {member}")
        work = rank_score_frame(work, member, "high", output_column=f"_base_{member}")
    work = work.with_columns(sum(pl.col(f"_base_{member}").fill_null(0.0) for member in prereg["baseline_members"]).alias("_base_composite"))
    rows: dict[str, dict[str, Any]] = {}
    raw_p = []
    recent = date.fromisoformat(prereg["windows"]["recent_start"])
    for factor in prereg["candidates"]:
        rows[factor] = {}
        for horizon in prereg["horizons"]:
            return_col = f"forward_return_{horizon}d"
            work = rank_score_frame(work, factor, prereg["directions"][factor], output_column=f"_cand_{factor}")
            inc = marginal_ic(work, "_base_composite", f"_cand_{factor}", return_col, recent_start=recent, minimum_symbols=30, horizon=horizon)
            base_ic = calculate_rank_ic(work, "_base_composite", return_col, minimum=30)
            combined_col = f"_combined_{factor}_{horizon}"
            combined = work.with_columns((0.8 * pl.col("_base_composite") + 0.2 * pl.col(f"_cand_{factor}")).alias(combined_col))
            comb_ic = calculate_rank_ic(combined, combined_col, return_col, minimum=30)
            paired = base_ic.select(["date", pl.col("ic").alias("base")]).join(comb_ic.select(["date", pl.col("ic").alias("combined")]), on="date", how="inner").with_columns((pl.col("combined") - pl.col("base")).alias("delta"))
            consistency = yearly_sign_consistency(paired)
            rows[factor][str(horizon)] = {"marginal": inc, "yearly_delta_sign_consistency": consistency, "recent_delta_positive": isinstance(inc.get("recent_directional_delta_mean"), (int, float)) and inc["recent_directional_delta_mean"] > 0}
            raw_p.append((factor, horizon, inc.get("delta_p_value")))
    qs = stats_v2.bh_fdr_qvalues([p for _, _, p in raw_p])
    qmap = {(f, h): q for (f, h, _), q in zip(raw_p, qs, strict=True)}
    passes = []
    for factor, by_h in rows.items():
        for horizon in prereg["horizons"]:
            item = by_h[str(horizon)]
            q = qmap[(factor, horizon)]
            item["q_value"] = round(float(q), 8) if q is not None else None
            checks = {
                "directional_delta_positive": isinstance(item["marginal"].get("directional_delta_mean"), (int, float)) and item["marginal"]["directional_delta_mean"] > 0,
                "nw_p_max": isinstance(item["marginal"].get("delta_p_value"), (int, float)) and item["marginal"]["delta_p_value"] <= 0.10,
                "bh_q_max": isinstance(q, (int, float)) and q <= 0.10,
                "yearly_delta_sign_consistency_min": isinstance(item.get("yearly_delta_sign_consistency"), (int, float)) and item["yearly_delta_sign_consistency"] >= 0.80,
                "recent_delta_positive": item["recent_delta_positive"],
                "minimum_n_dates": item["marginal"].get("n_dates", 0) >= 120,
            }
            item["gate"] = {"pass": all(checks.values()), "checks": checks}
            if item["gate"]["pass"]:
                passes.append({"factor": factor, "horizon": horizon, "directional_delta": item["marginal"]["directional_delta_mean"], "q_value": q})
        by_h["passing_horizons"] = [h for h in prereg["horizons"] if by_h[str(h)]["gate"]["pass"]]
    payload = {"round": "R431", "classification": prereg["classification"], "preregistration_sha256": sha(PREREG), "rows": rows, "pass_rows": passes, "pass_count": len(passes), "decision": prereg["output_policy"], "known_limits": prereg["known_limits"]}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R431 财报质量对当前 8 腿的边际 IC", "", "| 因子 | 5日增量 | 20日增量 | 60日增量 | 合格周期 |", "| --- | ---: | ---: | ---: | --- |"]
    for factor, by_h in rows.items():
        lines.append(f"| {factor} | {by_h['5']['marginal'].get('directional_delta_mean')} | {by_h['20']['marginal'].get('directional_delta_mean')} | {by_h['60']['marginal'].get('directional_delta_mean')} | {by_h.get('passing_horizons', [])} |")
    lines += ["", f"- 合格候选-周期数：{len(passes)}", "", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
