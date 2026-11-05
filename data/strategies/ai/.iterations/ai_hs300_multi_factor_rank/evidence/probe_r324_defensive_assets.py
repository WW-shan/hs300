from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import akshare as ak
import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
CACHE = DATA / "research" / "r324_defensive_assets"
OUTPUT = EVIDENCE / "r324_asset_probe.json"
ASSETS = {
    "511010.SH": {"code": "511010", "name": "国泰上证5年期国债ETF"},
    "511260.SH": {"code": "511260", "name": "上证10年期国债ETF"},
}
START = "2017-01-01"
END = "2026-10-14"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trade_calendar() -> list[date]:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError("index calendar is unavailable")
    return (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(pl.col("date").cast(pl.Date))
        .unique()
        .collect()["date"]
        .to_list()
    )


def fetch_asset(symbol: str, code: str, fetched_at: str) -> tuple[pl.DataFrame, dict]:
    raw = ak.fund_etf_hist_em(
        symbol=code,
        period="daily",
        start_date=START.replace("-", ""),
        end_date=END.replace("-", ""),
        adjust="qfq",
    )
    frame = (
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
    calendar = [day for day in trade_calendar() if day >= frame["date"].min() and day <= frame["date"].max()]
    present = set(frame["date"].to_list())
    missing = [str(day) for day in calendar if day not in present]
    nonzero_close = frame.filter(pl.col("close") > 0)
    metadata = {
        "symbol": symbol,
        "name": ASSETS[symbol]["name"],
        "rows": frame.height,
        "first_date": str(frame["date"].min()),
        "last_date": str(frame["date"].max()),
        "duplicate_dates": frame.height - frame["date"].n_unique(),
        "calendar_days": len(calendar),
        "missing_calendar_days": len(missing),
        "missing_calendar_rate": round(len(missing) / len(calendar), 6) if calendar else 1.0,
        "missing_calendar_sample": missing[:20],
        "null_close": frame["close"].null_count(),
        "nonpositive_close": frame.height - nonzero_close.height,
        "null_open": frame["open"].null_count(),
        "null_volume": frame["volume"].null_count(),
        "null_amount": frame["amount"].null_count(),
    }
    return frame, metadata


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R324 asset probe exists; refusing overwrite")
    CACHE.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(UTC).isoformat()
    frames = []
    metadata = {}
    for symbol, item in ASSETS.items():
        frame, details = fetch_asset(symbol, item["code"], fetched_at)
        frames.append(frame)
        metadata[symbol] = details
        print(f"{symbol}: rows={details['rows']} missing={details['missing_calendar_days']}", flush=True)
    combined = pl.concat(frames).sort(["symbol", "date"])
    cache_path = CACHE / "treasury_etf_qfq.parquet"
    if cache_path.exists():
        existing = pl.read_parquet(cache_path)
        if existing.shape != combined.shape or existing.schema != combined.schema:
            raise RuntimeError("existing R324 cache does not match the probe result")
    else:
        combined.write_parquet(cache_path, compression="zstd")
    payload = {
        "round": "R324",
        "classification": "data-availability and integrity probe only; no returns inspected",
        "source": "AkShare fund_etf_hist_em, Eastmoney daily qfq",
        "requested_window": {"start": START, "end": END},
        "fetched_at_utc": fetched_at,
        "assets": metadata,
        "cache": {
            "path": str(cache_path.relative_to(ROOT)),
            "rows": combined.height,
            "sha256": sha256(cache_path),
            "committed": False,
        },
        "decision_rule": "Both assets must have >=95% coverage of the local 000300.SH trading calendar, no duplicate dates, no null close, and no nonpositive close before performance design.",
        "all_pass": all(
            item["missing_calendar_rate"] <= 0.05
            and item["duplicate_dates"] == 0
            and item["null_close"] == 0
            and item["nonpositive_close"] == 0
            for item in metadata.values()
        ),
        "limits": [
            "This probe does not read or report any strategy return.",
            "Adjusted ETF prices are provider-reconstructed and are not an official PIT archive.",
            "The cached Parquet file is local research data and must not be committed.",
        ],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
