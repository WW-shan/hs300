#!/usr/bin/env python3
"""R450: industry+size-neutral amendment audit for the five R418 admissions."""
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
PREREG = E / "r450_r418_neutral_amendment_preregistration.json"
OUTPUT = E / "r450_r418_neutral_amendment_2026-11-22.json"
RESULT = E / "r450_r418_neutral_amendment_2026-11-22.md"
R418 = E / "r418_factor_admission_audit_2026-11-22.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r450_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R450 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R450 preregistration checksum mismatch")
    from app.backtest import stats_v2
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.factor_research.signal_scan import calculate_rank_ic, summarise_ic
    from app.hs300.current import current_symbols
    from app.strategy.scoring import neutralize_scoring_columns
    p5 = load_p5()
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    extra = load_existing_ext_factors(DATA, {"ext_alt_hs300_vol_of_vol_60d_low": "vol_of_vol_60d_low"})
    panel = panel.join(extra, on=["symbol", "date"], how="left").sort(["symbol", "date"])
    horizon = int(prereg["horizon"])
    return_column = f"forward_return_{horizon}d"
    panel = panel.with_columns(
        (pl.col("close").shift(-horizon).over("symbol") / pl.col("close") - 1.0).alias(return_column)
    )
    factors = list(prereg["factors"])
    rows = {}
    ps = []
    for factor in factors:
        neutral = neutralize_scoring_columns(
            panel.select(["date", "symbol", factor, return_column, "industry_l1", "log_float_mv"]),
            [factor],
            {"industry": "industry_l1", "styles": ["log_float_mv"], "exclude": [], "shrink": 1.0},
        )
        ic = calculate_rank_ic(neutral, factor, return_column, minimum=int(prereg["minimum_cross_section"]))
        summary = summarise_ic(ic, recent_start=date.fromisoformat("2025-10-14"), direction="high", nw_lag=horizon)
        row = {
            "directional_ic": summary["full"].get("directional_ic_mean"),
            "p_value": summary.get("p_value"),
            "yearly_sign_consistency": summary.get("yearly_sign_consistency"),
            "recent_directional_ic": summary["recent"].get("directional_ic_mean"),
            "valid_cross_sections": ic.height,
        }
        checks = {
            "directional_ic_positive": isinstance(row["directional_ic"], (int, float)) and row["directional_ic"] > 0,
            "newey_west_p_le": isinstance(row["p_value"], (int, float)) and row["p_value"] <= prereg["rules"]["newey_west_p_le"],
            "yearly_sign_consistency_min": isinstance(row["yearly_sign_consistency"], (int, float)) and row["yearly_sign_consistency"] >= prereg["rules"]["yearly_sign_consistency_min"],
            "recent_directional_ic_positive": isinstance(row["recent_directional_ic"], (int, float)) and row["recent_directional_ic"] > 0,
        }
        row["checks"] = checks
        row["admitted_under_amendment"] = all(checks.values())
        rows[factor] = row
        ps.append(row["p_value"])
    qvalues = stats_v2.bh_fdr_qvalues([p if isinstance(p, (int, float)) else None for p in ps])
    for (_factor, row), q in zip(rows.items(), qvalues, strict=True):
        row["q_value"] = round(float(q), 8) if q is not None else None
        row["checks"]["bh_q_le_0_10"] = isinstance(q, (int, float)) and q <= 0.10
        row["admitted_under_amendment"] = all(row["checks"].values())
    admitted = sorted(f for f, row in rows.items() if row["admitted_under_amendment"])
    payload = {
        "round": "R450",
        "classification": prereg["classification"],
        "source_round": "R418",
        "r418_sha256": sha(R418),
        "preregistration_sha256": sha(PREREG),
        "rows": rows,
        "admitted": admitted,
        "admitted_count": len(admitted),
        "decision": prereg["output_policy"],
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R450 R418 中性化补充审计", "", "| 因子 | 60日行业+规模中性IC | NW p | q | 年度同号 | 近一年IC | 补充准入 |", "| --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    for factor, row in rows.items():
        lines.append(f"| `{factor}` | {row['directional_ic']:.4f} | {row['p_value']} | {row['q_value']} | {row['yearly_sign_consistency']} | {row['recent_directional_ic']:.4f} | {'是' if row['admitted_under_amendment'] else '否'} |")
    lines += ["", f"- 补充准入数量：{len(admitted)}/{len(rows)}", f"- 名单：{', '.join(admitted) if admitted else '无'}", "", "## 结论", "", "R418 原准入证据保留；本审计只回答行业+规模中性后信号是否仍成立，不生成订单或生产配置。", "", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
