#!/usr/bin/env python3
"""R394: cross-source resonance feasibility audit."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PREREG = DATA / "research" / "r394_cross_source_resonance_preregistration.json"
OUT = DATA / "factor_research" / "research" / "r394_cross_source_resonance_2026-11-22.json"
SOURCE = DATA / "ext_data" / "alt_hs300" / "timeseries"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_conditions(payload: dict) -> list[pl.Expr]:
    return [
        pl.col("analyst_rating_change_20d") > 0,
        pl.col("fund_holding_ratio_low") < 0,
        pl.col("margin_chg_1m_low") > 0,
        (pl.col("lhb_inst_net_buy_ratio_20d") > 0)
        | (pl.col("lhb_event_count_20d_low") < 0),
    ]


def main() -> int:
    if OUT.exists() or OUT.with_name(OUT.name + ".sha256").exists():
        raise RuntimeError(f"R394 output exists; refusing overwrite: {OUT.name}")
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    if sha256(PREREG) != sidecar:
        raise RuntimeError("R394 preregistration checksum mismatch")
    cols = [
        "symbol",
        "analyst_rating_change_20d",
        "fund_holding_ratio_low",
        "margin_chg_1m_low",
        "lhb_inst_net_buy_ratio_20d",
        "lhb_event_count_20d_low",
    ]
    frame = (
        pl.scan_parquet(str(SOURCE / "date=*/part.parquet"), include_file_paths="path")
        .select([*cols, "path"])
        .with_columns(
            pl.col("path").str.extract(r"date=(\d{4}-\d{2}-\d{2})").str.to_date().alias("date")
        )
        .collect()
    )
    conditions = load_conditions(payload)
    rows = []
    total = frame.height
    for depth in range(1, 5):
        selected = frame.filter(pl.all_horizontal(conditions[:depth]))
        dates = selected["date"].n_unique()
        symbols = selected["symbol"].n_unique()
        coverage = selected.height / total if total else 0.0
        daily_counts = (
            selected.group_by("date")
            .agg(pl.len().alias("symbol_count"))
            .filter(pl.col("symbol_count") >= payload["minimum_valid_symbols"])
        )
        rows.append({
            "layers": depth,
            "rows": selected.height,
            "dates": dates,
            "symbols": symbols,
            "coverage_ratio": round(coverage, 6),
            "daily_breadth_pass_dates": daily_counts.height,
            "daily_breadth_pass_ratio": round(daily_counts.height / dates, 6)
            if dates
            else 0.0,
            "minimum_daily_breadth": int(daily_counts["symbol_count"].min())
            if daily_counts.height
            else 0,
            "pass": bool(
                dates >= payload["minimum_valid_dates"]
                and symbols >= payload["minimum_valid_symbols"]
                and coverage >= payload["minimum_coverage_ratio"]
                and daily_counts.height >= payload["minimum_valid_dates"]
            ),
        })
    report = {
        "round": "R394",
        "classification": payload["classification"],
        "preregistration_sha256": sha256(PREREG),
        "runner_sha256": sha256(Path(__file__)),
        "source_rows": total,
        "layers": rows,
        "decision": {
            "status": "resonance_feasibility_complete_no_promotion",
            "alpha_evidence": False,
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "known_limits": payload["known_limits"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUT.write_bytes(raw)
    OUT.with_name(OUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUT.name}\n", encoding="ascii")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
