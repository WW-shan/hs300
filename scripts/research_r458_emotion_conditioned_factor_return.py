#!/usr/bin/env python3
"""R458: emotion-conditioned forward-return audit for stock sleeves."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r458_emotion_conditioned_factor_return_preregistration.json"
OUTPUT = E / "r458_emotion_conditioned_factor_return_2026-11-22.json"
RESULT = E / "r458_emotion_conditioned_factor_return_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r458_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def monthly_emotion_states(frame: pl.DataFrame, column: str, min_history: int) -> pl.DataFrame:
    monthly = (
        frame.group_by("date")
        .agg(pl.col(column).drop_nulls().first().alias("value"))
        .sort("date")
        .with_columns(pl.col("date").dt.truncate("1mo").alias("month"))
        .group_by("month")
        .agg(pl.col("value").drop_nulls().first().alias("value"))
        .sort("month")
    )
    values = monthly["value"].to_numpy()
    states = []
    for index, value in enumerate(values):
        history = values[max(0, index - min_history) : index]
        if len(history) < min_history or value is None:
            states.append(None)
            continue
        low, high = np.quantile(history, [1 / 3, 2 / 3])
        states.append("low" if value <= low else "high" if value >= high else "mid")
    return monthly.with_columns(pl.Series("state", states, dtype=pl.String))


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R458 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R458 preregistration checksum mismatch")
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
    emotion_cols = [f"ext_alt_hs300_{name}" for name in prereg["emotion_states"]]
    panel = panel.join(
        load_existing_ext_factors(
            DATA,
            {column: column.removeprefix("ext_alt_hs300_") for column in [*emotion_cols, "ext_alt_hs300_idio_vol_40d_low"]},
        ),
        on=["symbol", "date"],
        how="left",
    ).sort(["symbol", "date"])
    for horizon in prereg["return_horizons"]:
        panel = panel.with_columns((pl.col("close").shift(-horizon).over("symbol") / pl.col("close") - 1.0).alias(f"forward_return_{horizon}d"))
    sleeve_columns = {}
    for name, spec in prereg["factor_sleeves"].items():
        composite = None
        coverage = None
        for factor, weight in spec["factors"].items():
            panel = rank_score_frame(panel, factor, spec["directions"][factor], output_column=f"_z_{name}_{factor}")
            score = float(weight) * pl.col(f"_z_{name}_{factor}").fill_null(0.0)
            missing = pl.col(f"_z_{name}_{factor}").is_null().cast(pl.Int8)
            composite = score if composite is None else composite + score
            coverage = missing if coverage is None else coverage + missing
        panel = panel.with_columns((composite).alias(f"_sleeve_{name}"), (coverage == 0).alias(f"_valid_{name}"))
        sleeve_columns[name] = (f"_sleeve_{name}", f"_valid_{name}")
    rows = {}
    raw_p = []
    for emotion in prereg["emotion_states"]:
        full_emotion = f"ext_alt_hs300_{emotion}"
        states = monthly_emotion_states(panel, full_emotion, int(prereg["emotion_partition"]["min_history_months"]))
        state_map = states.select(["month", "state"]).to_dicts()
        state_by_month = {row["month"]: row["state"] for row in state_map}
        panel = panel.with_columns(pl.col("date").dt.truncate("1mo").alias("month")).with_columns(
            pl.col("month").replace_strict(state_by_month, default=None).alias(f"_state_{emotion}")
        )
        rows[emotion] = {}
        for sleeve, (score_col, valid_col) in sleeve_columns.items():
            rows[emotion][sleeve] = {}
            for horizon in prereg["return_horizons"]:
                return_col = f"forward_return_{horizon}d"
                scoped = panel.filter(pl.col(valid_col) & pl.col(f"_state_{emotion}").is_not_null())
                # Daily equal-weight composite return, then average by month and emotion state.
                daily = (
                    scoped.filter(pl.col(score_col).is_not_null() & pl.col(return_col).is_not_null())
                    .group_by(["date", f"_state_{emotion}"])
                    .agg(pl.corr(pl.col(score_col).rank(), pl.col(return_col).rank()).alias("ic"), pl.len().alias("n"))
                    .filter(pl.col("n") >= 30)
                    .drop_nulls("ic")
                    .filter(pl.col("ic").is_finite())
                )
                by_state = {state: daily.filter(pl.col(f"_state_{emotion}") == state)["ic"].to_numpy() for state in ("low", "mid", "high")}
                low, high = by_state["low"], by_state["high"]
                def mean(values: np.ndarray) -> float | None:
                    return round(float(values.mean()), 8) if values.size else None
                current_horizon = int(horizon)
                def nw_p(values: np.ndarray, lag: int = current_horizon) -> float | None:
                    nw = stats_v2.newey_west_t(values, lag=lag) if values.size >= 3 else None
                    return round(float(stats_v2.normal_two_sided_p(nw[0])), 8) if nw is not None else None
                difference = float(high.mean() - low.mean()) if high.size and low.size else None
                difference_p = nw_p(np.concatenate([high, -low])) if high.size and low.size else None
                # Sign interpretation: this is high-state IC minus low-state IC. Stable positive
                # means the signal works more in high emotion; stable negative means it reverses.
                rows[emotion][sleeve][str(horizon)] = {
                    "low_ic": mean(low), "mid_ic": mean(by_state["mid"]), "high_ic": mean(high),
                    "high_minus_low": round(difference, 8) if difference is not None else None,
                    "high_minus_low_p": difference_p,
                    "low_n": int(low.size), "high_n": int(high.size),
                    "high_minus_low_sign_consistent": (difference or 0.0) * (mean(high) or 0.0) >= 0 and (difference or 0.0) * (mean(low) or 0.0) <= 0 if difference is not None else False,
                }
                raw_p.append((emotion, sleeve, horizon, difference_p))
    qvalues = stats_v2.bh_fdr_qvalues([p for _, _, _, p in raw_p])
    qmap = {(emotion, sleeve, horizon): q for (emotion, sleeve, horizon, _), q in zip(raw_p, qvalues, strict=True)}
    for emotion, by_sleeve in rows.items():
        for sleeve, by_horizon in by_sleeve.items():
            for horizon, row in by_horizon.items():
                q = qmap[(emotion, sleeve, int(horizon))]
                row["q_value"] = round(float(q), 8) if q is not None else None
                row["gate"] = {
                    "pass": row["high_minus_low"] is not None
                    and abs(row["high_minus_low"]) > 0
                    and isinstance(row["high_minus_low_p"], (int, float))
                    and row["high_minus_low_p"] <= 0.10
                    and isinstance(q, (int, float))
                    and q <= 0.10
                    and row["high_minus_low_sign_consistent"]
                    and row["low_n"] >= 80
                    and row["high_n"] >= 80
                }
    payload = {"round": "R458", "classification": prereg["classification"], "preregistration_sha256": sha(PREREG), "states": rows, "decision": prereg["output_policy"], "known_limits": prereg["known_limits"]}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R458 情绪条件因子收益", "", "| 情绪指标 | 股票袖 | 20日高-低IC | 20日p | 60日高-低IC | 60日p |", "| --- | --- | ---: | ---: | ---: | ---: |"]
    for emotion, by_sleeve in rows.items():
        for sleeve in by_sleeve:
            r20, r60 = by_sleeve[sleeve]["20"], by_sleeve[sleeve]["60"]
            lines.append(f"| {emotion} | {sleeve} | {r20['high_minus_low']} | {r20['high_minus_low_p']} | {r60['high_minus_low']} | {r60['high_minus_low_p']} |")
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
