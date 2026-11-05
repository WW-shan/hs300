from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
DATA = ROOT / "data"
EVIDENCE = Path(__file__).resolve().parent
ASSET_CACHE = DATA / "research/r331_defensive_assets"
OUTPUT = EVIDENCE / "r349_defensive_dual_momentum_preflight.json"
START = date(2022, 5, 5)
END = date(2026, 11, 5)
LOOKBACK = 60
PRIMARY = {"gold": "518880.SH", "treasury": "511260.SH", "dividend": "510880.SH"}
ROBUSTNESS = {
    "R1_dividend": {"gold": "518880.SH", "treasury": "511260.SH", "dividend": "515080.SH"},
    "R2_gold": {"gold": "159937.SZ", "treasury": "511260.SH", "dividend": "510880.SH"},
    "R3_treasury": {"gold": "518880.SH", "treasury": "511010.SH", "dividend": "510880.SH"},
}
ASSET_FILES = {
    "gold": "gold_etf_qfq.parquet",
    "treasury": "treasury_etf_qfq.parquet",
    "dividend": "dividend_etf_qfq.parquet",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def asset_frame(kind: str, symbol: str) -> pl.DataFrame:
    frame = pl.read_parquet(ASSET_CACHE / ASSET_FILES[kind])
    return frame.filter(pl.col("symbol") == symbol).select(["date", "close"]).sort("date")


def monthly_selections(gold: pl.DataFrame, treasury: pl.DataFrame, dividend: pl.DataFrame) -> tuple[list[dict], dict]:
    joined = (
        gold.rename({"close": "gold"})
        .join(treasury.rename({"close": "treasury"}), on="date", how="inner")
        .join(dividend.rename({"close": "dividend"}), on="date", how="inner")
        .sort("date")
    )
    dates = joined["date"].to_list()
    values = {name: joined[name].to_list() for name in ("gold", "treasury", "dividend")}
    names = list(PRIMARY)
    current = {name: 1.0 / 3.0 for name in names}
    records = []
    total_turnover = 0.0
    for index, day in enumerate(dates):
        if index == 0:
            continue
        if (day.year, day.month) != (dates[index - 1].year, dates[index - 1].month):
            if index < LOOKBACK:
                continue
            momentum = {
                name: values[name][index] / values[name][index - LOOKBACK] - 1.0
                for name in names
            }
            ranked = sorted(names, key=lambda name: (-momentum[name], names.index(name)))
            selected = ranked[:2]
            target = {name: 0.5 if name in selected else 0.0 for name in names}
            turnover = 0.5 * sum(abs(target[name] - current[name]) for name in names)
            total_turnover += turnover
            records.append({
                "date": str(day),
                "momentum": {name: round(momentum[name], 10) for name in names},
                "selected": selected,
                "weights": target,
                "turnover": round(turnover, 10),
            })
            current = target
    window = [row for row in records if START.isoformat() <= row["date"] <= END.isoformat()]
    counts = {name: sum(name in row["selected"] for row in window) for name in names}
    pairs = {}
    for row in window:
        key = "+".join(row["selected"])
        pairs[key] = pairs.get(key, 0) + 1
    summary = {
        "schedule_rows_in_window": len(window),
        "selection_counts": counts,
        "selection_frequency": {
            name: counts[name] / len(window) if window else 0.0 for name in names
        },
        "selection_pairs": dict(sorted(pairs.items())),
        "mean_schedule_turnover": sum(row["turnover"] for row in window) / len(window) if window else None,
        "max_schedule_turnover": max((row["turnover"] for row in window), default=None),
    }
    return window, summary


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R349 preflight exists; refusing overwrite")
    paths = {kind: ASSET_CACHE / filename for kind, filename in ASSET_FILES.items()}
    before = {kind: sha256(path) for kind, path in paths.items()}
    coverage = {}
    all_sets = {"primary": PRIMARY, **ROBUSTNESS}
    for variant, symbols in all_sets.items():
        coverage[variant] = {}
        for kind in ("gold", "treasury", "dividend"):
            symbol = symbols[kind]
            frame = asset_frame(kind, symbol)
            before_start = frame.filter(pl.col("date") < START)
            window = frame.filter(pl.col("date").is_between(START, END))
            coverage[variant][kind] = {
                "symbol": symbol,
                "rows": frame.height,
                "first_date": str(frame["date"].min()),
                "last_date": str(frame["date"].max()),
                "warmup_rows_before_start": before_start.height,
                "window_rows": window.height,
                "has_60d_warmup": before_start.height >= LOOKBACK,
                "covers_window": not window.is_empty() and window["date"].min() <= START and window["date"].max() >= END,
            }
    primary_frames = {kind: asset_frame(kind, PRIMARY[kind]) for kind in ("gold", "treasury", "dividend")}
    schedule, summary = monthly_selections(primary_frames["gold"], primary_frames["treasury"], primary_frames["dividend"])
    after = {kind: sha256(path) for kind, path in paths.items()}
    checks = {
        "D1_primary_asset_coverage": all(item["covers_window"] and item["has_60d_warmup"] for item in coverage["primary"].values()),
        "D2_robustness_asset_coverage": all(item["covers_window"] and item["has_60d_warmup"] for variant in ROBUSTNESS.values() for item in coverage[next(name for name, value in all_sets.items() if value is variant)].values()),
        "D3_monthly_schedule_complete": summary["schedule_rows_in_window"] >= 50,
        "D4_all_assets_selected": all(value >= 0.20 for value in summary["selection_frequency"].values()),
        "D5_max_turnover_bounded": summary["max_schedule_turnover"] is not None and summary["max_schedule_turnover"] <= 0.5000000001,
        "D6_no_leverage": all(sum(row["weights"].values()) <= 1.0 + 1e-12 for row in schedule),
        "D7_inputs_byte_stable": before == after,
    }
    payload = {
        "round": "R349",
        "status": "coverage_only_preflight_no_returns",
        "rule": {
            "universe": PRIMARY,
            "signal": "at each first trading day of month, rank the three ETFs by total return over the trailing 60 trading days through that close",
            "selection": "equal-weight the top two; signal applies only to subsequent returns",
            "lookback": LOOKBACK,
            "no_parameter_scan": True,
        },
        "window": [str(START), str(END)],
        "coverage": coverage,
        "selection_summary": summary,
        "schedule_sample": schedule[:3] + schedule[-3:],
        "checks": checks,
        "all_checks": all(checks.values()),
        "input_isolation": {"before": before, "after": after, "stable": before == after},
        "no_return_or_backtest_evaluated": True,
        "next": "If all checks pass, freeze R349 portfolio gates before running any portfolio return or drawdown backtest.",
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({
        "status": payload["status"],
        "checks": checks,
        "all_checks": payload["all_checks"],
        "selection_summary": summary,
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0 if payload["all_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
