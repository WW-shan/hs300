#!/usr/bin/env python3
"""R460: T+1 emotion/macro condition audit for factor sleeves."""
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
PREREG = E / "r460_emotion_macro_t1_condition_preregistration.json"
OUTPUT = E / "r460_emotion_macro_t1_condition_2026-11-22.json"
RESULT = E / "r460_emotion_macro_t1_condition_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r460_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def month_end_states(frame: pl.DataFrame, column: str, min_history: int) -> pl.DataFrame:
    """Compute trailing tercile labels and the next-trading-day effective date."""
    daily = (
        frame.group_by("date")
        .agg(pl.col(column).drop_nulls().first().alias("value"))
        .sort("date")
        .with_columns(pl.col("date").dt.truncate("1mo").alias("month"))
        .group_by("month")
        .agg(pl.col("value").drop_nulls().last().alias("value"))
        .sort("month")
    )
    values = daily["value"].to_numpy()
    states: list[str | None] = []
    for index, value in enumerate(values):
        history = values[max(0, index - min_history) : index]
        if len(history) < min_history or value is None:
            states.append(None)
            continue
        low, high = np.quantile(history, [1 / 3, 2 / 3])
        states.append("low" if value <= low else "high" if value >= high else "mid")
    label_by_month = dict(zip(daily["month"].to_list(), states, strict=True))
    trading_dates = sorted(frame["date"].unique().to_list())
    month_last_day = {}
    for day in trading_dates:
        month_last_day[day.replace(day=1)] = day
    effective_labels = []
    for day in trading_dates:
        # Find the most recent month-end that is strictly before this date.
        effective = None
        for month_start, last_day in sorted(month_last_day.items(), reverse=True):
            if last_day < day and label_by_month.get(month_start) is not None:
                effective = label_by_month[month_start]
                break
        effective_labels.append(effective)
    return pl.DataFrame({"date": trading_dates, "state": effective_labels}).sort("date")


def ic_series(frame: pl.DataFrame, sleeve_column: str, return_column: str, state_column: str) -> pl.DataFrame:
    from app.factor_research.signal_scan import calculate_rank_ic
    clean = frame.filter(
        pl.col(sleeve_column).is_not_null()
        & pl.col(return_column).is_not_null()
        & pl.col(state_column).is_not_null()
    )
    return calculate_rank_ic(clean, sleeve_column, return_column, minimum=30).join(
        clean.select(["date", state_column]).unique(), on="date", how="left"
    ).sort("date")


def block_bootstrap(values: np.ndarray, *, block: int, paths: int, seed: int) -> dict[str, float]:
    from app.factor_research.signal_scan import block_bootstrap_mean_ci
    return block_bootstrap_mean_ci(values, block_size=block, n_bootstrap=paths, seed=seed)


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R460 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R460 preregistration checksum mismatch")
    p5 = load_p5()
    from app.backtest import stats_v2
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.factor_research.incremental import rank_score_frame
    from app.hs300.current import current_symbols
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    panel = attach_fundamental_factors(panel, snapshot, ["pb_latest", "ocf_debt", "debt_ratio_latest"])
    conditioner_ids = list(prereg["conditioners"])
    ext = load_existing_ext_factors(
        DATA,
        {
            "ext_alt_hs300_idio_vol_40d_low": "idio_vol_40d_low",
            **{item: item.removeprefix("ext_alt_hs300_") for item in conditioner_ids},
        },
    )
    panel = panel.join(ext, on=["symbol", "date"], how="left").sort(["symbol", "date"])
    for horizon in prereg["return_horizons"]:
        panel = panel.with_columns((pl.col("close").shift(-horizon).over("symbol") / pl.col("close") - 1.0).alias(f"forward_return_{horizon}d"))
    for sleeve in prereg["sleeves"].values():
        name = next(iter(sleeve["weights"])) if len(sleeve["weights"]) == 1 else "_".join(sleeve["members"])
        score_col = f"_sleeve_{name}"
        score = None
        coverage = None
        for factor, weight in sleeve["weights"].items():
            direction = prereg["factors"][factor]
            rank_col = f"_z_{name}_{factor}"
            panel = rank_score_frame(panel, factor, direction, output_column=rank_col)
            term = float(weight) * pl.col(rank_col).fill_null(0.0)
            missing = pl.col(rank_col).is_null().cast(pl.Int8)
            score = term if score is None else score + term
            coverage = missing if coverage is None else coverage + missing
        panel = panel.with_columns(score.alias(score_col), (coverage == 0).alias(f"_valid_{name}"))
    rows = {}
    raw_p = []
    for conditioner in conditioner_ids:
        state_frame = month_end_states(panel, conditioner, int(prereg["conditioning"]["minimum_history_months"]))
        state_column = f"_state_{conditioner}"
        joined = panel.join(state_frame.rename({"state": state_column}), on="date", how="left")
        rows[conditioner] = {}
        for sleeve_name, sleeve in prereg["sleeves"].items():
            sleeve_name = next(iter(sleeve["weights"])) if len(sleeve["weights"]) == 1 else "_".join(sleeve["members"])
            rows[conditioner][sleeve_name] = {}
            for horizon in prereg["return_horizons"]:
                daily = ic_series(joined, f"_sleeve_{sleeve_name}", f"forward_return_{horizon}d", state_column)
                low = daily.filter(pl.col(state_column) == "low")["ic"].to_numpy()
                high = daily.filter(pl.col(state_column) == "high")["ic"].to_numpy()
                recent = daily.filter(pl.col("date") >= date.fromisoformat(prereg["windows"]["recent_start"]))
                recent_low = recent.filter(pl.col(state_column) == "low")["ic"].to_numpy()
                recent_high = recent.filter(pl.col(state_column) == "high")["ic"].to_numpy()
                difference = float(high.mean() - low.mean()) if high.size and low.size else None
                recent_difference = float(recent_high.mean() - recent_low.mean()) if recent_high.size and recent_low.size else None
                bootstrap = block_bootstrap(daily["ic"].to_numpy() * 0 - 1, block=int(prereg["bootstrap"]["block_months"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"])) if False else None
                # Bootstrap the monthly high-minus-low state IC directly.
                monthly = daily.with_columns(pl.col("date").dt.truncate("1mo").alias("month")).group_by(["month", state_column]).agg(pl.col("ic").mean().alias("ic")).sort(["month", state_column])
                # Bootstrap the paired monthly difference series; a daily-state
                # concatenation would discard month alignment and is not valid.
                monthly_wide = monthly.pivot(index="month", on=state_column, values="ic").sort("month")
                if {"low", "high"}.issubset(monthly_wide.columns):
                    paired = monthly_wide.filter(pl.col("low").is_not_null() & pl.col("high").is_not_null())
                    differences = (paired["high"] - paired["low"]).to_numpy()
                else:
                    differences = np.array([])
                bootstrap = block_bootstrap(differences, block=min(3, max(1, len(differences) // 3)), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"])) if len(differences) >= 4 else None
                rows[conditioner][sleeve_name][str(horizon)] = {
                    "low_ic": round(float(low.mean()), 8) if low.size else None,
                    "high_ic": round(float(high.mean()), 8) if high.size else None,
                    "high_minus_low": round(difference, 8) if difference is not None else None,
                    "recent_high_minus_low": round(recent_difference, 8) if recent_difference is not None else None,
                    "low_days": int(low.size),
                    "high_days": int(high.size),
                    "bootstrap": bootstrap,
                    "raw_p": stats_v2.normal_two_sided_p(float(bootstrap["mean"] / max(1e-12, (bootstrap["ci_high"] - bootstrap["ci_low"]) / 3.92))) if bootstrap else None,
                }
                raw_p.append((conditioner, sleeve_name, horizon, rows[conditioner][sleeve_name][str(horizon)]["raw_p"]))
    qvalues = stats_v2.bh_fdr_qvalues([p for _, _, _, p in raw_p])
    qmap = {(conditioner, sleeve, horizon): q for (conditioner, sleeve, horizon, _), q in zip(raw_p, qvalues, strict=True)}
    passes = []
    for conditioner, by_sleeve in rows.items():
        for sleeve, by_horizon in by_sleeve.items():
            for horizon, item in by_horizon.items():
                q = qmap[(conditioner, sleeve, int(horizon))]
                checks = {
                    "minimum_observations": item["low_days"] >= 80 and item["high_days"] >= 80,
                    "absolute_ic_min": isinstance(item["high_minus_low"], (int, float)) and abs(item["high_minus_low"]) >= float(prereg["gates"]["absolute_high_minus_low_ic_min"]),
                    "bootstrap_ci_excludes_zero": bool(item["bootstrap"]) and (item["bootstrap"]["ci_low"] > 0 or item["bootstrap"]["ci_high"] < 0),
                    "recent_sign_matches_full": bool(isinstance(item["high_minus_low"], (int, float)) and isinstance(item["recent_high_minus_low"], (int, float)) and np.sign(item["high_minus_low"]) == np.sign(item["recent_high_minus_low"])),
                    "bh_q_le": isinstance(q, (int, float)) and q <= 0.10,
                }
                item["q_value"] = round(float(q), 8) if q is not None else None
                item["gate"] = {"pass": all(checks.values()), "checks": checks}
                if item["gate"]["pass"]:
                    passes.append({"conditioner": conditioner, "sleeve": sleeve, "horizon": horizon})
    payload = {"round": "R460", "classification": prereg["classification"], "preregistration_sha256": sha(PREREG), "rows": rows, "pass_rows": passes, "decision": prereg["output_policy"], "known_limits": prereg["known_limits"]}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R460 情绪/宏观 T+1 条件审计", "", "| 条件变量 | 股票袖 | 20日高低差 | 20日q | 60日高低差 | 60日q |", "| --- | --- | ---: | ---: | ---: | ---: |"]
    for conditioner, by_sleeve in rows.items():
        for sleeve, by_h in by_sleeve.items():
            r20, r60 = by_h["20"], by_h["60"]
            lines.append(f"| {conditioner} | {sleeve} | {r20['high_minus_low']} | {r20['q_value']} | {r60['high_minus_low']} | {r60['q_value']} |")
    lines += ["", f"- 通过单元：{len(passes)}", "", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
