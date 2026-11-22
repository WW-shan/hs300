#!/usr/bin/env python3
"""R457: weight-neighborhood and moving-block stability for value_quality."""
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
PREREG = E / "r457_value_quality_weight_stability_preregistration.json"
OUTPUT = E / "r457_value_quality_weight_stability_2026-11-22.json"
RESULT = E / "r457_value_quality_weight_stability_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r457_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ic_series(frame: pl.DataFrame, column: str) -> pl.DataFrame:
    from app.factor_research.signal_scan import calculate_rank_ic
    from app.strategy.scoring import neutralize_scoring_columns
    neutral = neutralize_scoring_columns(
        frame.select(["date", "symbol", column, "forward_return_60d", "industry_l1", "log_float_mv"]),
        [column],
        {"industry": "industry_l1", "styles": ["log_float_mv"], "exclude": [], "shrink": 1.0},
    )
    return calculate_rank_ic(neutral, column, "forward_return_60d", minimum=30)


def moving_block_bootstrap(values: np.ndarray, *, block: int, paths: int, seed: int) -> dict[str, float]:
    from app.factor_research.signal_scan import block_bootstrap_mean_ci
    return block_bootstrap_mean_ci(values, block_size=block, n_bootstrap=paths, seed=seed)


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R457 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R457 preregistration checksum mismatch")
    p5 = load_p5()
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.incremental import rank_score_frame
    from app.factor_research.signal_scan import summarise_ic
    from app.hs300.current import current_symbols
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    components = list(prereg["components"])
    panel = attach_fundamental_factors(panel, snapshot, components).sort(["symbol", "date"])
    panel = panel.with_columns((pl.col("close").shift(-60).over("symbol") / pl.col("close") - 1.0).alias("forward_return_60d"))
    for component in components:
        panel = rank_score_frame(panel, component, prereg["directions"][component], output_column=f"_z_{component}")
    panel = panel.with_columns(
        sum(pl.col(f"_z_{component}").is_not_null().cast(pl.Int8) for component in components).alias("_coverage")
    ).filter(pl.col("_coverage") == len(components))
    step = float(prereg["grid"]["step"])
    values = np.arange(0.0, 1.0 + step / 2.0, step)
    rows = []
    for x in values:
        for y in values:
            z = 1.0 - x - y
            if z < -1e-12:
                continue
            z = max(0.0, z)
            weights = {"pb_latest": x, "ocf_debt": y, "debt_ratio_latest": z}
            if sum(weights.values()) <= 0:
                continue
            column = "_weight_variant"
            panel = panel.with_columns(
                sum(float(weights[component]) * pl.col(f"_z_{component}").fill_null(0.0) for component in components).alias(column)
            )
            ic = ic_series(panel, column)
            summary = summarise_ic(ic, recent_start=date.fromisoformat("2025-10-14"), direction="high", nw_lag=60)
            rows.append({"weights": {key: round(value, 8) for key, value in weights.items()}, "ic": summary["full"]["directional_ic_mean"], "p_value": summary.get("p_value"), "yearly_sign_consistency": summary.get("yearly_sign_consistency"), "recent_ic": summary["recent"]["directional_ic_mean"], "n_dates": summary["full"]["n_dates"], "series": ic})
    center = prereg["center_weights"]
    center_row = next(row for row in rows if all(abs(row["weights"][key] - center[key]) < 1e-8 for key in components))
    center_series = center_row["series"]["ic"].to_numpy()
    bootstrap = moving_block_bootstrap(center_series, block=int(prereg["grid"]["block_days"]), paths=int(prereg["grid"]["bootstrap_replications"]), seed=int(prereg["grid"]["seed"]))
    neighborhoods = []
    for row in rows:
        distance = sum(abs(row["weights"][key] - center[key]) for key in components)
        if 0.0 < distance <= float(prereg["gates"]["min_distance_to_center"]):
            neighborhoods.append(row)
    neighbor_values = [float(row["ic"]) for row in neighborhoods]
    positive_weight_variants = [row for row in rows if all(value > 0 for value in row["weights"].values())]
    checks = {
        "center_ic_positive": isinstance(center_row["ic"], (int, float)) and center_row["ic"] > 0,
        "center_newey_west_p_le": isinstance(center_row["p_value"], (int, float)) and center_row["p_value"] <= 0.10,
        "neighbor_median_ic_positive": bool(neighbor_values) and float(np.median(neighbor_values)) > 0,
        "neighbor_5pct_ic_positive": bool(neighbor_values) and float(np.quantile(neighbor_values, 0.05)) > 0,
        "bootstrap_ci_low_positive": float(bootstrap["ci_low"]) > 0,
        "all_positive_weight_variants_positive": all(float(row["ic"]) > 0 for row in positive_weight_variants),
    }
    payload = {
        "round": "R457",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "center": {key: value for key, value in center_row.items() if key != "series"},
        "bootstrap": bootstrap,
        "neighbor_count": len(neighborhoods),
        "neighbor_ic": {"min": min(neighbor_values), "p05": float(np.quantile(neighbor_values, 0.05)), "median": float(np.median(neighbor_values)), "max": max(neighbor_values)},
        "variants": [{key: value for key, value in row.items() if key != "series"} for row in rows],
        "gate": {"pass": all(checks.values()), "checks": checks},
        "decision": {**prereg["output_policy"], "status": "weight_stability_admitted_no_portfolio_promotion" if all(checks.values()) else "weight_stability_rejected"},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R457 value_quality 权重稳定性", "", f"- 变体数：{len(rows)}", f"- 中心权重：{center}", f"- 中心 60 日中性 IC：{center_row['ic']:.6f}", f"- 邻域（L1≤{prereg['gates']['min_distance_to_center']}）中位 IC：{np.median(neighbor_values):.6f}", f"- 邻域 5% 分位 IC：{np.quantile(neighbor_values, 0.05):.6f}", f"- 中心 moving-block bootstrap 95% 下界：{bootstrap['ci_low']:.6f}", "", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
