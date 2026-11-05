from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import polars as pl

# ruff: noqa: E402

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTPUT = EVIDENCE / "r344_window_preflight.json"
STRATEGY_ID = "ai_hs300_quality_momentum_broad_h60"
START = date(2020, 6, 1)
END = date(2023, 12, 31)
PROPOSED_START = date(2022, 5, 5)
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
ASSET_CACHE = DATA / "research/r331_defensive_assets"
ASSETS = {
    "gold": ("518880.SH", "gold_etf_qfq.parquet"),
    "treasury": ("511260.SH", "treasury_etf_qfq.parquet"),
    "dividend": ("510880.SH", "dividend_etf_qfq.parquet"),
}
REQUIRED_FIELDS = (
    "ma60",
    "momentum_60d",
    "roe_latest",
    "pb_latest",
    "debt_ratio_latest",
    "ocf_ni",
    "accrual",
    "annual_vol_20d",
)

sys.path.insert(0, str(ROOT / "backend"))

from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyDependencyResolver, _hs300_panel_row_mask
from app.factors.store import load_into_registry
from app.services.industry_map import industry_loader_for
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyEngine
from app.strategy.scoring import materialize_scoring_columns
from app.tickflow.repository import DataStore, KlineRepository


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


def membership_frame() -> pl.DataFrame:
    return (
        pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"])
        .select(
            pl.col("query_date").cast(pl.Date).alias("effective_date"),
            pl.col("symbol").cast(pl.String),
        )
        .unique()
        .sort(["effective_date", "symbol"])
    )


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R344 preflight exists; refusing overwrite")

    before = {
        "kline_daily_enriched": tree_hash(DATA / "kline_daily_enriched"),
        "alt_hs300": tree_hash(DATA / "ext_data/alt_hs300/timeseries"),
        "financials": {
            name: sha256(DATA / "financials" / name / "part.parquet")
            for name in ("metrics", "income", "balance_sheet", "cash_flow")
        },
        "membership": sha256(MEMBERSHIP),
        "defensive_assets": {
            key: sha256(ASSET_CACHE / filename)
            for key, (_symbol, filename) in ASSETS.items()
        },
    }

    load_into_registry(DATA)
    engine = StrategyEngine(
        strategy_dirs=[DATA / "strategies/ai"],
        override_loader=lambda sid: strategy_config.load_override(DATA, sid),
        industry_loader=industry_loader_for(DATA),
    )
    strategy = engine.get(STRATEGY_ID)
    plan = StrategyDependencyResolver().resolve(
        strategy,
        params={},
        basic_filter=strategy.basic_filter,
        entry_signals=strategy.entry_signals,
        exit_signals=strategy.exit_signals,
        asset_type="stock",
    )
    repo = KlineRepository(DataStore(DATA))
    panel = BacktestEngine(repo).load_panel_for_backtest(
        None,
        START,
        END,
        plan,
        asset_type="stock",
    )
    if panel.is_empty():
        raise RuntimeError("R344 preflight panel is empty")

    filter_expr = strategy.filter_fn(panel, {}) if strategy.filter_fn else pl.lit(True)
    basic_expr = StrategyEngine._basic_filter_expr(panel, strategy.basic_filter) if strategy.basic_filter else None
    candidate_expr = filter_expr if basic_expr is None else filter_expr & basic_expr
    members = membership_frame()
    candidate_expr = candidate_expr & _hs300_panel_row_mask(panel, members)
    work = panel.with_columns(candidate_expr.fill_null(False).alias("_candidate"))
    work = materialize_scoring_columns(work, strategy.meta["scoring"].keys())

    day_counts = (
        work.group_by("date")
        .agg(pl.col("_candidate").sum().alias("candidates"))
        .sort("date")
    )
    first_any = day_counts.filter(pl.col("candidates") > 0).head(1)
    first_10 = day_counts.filter(pl.col("candidates") >= 10).head(1)
    first_30 = day_counts.filter(pl.col("candidates") >= 30).head(1)
    if first_any.is_empty():
        raise RuntimeError("R344 preflight found no C candidates")
    first_date = first_any.row(0, named=True)["date"]
    first_rows = work.filter(pl.col("date") == first_date)
    first_field_coverage = {
        field: int(first_rows[field].is_not_null().sum())
        for field in REQUIRED_FIELDS
    }

    yearly = (
        day_counts.with_columns(pl.col("date").dt.year().alias("year"))
        .group_by("year")
        .agg(
            pl.len().alias("trading_days"),
            (pl.col("candidates") > 0).sum().alias("days_any"),
            (pl.col("candidates") >= 10).sum().alias("days_ge_10"),
            (pl.col("candidates") >= 30).sum().alias("days_ge_30"),
            pl.col("candidates").median().alias("median_candidates"),
            pl.col("candidates").min().alias("min_candidates"),
            pl.col("candidates").max().alias("max_candidates"),
        )
        .sort("year")
    )

    asset_coverage = {}
    for key, (symbol, filename) in ASSETS.items():
        frame = (
            pl.read_parquet(ASSET_CACHE / filename)
            .filter(pl.col("symbol") == symbol)
            .select(["date", "close"])
            .sort("date")
        )
        warmup = frame.filter(pl.col("date") < PROPOSED_START)
        window = frame.filter(pl.col("date") >= PROPOSED_START)
        asset_coverage[key] = {
            "symbol": symbol,
            "rows": frame.height,
            "first_date": str(frame["date"].min()),
            "last_date": str(frame["date"].max()),
            "warmup_closes_before_start": warmup.height,
            "window_rows": window.height,
            "has_200_close_warmup": warmup.height >= 200,
            "covers_proposed_start": not window.is_empty() and window["date"].min() <= PROPOSED_START,
        }

    membership_dates = members["effective_date"].unique().sort()
    membership_at_start = members.filter(pl.col("effective_date") <= PROPOSED_START)
    last_membership_date = membership_at_start["effective_date"].max()
    membership_count = membership_at_start.filter(
        pl.col("effective_date") == last_membership_date
    ).height

    checks = {
        "D1_panel_loaded": panel.height > 0,
        "D2_required_fields_present": all(field in work.columns for field in REQUIRED_FIELDS),
        "D3_first_candidate_within_may_2022": first_date <= date(2022, 5, 31),
        "D4_2022_candidate_days_ge_120": int(
            yearly.filter(pl.col("year") == 2022)["days_ge_10"].sum()
        ) >= 120,
        "D5_2023_candidate_days_ge_200": int(
            yearly.filter(pl.col("year") == 2023)["days_ge_10"].sum()
        ) >= 200,
        "D6_membership_covers_start": last_membership_date is not None
        and last_membership_date <= PROPOSED_START
        and membership_count >= 250,
        "D7_all_assets_cover_start_with_gold_warmup": all(
            item["covers_proposed_start"] and item["has_200_close_warmup"]
            for item in asset_coverage.values()
        ),
    }

    after = {
        "kline_daily_enriched": tree_hash(DATA / "kline_daily_enriched"),
        "alt_hs300": tree_hash(DATA / "ext_data/alt_hs300/timeseries"),
        "financials": {
            name: sha256(DATA / "financials" / name / "part.parquet")
            for name in ("metrics", "income", "balance_sheet", "cash_flow")
        },
        "membership": sha256(MEMBERSHIP),
        "defensive_assets": {
            key: sha256(ASSET_CACHE / filename)
            for key, (_symbol, filename) in ASSETS.items()
        },
    }
    checks["D8_input_trees_stable"] = before == after

    payload = {
        "round": "R344",
        "status": "coverage_only_preflight_no_returns",
        "strategy": {
            "id": STRATEGY_ID,
            "file": str(strategy.file_path.relative_to(ROOT)),
            "sha256": sha256(Path(strategy.file_path)),
            "required_features": sorted(strategy.required_features),
            "scoring": strategy.meta["scoring"],
        },
        "audit_period": [str(START), str(END)],
        "proposed_extended_start": str(PROPOSED_START),
        "first_candidate_date": str(first_date),
        "first_candidate_count": int(first_any["candidates"][0]),
        "first_date_ge_10": str(first_10["date"][0]),
        "first_date_ge_30": str(first_30["date"][0]),
        "required_field_coverage_on_first_date": first_field_coverage,
        "candidate_coverage_by_year": yearly.to_dicts(),
        "asset_coverage": asset_coverage,
        "membership": {
            "first_date": str(membership_dates.min()),
            "last_date": str(membership_dates.max()),
            "last_snapshot_at_or_before_start": str(last_membership_date),
            "snapshot_symbols": membership_count,
        },
        "checks": checks,
        "all_checks": all(checks.values()),
        "input_isolation": {"before": before, "after": after, "stable": before == after},
        "no_return_or_backtest_evaluated": True,
        "next": "If all checks pass, freeze R344 window and return/risk gates before running the portfolio backtest.",
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n",
        encoding="ascii",
    )
    print(json.dumps({
        "status": payload["status"],
        "first_candidate_date": payload["first_candidate_date"],
        "checks": checks,
        "all_checks": payload["all_checks"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0 if payload["all_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
