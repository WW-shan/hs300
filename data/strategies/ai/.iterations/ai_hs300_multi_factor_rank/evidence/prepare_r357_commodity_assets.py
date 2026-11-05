from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import akshare as ak
import polars as pl

# ruff: noqa: E402

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
DATA = ROOT / "data"
EVIDENCE = Path(__file__).resolve().parent
PREREG = EVIDENCE / "r357_commodity_trend_preregistration.json"
OUTPUT = EVIDENCE / "r357_commodity_asset_manifest.json"
CACHE = DATA / "research" / "r357_commodity_assets"
CACHE_FILE = CACHE / "commodity_etfs_qfq.parquet"
GOLD_CACHE = DATA / "research" / "r331_defensive_assets" / "gold_etf_qfq.parquet"
START = date(2022, 5, 5)
END = date(2026, 11, 5)
PRIMARY = {
    "159980.SZ": {"code": "159980", "name": "有色ETF", "role": "metals"},
    "159985.SZ": {"code": "159985", "name": "豆粕ETF", "role": "agriculture"},
    "159981.SZ": {"code": "159981", "name": "能源化工ETF", "role": "energy_chemicals"},
}

sys.path.insert(0, str(ROOT / "backend"))

from app.services.fs_utils import atomic_write_parquet


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(root: Path) -> dict:
    digest = hashlib.sha256()
    paths = sorted(root.rglob("part.parquet"))
    total = 0
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(sha256(path).encode())
        digest.update(b"\n")
        total += path.stat().st_size
    return {"files": len(paths), "bytes": total, "sha256": digest.hexdigest()}


def fetch(symbol: str, code: str, fetched_at: str) -> pl.DataFrame:
    raw = ak.fund_etf_hist_em(
        symbol=code,
        period="daily",
        start_date="20190101",
        end_date=END.strftime("%Y%m%d"),
        adjust="qfq",
    )
    return (
        pl.from_pandas(raw)
        .rename({
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
        })
        .select(["date", "open", "high", "low", "close", "volume", "amount"])
        .with_columns(
            pl.col("date").cast(pl.String).str.slice(0, 10).str.to_date(),
            *[pl.col(name).cast(pl.Float64) for name in ("open", "high", "low", "close", "volume", "amount")],
            pl.lit(symbol).alias("symbol"),
            pl.lit("akshare.fund_etf_hist_em").alias("source"),
            pl.lit("qfq").alias("adjustment"),
            pl.lit(fetched_at).alias("fetched_at_utc"),
        )
        .select(["symbol", "date", "open", "high", "low", "close", "volume", "amount", "source", "adjustment", "fetched_at_utc"])
        .sort("date")
    )


def calendar_dates() -> list[date]:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError("equity calendar unavailable")
    return (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .select(pl.col("date").cast(pl.Date))
        .unique()
        .collect()
        .filter(pl.col("date").is_between(START, END))
        .sort("date")["date"]
        .to_list()
    )


def basket_frame(frame: pl.DataFrame) -> pl.DataFrame:
    common = (
        frame.filter(pl.col("date").is_between(START, END))
        .group_by("date")
        .agg(pl.col("symbol").n_unique().alias("symbols"))
        .filter(pl.col("symbols") == len(PRIMARY))
        .select("date")
    )
    prices = frame.join(common, on="date", how="inner").select("date", "symbol", "close")
    first = common["date"].min()
    first_prices = {
        row["symbol"]: float(row["close"])
        for row in prices.filter(pl.col("date") == first).iter_rows(named=True)
    }
    basket = (
        prices.with_columns((pl.col("close") / pl.col("symbol").replace_strict(first_prices)).alias("normalized"))
        .group_by("date")
        .agg(pl.col("normalized").mean().alias("close"))
        .sort("date")
    )
    return basket.with_columns(
        (pl.col("close") > pl.col("close").rolling_mean(200)).alias("trend_on")
    )


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R357 asset manifest exists; refusing overwrite")
    if CACHE_FILE.exists():
        raise RuntimeError("R357 commodity cache exists; refusing overwrite")
    if sha256(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R357 preregistration checksum mismatch")
    if not GOLD_CACHE.exists():
        raise RuntimeError("R357 gold cache unavailable")

    before_prod = tree_hash(DATA / "kline_daily_enriched")
    fetched_at = datetime.now(UTC).isoformat()
    CACHE.mkdir(parents=True, exist_ok=True)
    frame = pl.concat([
        fetch(symbol, item["code"], fetched_at)
        for symbol, item in PRIMARY.items()
    ]).sort(["symbol", "date"])
    atomic_write_parquet(frame, CACHE_FILE)

    calendar = calendar_dates()
    calendar_set = set(calendar)
    assets = {}
    for symbol, meta in PRIMARY.items():
        one = frame.filter(pl.col("symbol") == symbol).sort("date")
        in_window = one.filter(pl.col("date").is_between(START, END))
        present = set(in_window["date"].to_list())
        amount = in_window["amount"]
        adv20 = amount.rolling_mean(20)
        assets[symbol] = {
            **meta,
            "rows": one.height,
            "strategy_rows": in_window.height,
            "first_date": str(one["date"].min()),
            "last_date": str(one["date"].max()),
            "calendar_coverage": round(len(present & calendar_set) / len(calendar_set), 6),
            "duplicate_dates": one.height - one["date"].n_unique(),
            "null_close": one["close"].null_count(),
            "nonpositive_close": one.filter(pl.col("close") <= 0).height,
            "median_daily_amount_cny": round(float(amount.median()), 2),
            "min_20day_average_amount_cny": round(float(adv20.drop_nulls().min()), 2),
        }

    basket = basket_frame(frame)
    gold = (
        pl.read_parquet(GOLD_CACHE)
        .filter(pl.col("symbol") == "518880.SH")
        .select("date", "close")
        .sort("date")
        .with_columns((pl.col("close") > pl.col("close").rolling_mean(200)).alias("gold_on"))
    )
    joined = basket.join(gold, on="date", how="inner").sort("date")
    month_end = (
        joined.with_columns(pl.col("date").dt.strftime("%Y-%m").alias("month"))
        .group_by("month")
        .agg(pl.all().last())
        .sort("month")
        .filter(pl.col("date").is_between(START, END))
    )
    state_counts = {
        "gold_on_commodity_on": month_end.filter(pl.col("gold_on") & pl.col("trend_on")).height,
        "gold_on_commodity_off": month_end.filter(pl.col("gold_on") & ~pl.col("trend_on")).height,
        "gold_off_commodity_on": month_end.filter(~pl.col("gold_on") & pl.col("trend_on")).height,
        "both_off": month_end.filter(~pl.col("gold_on") & ~pl.col("trend_on")).height,
    }
    state_dates = {
        "gold_off_commodity_on": [
            str(row["date"]) for row in month_end.filter(~pl.col("gold_on") & pl.col("trend_on")).iter_rows(named=True)
        ]
    }
    checks = {
        "D1_each_asset_coverage_at_least_0.95": all(item["calendar_coverage"] >= 0.95 for item in assets.values()),
        "D2_common_calendar_days_at_least_1000": len(calendar) >= 1000 and basket.height >= 1000,
        "D3_no_duplicate_dates_or_null_nonpositive_close": all(
            item["duplicate_dates"] == 0 and item["null_close"] == 0 and item["nonpositive_close"] == 0
            for item in assets.values()
        ),
        "D4_primary_commodity_median_daily_amount_at_least_10m_cny": all(
            item["median_daily_amount_cny"] >= 10_000_000 for item in assets.values()
        ),
        "D5_primary_commodity_min_20day_average_amount_at_least_5m_cny": all(
            item["min_20day_average_amount_cny"] >= 5_000_000 for item in assets.values()
        ),
        "D6_causal_month_end_signal_and_at_least_one_commodity_only_month": state_counts["gold_off_commodity_on"] >= 1,
    }
    after_prod = tree_hash(DATA / "kline_daily_enriched")
    checks["D7_inputs_byte_stable"] = before_prod == after_prod
    payload = {
        "round": "R357",
        "status": "asset_integrity_preflight_no_returns",
        "preregistration_sha256": sha256(PREREG),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "period": {"start": str(START), "end": str(END)},
        "calendar_days": len(calendar),
        "assets": assets,
        "common_basket_days": basket.height,
        "month_end_states": state_counts,
        "commodity_only_month_dates": state_dates["gold_off_commodity_on"],
        "cache": {
            "path": str(CACHE_FILE.relative_to(ROOT)),
            "sha256": sha256(CACHE_FILE),
            "rows": frame.height,
        },
        "checks": checks,
        "all_checks": all(checks.values()),
        "no_return_or_backtest_evaluated": True,
        "input_isolation": {"production_before": before_prod, "production_after": after_prod, "stable": before_prod == after_prod},
        "fetched_at_utc": fetched_at,
        "limits": [
            "ETF qfq series are vendor-reconstructed, not official PIT total-return archives.",
            "The commodity basket is an equal-weight buy-and-hold research proxy and does not model futures roll yield directly.",
            "Liquidity checks cover ETF secondary-market turnover only and do not model market impact.",
        ],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n",
        encoding="ascii",
    )
    print(json.dumps({
        "all_checks": payload["all_checks"],
        "common_basket_days": basket.height,
        "month_end_states": state_counts,
        "commodity_only_month_dates": state_dates["gold_off_commodity_on"],
        "checks": checks,
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2, default=str))
    return 0 if payload["all_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
