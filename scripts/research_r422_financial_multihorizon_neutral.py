#!/usr/bin/env python3
"""R422: financial factor IC across 5/20/60d with industry+size neutralization."""
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
PREREG = E / "r422_financial_multihorizon_neutral_preregistration.json"
OUTPUT = E / "r422_financial_multihorizon_neutral_2026-11-22.json"
RESULT = E / "r422_financial_multihorizon_neutral_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r422_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ic_with_neutralization(frame: pl.DataFrame, factor: str, horizon: int, prereg: dict[str, Any]) -> dict[str, Any]:
    from app.factor_research.signal_scan import calculate_rank_ic, summarise_ic
    from app.strategy.scoring import neutralize_scoring_columns
    return_col = f"forward_return_{horizon}d"
    spec = {
        "industry": "industry_l1",
        "styles": ["log_float_mv"],
        "exclude": [],
        "shrink": 1.0,
    }
    neutral = neutralize_scoring_columns(
        frame.select(["date", "symbol", factor, return_col, "industry_l1", "log_float_mv"]),
        [factor],
        spec,
    )
    ic = calculate_rank_ic(neutral, factor, return_col, minimum=int(prereg["minimum_cross_section"]))
    summary = summarise_ic(
        ic,
        recent_start=date.fromisoformat(prereg["windows"]["recent_start"]),
        direction=prereg["directions"][factor],
        nw_lag=horizon,
    )
    return summary


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R422 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R422 preregistration checksum mismatch")
    p5 = load_p5()
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.hs300.current import current_symbols
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
    raws = []
    for factor in prereg["candidates"]:
        rows[factor] = {}
        for horizon in prereg["horizons"]:
            summary = ic_with_neutralization(panel, factor, horizon, prereg)
            rows[factor][str(horizon)] = summary
            raws.append((factor, horizon, summary.get("p_value")))
    from app.backtest import stats_v2
    qvalues = stats_v2.bh_fdr_qvalues([item[2] for item in raws])
    qmap = {(factor, horizon): q for (factor, horizon, _), q in zip(raws, qvalues, strict=True)}
    best_rows = []
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
                best_rows.append((factor, horizon, item["full"]["directional_ic_mean"], item.get("p_value"), q))
        candidates = [h for h in prereg["horizons"] if by_h[str(h)]["gate"]["pass"]]
        rows[factor]["passing_horizons"] = candidates
        if candidates:
            best_horizon = max(candidates, key=lambda h: by_h[str(h)]["full"]["directional_ic_mean"])
            rows[factor]["best_horizon"] = best_horizon
    payload = {
        "round": "R422",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "horizons": prereg["horizons"],
        "rows": rows,
        "pass_count": len(best_rows),
        "pass_rows": [
            {"factor": factor, "horizon": horizon, "directional_ic": ic, "p_value": p, "q_value": q}
            for factor, horizon, ic, p, q in best_rows
        ],
        "decision": prereg["output_policy"],
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R422 财报多周期中性化 IC",
        "",
        "| 因子 | 5日中性IC | 20日中性IC | 60日中性IC | 合格周期 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for factor, by_h in rows.items():
        vals = []
        for horizon in prereg["horizons"]:
            vals.append(by_h[str(horizon)]["full"].get("directional_ic_mean"))
        lines.append(f"| {factor} | {vals[0]} | {vals[1]} | {vals[2]} | {by_h.get('passing_horizons', [])} |")
    lines += ["", f"- 合格候选-周期数：{len(best_rows)}", "", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
