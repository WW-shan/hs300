from __future__ import annotations

# ruff: noqa: E402
import hashlib
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import akshare as ak
import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
CACHE = DATA / "research" / "r330_dividend_assets"
PREREG = EVIDENCE / "r330_dividend_asset_probe_preregistration.json"
OUTPUT = EVIDENCE / "r330_dividend_asset_probe.json"
ASSETS = {
    "510880.SH": {"code": "510880", "name": "上证红利ETF", "role": "primary"},
    "515080.SH": {"code": "515080", "name": "中证红利ETF", "role": "robustness"},
}
START = "2019-01-01"
END = "2026-11-05"

sys.path.insert(0, str(ROOT / "backend"))
from app.services.fs_utils import atomic_write_parquet


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
    first = frame["date"].min()
    calendar = [day for day in trade_calendar() if first <= day <= frame["date"].max()]
    present = set(frame["date"].to_list())
    missing = [str(day) for day in calendar if day not in present]
    amount = frame["amount"].cast(pl.Float64)
    adv20 = amount.rolling_mean(20)
    metadata = {
        "symbol": symbol,
        "name": ASSETS[symbol]["name"],
        "role": ASSETS[symbol]["role"],
        "rows": frame.height,
        "first_date": str(first),
        "last_date": str(frame["date"].max()),
        "duplicate_dates": frame.height - frame["date"].n_unique(),
        "calendar_days": len(calendar),
        "missing_calendar_days": len(missing),
        "missing_calendar_rate": round(len(missing) / len(calendar), 6) if calendar else 1.0,
        "missing_calendar_sample": missing[:20],
        "null_close": frame["close"].null_count(),
        "nonpositive_close": frame.filter(pl.col("close") <= 0).height,
        "null_amount": frame["amount"].null_count(),
        "median_daily_amount_cny": round(float(amount.drop_nulls().median()), 2),
        "min_20day_average_amount_cny": round(float(adv20.drop_nulls().min()), 2),
    }
    return frame, metadata


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R330 probe result exists; refusing overwrite")
    if not PREREG.exists():
        raise RuntimeError("R330 preregistration is missing")
    if sha256(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R330 preregistration checksum mismatch")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if prereg.get("as_of") != END:
        raise RuntimeError("R330 preregistration as_of mismatch")

    CACHE.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(UTC).isoformat()
    frames = []
    metadata = {}
    for symbol, item in ASSETS.items():
        frame, details = fetch_asset(symbol, item["code"], fetched_at)
        frames.append(frame)
        metadata[symbol] = details
        print(
            f"{symbol}: rows={details['rows']} missing={details['missing_calendar_days']} "
            f"median_amount={details['median_daily_amount_cny']:.0f}",
            flush=True,
        )

    combined = pl.concat(frames).sort(["symbol", "date"])
    cache_path = CACHE / "dividend_etf_qfq.parquet"
    atomic_write_parquet(combined, cache_path)

    common = lambda item: (  # noqa: E731
        item["rows"] >= 1000
        and item["last_date"] == END
        and item["duplicate_dates"] == 0
        and item["null_close"] == 0
        and item["nonpositive_close"] == 0
        and item["missing_calendar_rate"] <= 0.05
    )
    gates = {
        "510880.SH": common(metadata["510880.SH"])
        and metadata["510880.SH"]["median_daily_amount_cny"] >= 100_000_000
        and metadata["510880.SH"]["min_20day_average_amount_cny"] >= 50_000_000,
        "515080.SH": common(metadata["515080.SH"])
        and metadata["515080.SH"]["median_daily_amount_cny"] >= 50_000_000,
    }
    payload = {
        "round": "R330",
        "classification": "data-availability and integrity probe only; no returns inspected",
        "preregistration_sha256": sha256(PREREG),
        "runner_sha256": sha256(Path(__file__).resolve()),
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
        "gates": gates,
        "all_pass": all(gates.values()),
        "decision": {
            "status": "ready_for_r331_defensive_bucket_test" if all(gates.values()) else "dividend_component_data_gap",
            "performance_test_run": False,
            "provider_write": False,
        },
        "limits": prereg["known_limitations"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({"gates": gates, "all_pass": payload["all_pass"], "cache": payload["cache"]}, ensure_ascii=False, indent=2))
    return 0 if payload["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
