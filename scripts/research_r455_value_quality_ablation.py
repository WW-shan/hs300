#!/usr/bin/env python3
"""R455: component ablation for the R452 value_quality composite."""
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
PREREG = E / "r455_value_quality_ablation_preregistration.json"
OUTPUT = E / "r455_value_quality_ablation_2026-11-22.json"
RESULT = E / "r455_value_quality_ablation_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r455_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def rank_z(frame: pl.DataFrame, factor: str, direction: str, output: str) -> pl.DataFrame:
    from app.factor_research.incremental import rank_score_frame
    return rank_score_frame(frame, factor, direction, output_column=output)


def summary(frame: pl.DataFrame, column: str, return_col: str, horizon: int, recent_start: date, minimum: int) -> dict[str, Any]:
    from app.factor_research.signal_scan import calculate_rank_ic, summarise_ic
    from app.strategy.scoring import neutralize_scoring_columns
    neutral = neutralize_scoring_columns(
        frame.select(["date", "symbol", column, return_col, "industry_l1", "log_float_mv"]),
        [column],
        {"industry": "industry_l1", "styles": ["log_float_mv"], "exclude": [], "shrink": 1.0},
    )
    ic = calculate_rank_ic(neutral, column, return_col, minimum=minimum)
    return summarise_ic(ic, recent_start=recent_start, direction="high", nw_lag=horizon)


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R455 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R455 preregistration checksum mismatch")
    p5 = load_p5()
    from app.backtest import stats_v2
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.hs300.current import current_symbols
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    panel = attach_fundamental_factors(panel, snapshot, ["pb_latest", "ocf_debt", "debt_ratio_latest"]).sort(["symbol", "date"])
    for horizon in prereg["horizons"]:
        panel = panel.with_columns((pl.col("close").shift(-horizon).over("symbol") / pl.col("close") - 1.0).alias(f"forward_return_{horizon}d"))
    for component, spec in prereg["components"].items():
        panel = rank_z(panel, component, spec["direction"], f"_z_{component}")
    panel = panel.with_columns(
        sum(pl.col(f"_z_{component}").is_not_null().cast(pl.Int8) for component in prereg["components"]).alias("_component_coverage")
    ).filter(pl.col("_component_coverage") == 3)
    rows = {}
    raw_p = []
    for variant in prereg["variants"]:
        weights = variant["weights"]
        column = f"_composite_{variant['name']}"
        panel = panel.with_columns(
            sum(float(weights[component]) * pl.col(f"_z_{component}").fill_null(0.0) for component in prereg["components"]).alias(column)
        )
        rows[variant["name"]] = {"weights": weights, "horizons": {}}
        for horizon in prereg["horizons"]:
            item = summary(panel, column, f"forward_return_{horizon}d", horizon, date.fromisoformat(prereg["recent_start"]), int(prereg["minimum_cross_section"]))
            rows[variant["name"]]["horizons"][str(horizon)] = item
            raw_p.append((variant["name"], horizon, item.get("p_value")))
    qvalues = stats_v2.bh_fdr_qvalues([p for _, _, p in raw_p])
    qmap = {(name, horizon): q for (name, horizon, _), q in zip(raw_p, qvalues, strict=True)}
    for name, row in rows.items():
        for horizon in prereg["horizons"]:
            item = row["horizons"][str(horizon)]
            q = qmap[(name, horizon)]
            item["q_value"] = round(float(q), 8) if q is not None else None
            item["gate"] = {
                "pass": isinstance(item.get("p_value"), (int, float))
                and item["p_value"] <= 0.10
                and isinstance(q, (int, float))
                and q <= 0.10
                and isinstance(item.get("yearly_sign_consistency"), (int, float))
                and item["yearly_sign_consistency"] >= 0.80
                and item["full"]["directional_ic_mean"] > 0
                and item["recent"]["directional_ic_mean"] > 0
            }
    payload = {
        "round": "R455",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "rows": rows,
        "decision": prereg["output_policy"],
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R455 财报质量复合因子消融", "", "| 变体 | 5日IC | 20日IC | 60日IC | 60日 p | 60日 q | 60日年度同号 | 60日近一年 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, row in rows.items():
        by_h = row["horizons"]
        lines.append(f"| {name} | {by_h['5']['full']['directional_ic_mean']:.6f} | {by_h['20']['full']['directional_ic_mean']:.6f} | {by_h['60']['full']['directional_ic_mean']:.6f} | {by_h['60'].get('p_value')} | {by_h['60']['q_value']} | {by_h['60'].get('yearly_sign_consistency')} | {by_h['60']['recent']['directional_ic_mean']:.6f} |")
    lines += ["", "## 解释", "", "单腿 IC 不能说明边际贡献；这里用同一中性化口径拆开复合因子，判断收益来自低 PB、现金流质量，还是低负债。", "", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
