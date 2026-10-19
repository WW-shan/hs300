#!/usr/bin/env python3
"""R171 data-availability probe for 300ETF option skew. No return analysis."""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import pandas as pd
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r171_option_data_probe.json"
CACHE = DATA / "research" / "r171_option_skew"
R167_SCRIPT = ROOT / "scripts" / "backtest_r167_target_price_scan.py"
START = date(2020, 1, 1)
END = date(2026, 8, 31)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _interp_iv(rows: list[tuple[float, float]], target: float) -> float | None:
    points = sorted((delta, iv) for delta, iv in rows if math.isfinite(delta) and math.isfinite(iv) and iv > 0)
    if len(points) < 3 or target < points[0][0] or target > points[-1][0]:
        return None
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= target <= x1:
            if abs(x1 - x0) < 1e-12:
                return y1
            return y0 + (target - x0) * (y1 - y0) / (x1 - x0)
    return None


def _parse_contract(contract_id: str) -> dict | None:
    if not contract_id.startswith("510300") or len(contract_id) < 16:
        return None
    side = contract_id[6]
    if side not in ("C", "P"):
        return None
    try:
        expiry = contract_id[7:11]
        strike = int(contract_id[-5:]) / 1000.0
    except ValueError:
        return None
    return {"side": side, "expiry": expiry, "strike": strike, "marker": contract_id[11]}


def _fetch_day(day: date) -> dict:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    ds = day.strftime("%Y%m%d")
    risk_path = CACHE / f"risk_{ds}.parquet"
    stats_path = CACHE / f"stats_{ds}.parquet"
    if risk_path.exists() and stats_path.exists():
        risk = pd.read_parquet(risk_path)
        stats = pd.read_parquet(stats_path)
        source = "cache"
    else:
        risk = ak.option_risk_indicator_sse(date=ds)
        stats = ak.option_daily_stats_sse(date=ds)
        risk.to_parquet(risk_path, index=False)
        stats.to_parquet(stats_path, index=False)
        source = "akshare"
    risk = risk[risk["CONTRACT_ID"].astype(str).str.startswith("510300")].copy()
    stats = stats[stats["合约标的代码"].astype(str) == "510300"].copy()
    parsed = []
    for row in risk.itertuples(index=False):
        meta = _parse_contract(str(row.CONTRACT_ID))
        if meta is None:
            continue
        parsed.append({
            **meta,
            "delta": float(row.DELTA_VALUE),
            "iv": float(row.IMPLC_VOLATLTY),
        })
    frame = pl.DataFrame(parsed) if parsed else pl.DataFrame(schema={
        "side": pl.Utf8, "expiry": pl.Utf8, "strike": pl.Float64,
        "marker": pl.Utf8, "delta": pl.Float64, "iv": pl.Float64,
    })
    current_ym = day.strftime("%y%m")
    expiries = sorted(x for x in frame["expiry"].unique().to_list() if x > current_ym) if frame.height else []
    selected = expiries[0] if expiries else None
    skew = None
    call_25 = None
    put_25 = None
    if selected is not None:
        part = frame.filter(pl.col("expiry") == selected)
        # Prefer standard M contracts over adjusted A contracts for duplicate strikes.
        part = part.sort(["side", "strike", "marker"], descending=[False, False, True]).unique(subset=["side", "strike"], keep="first")
        call_rows = [(float(r["delta"]), float(r["iv"])) for r in part.filter(pl.col("side") == "C").to_dicts()]
        put_rows = [(float(r["delta"]), float(r["iv"])) for r in part.filter(pl.col("side") == "P").to_dicts()]
        call_25 = _interp_iv(call_rows, 0.25)
        put_25 = _interp_iv(put_rows, -0.25)
        if call_25 is not None and put_25 is not None:
            skew = put_25 - call_25
    pcr_volume = None
    pcr_oi = None
    if len(stats):
        row = stats.iloc[0]
        try:
            pcr_volume = float(row.iloc[6]) / float(row.iloc[5]) if float(row.iloc[5]) else None
            pcr_oi = float(row.iloc[10]) / float(row.iloc[9]) if float(row.iloc[9]) else None
        except (IndexError, TypeError, ValueError, ZeroDivisionError):
            pass
    return {
        "date": str(day),
        "source": source,
        "contracts_510300": len(risk),
        "expiries": expiries,
        "selected_expiry": selected,
        "skew_25d": skew,
        "call_iv_25": call_25,
        "put_iv_25": put_25,
        "pcr_volume": pcr_volume,
        "pcr_oi": pcr_oi,
    }


def main() -> int:
    r167 = _load(R167_SCRIPT, "r167")
    calendar = r167._calendar()
    month_ends = [day for day in r167._month_ends(calendar) if START <= day <= END]
    rows = []
    errors = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_fetch_day, day): day for day in month_ends}
        for index, future in enumerate(as_completed(futures), start=1):
            day = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:
                errors.append({"date": str(day), "error": f"{type(exc).__name__}: {exc}"})
            if index % 12 == 0 or index == len(futures):
                print(f"option probe {index}/{len(futures)}", flush=True)
    rows.sort(key=lambda x: x["date"])
    valid_skew = [row for row in rows if row["skew_25d"] is not None]
    valid_pcr = [row for row in rows if row["pcr_volume"] is not None and row["pcr_oi"] is not None]
    result = {
        "purpose": "R171 300ETF option skew data-availability probe; no forward-return analysis",
        "window": [str(START), str(END)],
        "requested_months": len(month_ends),
        "successful_months": len(rows),
        "errors": errors,
        "coverage": {
            "skew_months": len(valid_skew),
            "skew_start": valid_skew[0]["date"] if valid_skew else None,
            "skew_end": valid_skew[-1]["date"] if valid_skew else None,
            "pcr_months": len(valid_pcr),
            "pcr_start": valid_pcr[0]["date"] if valid_pcr else None,
            "pcr_end": valid_pcr[-1]["date"] if valid_pcr else None,
        },
        "audit": {
            "min_contracts": min((row["contracts_510300"] for row in rows), default=None),
            "max_contracts": max((row["contracts_510300"] for row in rows), default=None),
            "months_with_multiple_expiries": sum(len(row["expiries"]) >= 2 for row in rows),
            "months_missing_skew": [row["date"] for row in rows if row["skew_25d"] is None],
        },
        "records": rows,
        "cache": str(CACHE.relative_to(ROOT)),
        "limitations": [
            "Only month-end official SSE risk-indicator snapshots are probed.",
            "Adjusted and standard contracts are deduplicated by side and strike, preferring M contracts.",
            "No forward returns or strategy performance are included in this probe.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "coverage": result["coverage"], "audit": result["audit"], "errors": errors}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())