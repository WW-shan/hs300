from __future__ import annotations

# ruff: noqa: E402
import hashlib
import json
import shutil
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import akshare as ak
import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
CACHE = DATA / "research" / "r331_defensive_assets"
PREREG = EVIDENCE / "r331_three_asset_defensive_bucket_preregistration.json"
OUTPUT = EVIDENCE / "r331_asset_manifest.json"
GOLD_SOURCE = DATA / "research" / "r325_gold_assets" / "gold_etf_qfq.parquet"
DIVIDEND_SOURCE = DATA / "research" / "r330_dividend_assets" / "dividend_etf_qfq.parquet"
TREASURY_OUTPUT = CACHE / "treasury_etf_qfq.parquet"
START = date(2021, 6, 1)
END = date(2026, 11, 5)
TREASURY = {
    "511010.SH": {"code": "511010", "name": "国泰上证5年期国债ETF", "role": "robustness"},
    "511260.SH": {"code": "511260", "name": "上证10年期国债ETF", "role": "primary"},
}
PRIMARY = {"518880.SH", "511260.SH", "510880.SH"}

sys.path.insert(0, str(ROOT / "backend"))
from app.services.fs_utils import atomic_write_parquet


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trading_calendar() -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError("equity calendar is unavailable")
    return (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .select(pl.col("date").cast(pl.Date))
        .unique()
        .collect()
        .filter(pl.col("date").is_between(START, END))
        .sort("date")
    )


def fetch_treasury(symbol: str, code: str, fetched_at: str) -> pl.DataFrame:
    raw = ak.fund_etf_hist_em(
        symbol=code,
        period="daily",
        start_date="20170101",
        end_date=END.strftime("%Y%m%d"),
        adjust="qfq",
    )
    return (
        pl.from_pandas(raw)
        .rename(
            {
                "日期": "date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
            }
        )
        .select(["date", "open", "high", "low", "close", "volume", "amount"])
        .with_columns(
            pl.col("date").cast(pl.String).str.slice(0, 10).str.to_date(),
            *[
                pl.col(name).cast(pl.Float64)
                for name in ("open", "high", "low", "close", "volume", "amount")
            ],
            pl.lit(symbol).alias("symbol"),
            pl.lit("akshare.fund_etf_hist_em").alias("source"),
            pl.lit("qfq").alias("adjustment"),
            pl.lit(fetched_at).alias("fetched_at_utc"),
        )
        .select(
            [
                "symbol",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "source",
                "adjustment",
                "fetched_at_utc",
            ]
        )
        .sort("date")
    )


def describe(frame: pl.DataFrame, calendar_dates: set[date]) -> dict:
    strategy = frame.filter(pl.col("date").is_between(START, END)).sort("date")
    present = set(strategy["date"].to_list())
    amount = strategy["amount"].cast(pl.Float64)
    adv20 = amount.rolling_mean(20)
    return {
        "rows": frame.height,
        "first_date": str(frame["date"].min()),
        "last_date": str(frame["date"].max()),
        "strategy_rows": strategy.height,
        "calendar_coverage": round(len(present & calendar_dates) / len(calendar_dates), 6),
        "duplicate_dates": frame.height - frame["date"].n_unique(),
        "null_close": frame["close"].null_count(),
        "nonpositive_close": frame.filter(pl.col("close") <= 0).height,
        "null_amount": frame["amount"].null_count(),
        "median_daily_amount_cny": round(float(amount.drop_nulls().median()), 2),
        "min_20day_average_amount_cny": round(float(adv20.drop_nulls().min()), 2),
    }


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R331 asset manifest exists; refusing overwrite")
    expected = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    if sha256(PREREG) != expected:
        raise RuntimeError("R331 preregistration checksum mismatch")
    if not GOLD_SOURCE.exists() or not DIVIDEND_SOURCE.exists():
        raise RuntimeError("gold or dividend source cache is missing")
    if TREASURY_OUTPUT.exists():
        raise RuntimeError("R331 treasury cache already exists; refusing overwrite")
    CACHE.mkdir(parents=True, exist_ok=True)

    gold_target = CACHE / "gold_etf_qfq.parquet"
    dividend_target = CACHE / "dividend_etf_qfq.parquet"
    shutil.copy2(GOLD_SOURCE, gold_target)
    shutil.copy2(DIVIDEND_SOURCE, dividend_target)

    fetched_at = datetime.now(UTC).isoformat()
    treasury = pl.concat(
        [fetch_treasury(symbol, item["code"], fetched_at) for symbol, item in TREASURY.items()]
    ).sort(["symbol", "date"])
    atomic_write_parquet(treasury, TREASURY_OUTPUT)

    calendar = trading_calendar()
    calendar_dates = set(calendar["date"].to_list())
    sources = {
        "gold": (gold_target, {"518880.SH": "primary", "159937.SZ": "robustness"}),
        "treasury": (TREASURY_OUTPUT, {symbol: item["role"] for symbol, item in TREASURY.items()}),
        "dividend": (dividend_target, {"510880.SH": "primary", "515080.SH": "robustness"}),
    }
    assets = {}
    for kind, (path, roles) in sources.items():
        frame = pl.read_parquet(path)
        for symbol, role in roles.items():
            detail = describe(frame.filter(pl.col("symbol") == symbol), calendar_dates)
            detail.update({"kind": kind, "symbol": symbol, "role": role})
            assets[symbol] = detail
            print(
                f"{symbol}: coverage={detail['calendar_coverage']:.4f} "
                f"median_amount={detail['median_daily_amount_cny']:.0f}",
                flush=True,
            )

    def integrity(item: dict) -> bool:
        return (
            item["calendar_coverage"] >= 0.95
            and item["duplicate_dates"] == 0
            and item["null_close"] == 0
            and item["nonpositive_close"] == 0
        )

    d1 = all(integrity(item) for item in assets.values())
    d3 = all(
        integrity(assets[symbol])
        and assets[symbol]["median_daily_amount_cny"] >= 100_000_000
        and assets[symbol]["min_20day_average_amount_cny"] >= 50_000_000
        for symbol in PRIMARY
    )
    payload = {
        "round": "R331",
        "classification": "asset preparation and integrity manifest only; no returns inspected",
        "preregistration_sha256": sha256(PREREG),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "period": {"start": str(START), "end": str(END)},
        "calendar_days": len(calendar_dates),
        "assets": assets,
        "caches": {
            "gold": {"path": str(gold_target.relative_to(ROOT)), "sha256": sha256(gold_target), "rows": pl.read_parquet(gold_target).height},
            "treasury": {"path": str(TREASURY_OUTPUT.relative_to(ROOT)), "sha256": sha256(TREASURY_OUTPUT), "rows": treasury.height},
            "dividend": {"path": str(dividend_target.relative_to(ROOT)), "sha256": sha256(dividend_target), "rows": pl.read_parquet(dividend_target).height},
        },
        "gates": {"D1_asset_integrity": d1, "D3_primary_liquidity": d3},
        "all_pass": d1 and d3,
        "decision": {
            "status": "ready_for_r331_performance" if d1 and d3 else "asset_data_or_liquidity_gap",
            "performance_test_run": False,
            "production_write": False,
        },
        "fetched_at_utc": fetched_at,
        "limits": [
            "Treasury series were fetched from AkShare/Eastmoney; gold and dividend series were copied from prior R329/R330 caches.",
            "ETF qfq data are vendor-reconstructed and not an official PIT archive.",
            "No return, IC, or performance metric is computed in this preparation step."
        ],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({"gates": payload["gates"], "all_pass": payload["all_pass"]}, ensure_ascii=False, indent=2))
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
