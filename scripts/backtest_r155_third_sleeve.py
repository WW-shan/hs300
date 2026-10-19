#!/usr/bin/env python3
"""R155: gate-closed third sleeve (C) and three-sleeve portfolio."""
from __future__ import annotations

import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r155_third_sleeve.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R127_SCRIPT = ROOT / "scripts" / "backtest_r127_vol_target_grid.py"
R155_CACHE = DATA / "research" / "r155_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
C_SLEEVE = "ai_hs300_gate_closed_leg"
WINDOW = 252


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _gate_frame() -> pl.DataFrame:
    lazy = pl.scan_parquet(
        str(DATA / "ext_data" / "alt_hs300" / "timeseries" / "*" / "part.parquet"),
        hive_partitioning=True,
    )
    return (
        lazy.select(["date", "equity_risk_premium", "credit_spread_5y_low", "term_spread_10y_2y"])
        .collect()
        .unique(subset=["date"])
        .sort("date")
        .with_columns(
            (
                (pl.col("equity_risk_premium") >= 5.5)
                & (pl.col("credit_spread_5y_low") >= -0.8)
                & (pl.col("term_spread_10y_2y") >= 0.3)
            ).alias("gate_open")
        )
        .select(["date", "gate_open"])
    )


def _three_sleeve(a: pl.DataFrame, b: pl.DataFrame, c: pl.DataFrame, gate: pl.DataFrame) -> pl.DataFrame:
    """Gate-open: risk parity of A/B.  Gate-closed: risk parity of B/C."""
    joined = (
        a.rename({"value": "a"})
        .join(b.rename({"value": "b"}), on="date", how="inner")
        .join(c.rename({"value": "c"}), on="date", how="inner")
        .join(gate, on="date", how="left")
        .sort("date")
    )
    dates = joined["date"].to_list()
    va = joined["a"].to_list()
    vb = joined["b"].to_list()
    vc = joined["c"].to_list()
    opens = joined["gate_open"].to_list()
    ra = [va[i] / va[i - 1] - 1.0 for i in range(1, len(va))]
    rb = [vb[i] / vb[i - 1] - 1.0 for i in range(1, len(vb))]
    rc = [vc[i] / vc[i - 1] - 1.0 for i in range(1, len(vc))]

    # monthly targets: gate-open -> (wA, wB, 0); gate-closed -> (0, wB2, wC2)
    targets = {}
    prev_month = None
    for i, day in enumerate(dates):
        month = (day.year, day.month)
        if i == 0 or month != prev_month:
            state = bool(opens[i - 1]) if i > 0 and opens[i - 1] is not None else True
            if i < 60:
                targets[day] = (0.5, 0.5, 0.0) if state else (0.0, 0.5, 0.5)
            else:
                wa = [0.0, *ra][i - 60 : i]
                wb = [0.0, *rb][i - 60 : i]
                wc = [0.0, *rc][i - 60 : i]
                sa = max(st.pstdev(wa), 1e-6)
                sb = max(st.pstdev(wb), 1e-6)
                sc = max(st.pstdev(wc), 1e-6)
                if state:
                    w_a = (1.0 / sa) / (1.0 / sa + 1.0 / sb)
                    w_a = min(0.8, max(0.2, w_a))
                    targets[day] = (w_a, 1.0 - w_a, 0.0)
                else:
                    w_b = (1.0 / sb) / (1.0 / sb + 1.0 / sc)
                    w_b = min(0.8, max(0.2, w_b))
                    targets[day] = (0.0, w_b, 1.0 - w_b)
            prev_month = month

    portfolio = 1.0
    sleeve = {k: 1.0 for k in "abc"}
    target = targets[dates[0]]
    rows = [{"date": dates[0], "value": portfolio}]
    for i in range(1, len(dates)):
        day = dates[i]
        for k, r in (("a", ra[i - 1]), ("b", rb[i - 1]), ("c", rc[i - 1])):
            sleeve[k] *= 1.0 + r
        portfolio *= 1.0 + sum(t * r for t, r in zip(target, (ra[i - 1], rb[i - 1], rc[i - 1])))
        next_month = i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (day.year, day.month)
        if next_month:
            totals = [target[j] * sleeve[k] for j, k in enumerate("abc")]
            total = sum(totals)
            actual = [t / total if total else target[j] for j, t in enumerate(totals)]
            nt = targets[dates[i + 1]] if i + 1 < len(dates) else target
            turnover = 0.5 * sum(abs(nt[j] - actual[j]) for j in range(3))
            portfolio *= 1.0 - turnover * 0.0052
            sleeve = {k: 1.0 for k in "abc"}
            target = nt
        rows.append({"date": day, "value": portfolio})
    return pl.DataFrame(rows)


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r127 = _load(R127_SCRIPT, "r127")
    r114.TARGET_VOL = 0.15
    R155_CACHE.mkdir(parents=True, exist_ok=True)

    membership = r112._historical_membership()
    service = r112._service()
    gate = _gate_frame()

    c_records = []
    three_records = []
    two_records = []
    c_closed_returns = []
    recent = None
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        # C sleeve
        c_path = R155_CACHE / f"{C_SLEEVE}_{key}_{r112.END.isoformat()}.parquet"
        if c_path.exists():
            c = pl.read_parquet(c_path).with_columns(pl.col("date").cast(pl.Date))
        else:
            cfg = r112.StrategyBacktestConfig(
                strategy_id=C_SLEEVE, symbols=None, start=phase, end=r112.END,
                params={}, overrides={}, matching="open_t+1",
                fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
                max_positions=10, position_sizing="equal", mode="position",
                asset_type="stock", hs300=True,
            )
            with r112._patched_membership(membership):
                result = service.run(cfg)
            if result.error:
                # The gate can stay open for the whole short phase, leaving the
                # C sleeve with no entry signals.  Fall back to a flat curve.
                print(f"C {phase}: no signals ({result.error}); using flat curve", flush=True)
                c = pl.DataFrame({
                    "date": pl.Series([], dtype=pl.Date),
                    "value": pl.Series([], dtype=pl.Float64),
                })
                c.write_parquet(c_path)
            else:
                c = r112._curve_frame(result.equity_curve)
                c.write_parquet(c_path)
                print(f"ran C {phase}: ret={result.stats.get('total_return')}", flush=True)

        if c.height == 0:
            print(f"[{key}] C sleeve empty; skipping three-sleeve", flush=True)
            continue
        a = pl.read_parquet(DATA / "research" / "r125_cache" / f"{A_SLEEVE}_{key}_{r112.END.isoformat()}_3x.parquet").with_columns(pl.col("date").cast(pl.Date))
        b = pl.read_parquet(DATA / "research" / "r119_cache" / f"{B_SLEEVE}_{key}_{r112.END.isoformat()}.parquet").with_columns(pl.col("date").cast(pl.Date))

        # C sleeve alone
        c_records.extend(r127._window_stats(c["value"].to_list()))
        # C in gate-closed periods: measure C return only on closed days
        cj = c.join(gate, on="date", how="left").sort("date")
        closed = cj.filter(pl.col("gate_open") == False)
        if closed.height > 1:
            c_closed_returns.append(float(closed["value"][-1] / closed["value"][0] - 1.0))

        # three-sleeve
        three = _three_sleeve(a, b, c, gate)
        three_overlay = r114._vol_target(three)["curve"]
        three_records.extend(r127._window_stats(three_overlay["value"].to_list()))

        # two-sleeve reference
        risk, _ = r112._combine(a, b, "risk")
        two = r114._vol_target(risk)["curve"]
        two_records.extend(r127._window_stats(two["value"].to_list()))

        if key == "2025-10-14":
            recent = r112._stats(three_overlay)
        print(f"[{key}] C={r112._stats(c)['total_return']:+.3f} three_pos={sum(1 for w in r127._window_stats(three_overlay['value'].to_list()) if w['return']>0)/max(len(r127._window_stats(three_overlay['value'].to_list())),1):.3f}", flush=True)

    c_summary = r127._summarize(c_records)
    three_summary = r127._summarize(three_records)
    two_summary = r127._summarize(two_records)

    r131 = json.loads((EVIDENCE / "r131_pit_dual_sleeve.json").read_text(encoding="utf-8"))
    base = r131["candidate_pit_dual_vol15"]

    d1 = len(c_closed_returns) == 15
    g1 = st.mean(c_closed_returns) > 0
    g2 = three_summary["positive_rate"] >= base["positive_rate"]
    g3 = three_summary["return_worst"] >= base["return_worst"] - 0.01
    g4 = recent is not None and recent["total_return"] > 0
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R155 gate-closed third sleeve",
        "c_sleeve_alone": c_summary,
        "c_closed_period_returns": [round(v, 4) for v in c_closed_returns],
        "c_closed_mean": round(st.mean(c_closed_returns), 4),
        "three_sleeve": three_summary,
        "two_sleeve_reference": two_summary,
        "reference_r131": base,
        "recent_year_three": recent,
        "decision": decision,
        "caveats": ["Gate-closed months are few; the C sleeve trades rarely."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("C alone:", json.dumps(c_summary, ensure_ascii=False), flush=True)
    print("C closed mean:", round(st.mean(c_closed_returns), 4), flush=True)
    print("three:", json.dumps(three_summary, ensure_ascii=False), flush=True)
    print("two:", json.dumps(two_summary, ensure_ascii=False), flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
