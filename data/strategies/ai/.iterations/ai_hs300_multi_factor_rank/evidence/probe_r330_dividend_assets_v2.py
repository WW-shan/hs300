from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
CACHE = DATA / "research" / "r330_dividend_assets" / "dividend_etf_qfq.parquet"
PREREG = EVIDENCE / "r330_dividend_asset_probe_preregistration.json"
AMENDMENT = EVIDENCE / "r330_preregistration_amendment_A1.json"
INITIAL = EVIDENCE / "r330_dividend_asset_probe.json"
OUTPUT = EVIDENCE / "r330_dividend_asset_probe_v2.json"
START = date(2021, 6, 1)
END = date(2026, 11, 5)
ASSETS = {
    "510880.SH": {"name": "上证红利ETF", "role": "primary"},
    "515080.SH": {"name": "中证红利ETF", "role": "robustness"},
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R330 v2 probe result exists; refusing overwrite")
    for path in (PREREG, AMENDMENT):
        expected = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if sha256(path) != expected:
            raise RuntimeError(f"checksum mismatch: {path.name}")
    if not CACHE.exists() or not INITIAL.exists():
        raise RuntimeError("initial probe cache/result is missing")
    initial = json.loads(INITIAL.read_text(encoding="utf-8"))
    if sha256(CACHE) != initial["cache"]["sha256"]:
        raise RuntimeError("cache changed after the initial probe")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    if prereg["as_of"] != str(END) or amendment["correction"]["liquidity_window"] != "2021-06-01 through 2026-11-05":
        raise RuntimeError("preregistration window mismatch")

    v1 = module(EVIDENCE / "probe_r330_dividend_assets.py", "r330_probe_v1")
    calendar = [day for day in v1.trade_calendar() if START <= day <= END]
    frame = pl.read_parquet(CACHE).sort(["symbol", "date"])
    metadata = {}
    for symbol, item in ASSETS.items():
        asset = frame.filter(pl.col("symbol") == symbol)
        strategy = asset.filter(pl.col("date").is_between(START, END))
        amount = strategy["amount"].cast(pl.Float64)
        adv20 = amount.rolling_mean(20)
        present = set(strategy["date"].to_list())
        missing = [day for day in calendar if day not in present]
        metadata[symbol] = {
            "name": item["name"],
            "role": item["role"],
            "full_rows": asset.height,
            "full_first_date": str(asset["date"].min()),
            "full_last_date": str(asset["date"].max()),
            "strategy_rows": strategy.height,
            "strategy_calendar_days": len(calendar),
            "strategy_missing_days": len(missing),
            "strategy_missing_rate": round(len(missing) / len(calendar), 6) if calendar else 1.0,
            "duplicate_dates": asset.height - asset["date"].n_unique(),
            "null_close": asset["close"].null_count(),
            "nonpositive_close": asset.filter(pl.col("close") <= 0).height,
            "strategy_median_daily_amount_cny": round(float(amount.drop_nulls().median()), 2),
            "strategy_min_20day_average_amount_cny": round(float(adv20.drop_nulls().min()), 2),
        }

    def integrity(meta: dict) -> bool:
        return (
            meta["full_rows"] >= 1000
            and meta["full_last_date"] == str(END)
            and meta["duplicate_dates"] == 0
            and meta["null_close"] == 0
            and meta["nonpositive_close"] == 0
            and meta["strategy_missing_rate"] <= 0.05
        )

    gates = {
        "510880.SH": integrity(metadata["510880.SH"])
        and metadata["510880.SH"]["strategy_median_daily_amount_cny"] >= 100_000_000
        and metadata["510880.SH"]["strategy_min_20day_average_amount_cny"] >= 50_000_000,
        "515080.SH": integrity(metadata["515080.SH"])
        and metadata["515080.SH"]["strategy_median_daily_amount_cny"] >= 50_000_000,
    }
    payload = {
        "round": "R330",
        "amendment": "A1",
        "classification": "corrected liquidity-window data probe; no returns inspected",
        "preregistration_sha256": sha256(PREREG),
        "amendment_sha256": sha256(AMENDMENT),
        "initial_attempt_sha256": sha256(INITIAL),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "assets": metadata,
        "cache": {
            "path": str(CACHE.relative_to(ROOT)),
            "rows": frame.height,
            "sha256": sha256(CACHE),
            "committed": False,
        },
        "gates": gates,
        "all_pass": all(gates.values()),
        "decision": {
            "status": "ready_for_r331_defensive_bucket_test" if all(gates.values()) else "dividend_component_data_gap",
            "performance_test_run": False,
            "provider_call": False,
            "production_write": False,
        },
        "limits": prereg["known_limitations"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({"gates": gates, "all_pass": payload["all_pass"], "assets": metadata}, ensure_ascii=False, indent=2))
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
