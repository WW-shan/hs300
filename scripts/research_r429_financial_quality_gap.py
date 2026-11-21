#!/usr/bin/env python3
"""R429: financial quality/safety gap IC audit."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r429_financial_quality_gap_preregistration.json"
OUTPUT = E / "r429_financial_quality_gap_2026-11-22.json"
RESULT = E / "r429_financial_quality_gap_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r429_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R429 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R429 preregistration checksum mismatch")
    p5 = load_p5()
    from app.backtest import stats_v2
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.signal_scan import calculate_rank_ic, summarise_ic
    from app.hs300.current import current_symbols
    from app.strategy.scoring import neutralize_scoring_columns
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    panel = attach_fundamental_factors(panel, snapshot, prereg["candidates"])
    panel = panel.sort(["symbol", "date"])
    for horizon in prereg["horizons"]:
        panel = panel.with_columns(
            (pl.col("close").shift(-horizon).over("symbol") / pl.col("close") - 1.0).alias(f"forward_return_{horizon}d")
        )
    rows: dict[str, dict[str, Any]] = {}
    raw_p = []
    for factor in prereg["candidates"]:
        if factor not in panel.columns:
            raise RuntimeError(f"R429 missing candidate: {factor}")
        rows[factor] = {}
        for horizon in prereg["horizons"]:
            return_col = f"forward_return_{horizon}d"
            neutral = neutralize_scoring_columns(
                panel.select(["date", "symbol", factor, return_col, "industry_l1", "log_float_mv"]),
                [factor],
                {"industry": "industry_l1", "styles": ["log_float_mv"], "exclude": [], "shrink": 1.0},
            )
            ic = calculate_rank_ic(neutral, factor, return_col, minimum=int(prereg["minimum_cross_section"]))
            summary = summarise_ic(ic, recent_start=date.fromisoformat(prereg["windows"]["recent_start"]), direction=prereg["directions"][factor], nw_lag=horizon)
            rows[factor][str(horizon)] = summary
            raw_p.append((factor, horizon, summary.get("p_value")))
    qs = stats_v2.bh_fdr_qvalues([p for _, _, p in raw_p])
    qmap = {(f, h): q for (f, h, _), q in zip(raw_p, qs, strict=True)}
    passes = []
    for factor, by_h in rows.items():
        for horizon in prereg["horizons"]:
            item = by_h[str(horizon)]
            q = qmap[(factor, horizon)]
            item["q_value"] = round(float(q), 8) if q is not None else None
            checks = {
                "directional_ic_positive": isinstance(item["full"].get("directional_ic_mean"), (int, float)) and item["full"]["directional_ic_mean"] > 0,
                "nw_p_max": isinstance(item.get("p_value"), (int, float)) and item["p_value"] <= 0.10,
                "bh_q_max": isinstance(q, (int, float)) and q <= 0.10,
                "yearly_sign_consistency_min": isinstance(item.get("yearly_sign_consistency"), (int, float)) and item["yearly_sign_consistency"] >= 0.80,
                "recent_directional_ic_positive": isinstance(item["recent"].get("directional_ic_mean"), (int, float)) and item["recent"]["directional_ic_mean"] > 0,
            }
            item["gate"] = {"pass": all(checks.values()), "checks": checks}
            if item["gate"]["pass"]:
                passes.append({"factor": factor, "horizon": horizon, "directional_ic": item["full"]["directional_ic_mean"], "q_value": q})
        rows[factor]["passing_horizons"] = [h for h in prereg["horizons"] if by_h[str(h)]["gate"]["pass"]]
    payload = {"round": "R429", "classification": prereg["classification"], "preregistration_sha256": sha(PREREG), "rows": rows, "pass_rows": passes, "pass_count": len(passes), "decision": prereg["output_policy"], "known_limits": prereg["known_limits"]}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R429 财报质量/安全缺口 IC", "", "| 因子 | 5日 | 20日 | 60日 | 合格周期 |", "| --- | ---: | ---: | ---: | --- |"]
    for factor, by_h in rows.items():
        lines.append(f"| {factor} | {by_h['5']['full'].get('directional_ic_mean')} | {by_h['20']['full'].get('directional_ic_mean')} | {by_h['60']['full'].get('directional_ic_mean')} | {by_h.get('passing_horizons', [])} |")
    lines += ["", f"- 合格候选-周期数：{len(passes)}", "", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
