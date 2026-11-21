"""R387: frozen-parameter forward shadow observation on data through 2026-11-20.

The R386 portfolio parameters and A/B/C strategy code are treated as immutable.
This runner reruns the complete warmup, reports only the eleven newly observed
trading days, and fails closed before performance if any required PIT input is
missing, stale, duplicated, or inconsistent with the preregistration.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import shutil
import statistics as st
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
RESEARCH = DATA / "research"
R40 = RESEARCH / "r40_engine_data"
PREREG = EVIDENCE / "r387_preregistration.json"
R385_RUNNER = EVIDENCE / "run_r385_pit_platform_monte_carlo.py"
R386_RUNNER = EVIDENCE / "run_r386_mc_advantage_confirmation.py"
R386_REPORT = EVIDENCE / "r386_mc_advantage_confirmation.json"
R385_REPORT = EVIDENCE / "r385_pit_platform_monte_carlo.json"
R381_RUNNER = EVIDENCE / "run_r381_pit_data_audit.py"
R371_RUNNER = EVIDENCE / "run_r371_alpha_parameter_grid.py"
R387_MEMBERSHIP = RESEARCH / "r387_membership/daily_snapshots.parquet"
R387_EXT = RESEARCH / "r387_pit_ext/alt_hs300_required_fields.parquet"
R381_EXT = RESEARCH / "r381_pit_industry/alt_hs300_required_fields.parquet"
ASSET_ROOT = RESEARCH / "r331_defensive_assets"
CACHE = RESEARCH / "r387_forward_shadow_cache"
OUTPUT = EVIDENCE / "r387_forward_shadow_confirmation.json"
RESULT = EVIDENCE / "r387_forward_shadow_result.md"
START = date(2022, 5, 5)
ANCHOR = date(2026, 11, 5)
NEW_START = date(2026, 11, 6)
END = date(2026, 11, 20)
NEW_SESSIONS = (
    date(2026, 11, 6),
    date(2026, 11, 9),
    date(2026, 11, 10),
    date(2026, 11, 11),
    date(2026, 11, 12),
    date(2026, 11, 13),
    date(2026, 11, 16),
    date(2026, 11, 17),
    date(2026, 11, 18),
    date(2026, 11, 19),
    date(2026, 11, 20),
)
EXPECTED_PARAMETERS = {
    "c_satellite_cap": 0.13,
    "equity_target_vol": 0.17,
    "overlay_lookback_days": 110,
    "overlay_target_vol": 0.17,
    "overlay_equity_weight_max": 0.37,
}
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_CELL = "fade05_h30_sl20"
B_STRATEGY_ID = "r371_industry_fade_5d_quality_h20_sl20"
C_STRATEGY = "ai_hs300_quality_momentum_broad_h60"
COHORT_STARTS = (date(2022, 5, 5), date(2022, 6, 2), date(2022, 7, 1))
INITIAL_CAPITAL = 1_000_000.0
COSTS = ("2x", "3x", "5x")
MAIN_COST = "2x"
DEFENSIVE_SYMBOLS = {
    "gold": ("518880.SH", "159937.SZ"),
    "treasury": ("511260.SH", "511010.SH"),
    "dividend": ("510880.SH", "515080.SH"),
}
BENCHMARK = "000300.SH"
MACRO_FIELDS = ("equity_risk_premium", "credit_spread_5y_low", "term_spread_10y_2y")
INDUSTRY_FIELDS = ("industry_mom_20d", "stock_rel_industry_20d")
sys.path.insert(0, str(ROOT / "backend"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def expected_sidecar(path: Path) -> str:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists():
        raise RuntimeError(f"missing checksum sidecar: {sidecar.relative_to(ROOT)}")
    return sidecar.read_text(encoding="ascii").split()[0]


def verify_sidecar(path: Path) -> str:
    actual = sha256(path)
    expected = expected_sidecar(path)
    if actual != expected:
        raise RuntimeError(f"checksum mismatch: {path.relative_to(ROOT)}")
    return actual


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R387 output exists; refusing overwrite: {path.name}")


def write_json_with_checksum(path: Path, payload: dict[str, Any]) -> None:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(raw)
    path.with_name(path.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n", encoding="ascii"
    )


def write_text_with_checksum(path: Path, text: str) -> None:
    raw = text.encode("utf-8")
    with path.open("xb") as stream:
        stream.write(raw)
    path.with_name(path.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n", encoding="ascii"
    )


def date_value(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_membership(frame: pl.DataFrame, sessions: Sequence[date]) -> dict[str, Any]:
    required = {"query_date", "update_date", "symbol", "name"}
    _require(required <= set(frame.columns), "R387 membership schema mismatch")
    ordered = tuple(sorted(set(sessions)))
    part = frame.with_columns(
        pl.col("query_date").cast(pl.Date),
        pl.col("update_date").cast(pl.Date),
    ).filter(pl.col("query_date").is_in(ordered))
    future = part.filter(pl.col("update_date") > pl.col("query_date"))
    duplicates = part.height - part.unique(subset=["query_date", "symbol"]).height
    _require(duplicates == 0, "R387 membership contains duplicate query_date/symbol rows")
    counts = part.group_by("query_date").agg(pl.len().alias("rows"), pl.col("symbol").n_unique().alias("symbols"))
    _require(counts.height == len(ordered), "R387 membership misses a required session")
    _require(set(counts["symbols"].to_list()) == {300}, "each R387 membership snapshot must contain 300 symbols")
    _require(set(counts["rows"].to_list()) == {300}, "each R387 membership snapshot must have exactly 300 rows")
    _require(future.height == 0, "R387 membership contains future update dates")
    _require(part["query_date"].max() == END, "R387 membership tail is stale")
    return {
        "sessions": len(ordered),
        "rows": part.height,
        "symbols_per_session": sorted(set(counts["symbols"].to_list())),
        "last_snapshot_date": str(part["query_date"].max()),
        "future_update_rows": future.height,
        "duplicate_rows": duplicates,
        "complete": True,
    }


def validate_enriched(sessions: Sequence[date], membership: pl.DataFrame) -> dict[str, Any]:
    ordered = tuple(sorted(set(sessions)))
    paths = [DATA / "kline_daily_enriched" / f"date={day.isoformat()}" / "part.parquet" for day in ordered]
    missing = [path.relative_to(ROOT).as_posix() for path in paths if not path.exists()]
    _require(not missing, f"R387 enriched partitions missing: {missing}")
    frame = pl.concat([pl.read_parquet(path) for path in paths], how="diagonal_relaxed").with_columns(
        pl.col("date").cast(pl.Date)
    )
    required = {"symbol", "date", "raw_close", "close"}
    _require(required <= set(frame.columns), "R387 enriched schema mismatch")
    duplicates = frame.height - frame.unique(subset=["symbol", "date"]).height
    _require(duplicates == 0, "R387 enriched contains duplicate symbol/date rows")
    counts = frame.group_by("date").agg(
        pl.len().alias("rows"),
        pl.col("symbol").n_unique().alias("symbols"),
        pl.col("raw_close").null_count().alias("raw_close_null"),
        pl.col("close").null_count().alias("close_null"),
    )
    _require(counts.height == len(ordered), "R387 enriched sessions mismatch")
    _require(set(counts["rows"].to_list()) == {300}, "each R387 enriched session must have 300 rows")
    _require(set(counts["symbols"].to_list()) == {300}, "each R387 enriched session must have 300 symbols")
    _require(counts["raw_close_null"].sum() == 0 and counts["close_null"].sum() == 0, "R387 enriched prices contain nulls")
    expected_by_day = {
        day: set(membership.filter(pl.col("query_date") == day)["symbol"].to_list()) for day in ordered
    }
    missing_symbols: dict[str, list[str]] = {}
    for day in ordered:
        actual = set(frame.filter(pl.col("date") == day)["symbol"].to_list())
        diff = sorted(expected_by_day[day] - actual)
        if diff:
            missing_symbols[day.isoformat()] = diff
    _require(not missing_symbols, f"R387 enriched misses membership symbols: {missing_symbols}")
    partition_dates = [date_value(path.parent.name.split("=", 1)[1]) for path in (DATA / "kline_daily_enriched").glob("date=*/part.parquet")]
    future_partitions = [str(day) for day in partition_dates if day > END]
    _require(not future_partitions, f"future enriched partitions present: {future_partitions}")
    return {
        "sessions": len(ordered),
        "rows": frame.height,
        "symbols_per_session": 300,
        "missing_symbols": {},
        "duplicate_rows": duplicates,
        "future_partitions": [],
        "latest_partition": str(max(partition_dates)),
        "complete": True,
    }


def validate_extension(
    extension: pl.DataFrame,
    membership: pl.DataFrame,
    sessions: Sequence[date],
    old_extension: pl.DataFrame,
) -> dict[str, Any]:
    required = {"symbol", "date", *MACRO_FIELDS, *INDUSTRY_FIELDS, "holder_top10_free_float_share"}
    _require(required <= set(extension.columns), "R387 extension schema mismatch")
    ext = extension.with_columns(pl.col("date").cast(pl.Date))
    old = old_extension.with_columns(pl.col("date").cast(pl.Date))
    ordered = tuple(sorted(set(sessions)))
    prefix = ext.filter(pl.col("date") <= ANCHOR).sort(["date", "symbol"])
    _require(prefix.equals(old.sort(["date", "symbol"])), "R387 extension changed the frozen R381 history")
    new = ext.filter(pl.col("date").is_in(ordered))
    counts = new.group_by("date").agg(pl.len().alias("rows"), pl.col("symbol").n_unique().alias("symbols"))
    _require(counts.height == len(ordered), "R387 extension misses a new session")
    _require(set(counts["rows"].to_list()) == {300} and set(counts["symbols"].to_list()) == {300}, "new extension sessions must contain 300 symbols")
    _require(ext["date"].max() == END, "R387 extension tail is stale")
    duplicates = new.height - new.unique(subset=["date", "symbol"]).height
    _require(duplicates == 0, "R387 extension contains duplicate rows")
    nulls = new.select([pl.col(column).null_count().alias(column) for column in (*MACRO_FIELDS, *INDUSTRY_FIELDS)]).to_dicts()[0]
    _require(not any(nulls.values()), f"R387 extension has null macro/industry fields: {nulls}")
    member_sets = {day: set(membership.filter(pl.col("query_date") == day)["symbol"].to_list()) for day in ordered}
    for day in ordered:
        actual = set(new.filter(pl.col("date") == day)["symbol"].to_list())
        _require(actual == member_sets[day], f"R387 extension universe mismatch on {day}")
    holder_null = int(new["holder_top10_free_float_share"].null_count())
    return {
        "prefix_rows": prefix.height,
        "prefix_matches_r381": True,
        "new_rows": new.height,
        "macro_nulls": {key: int(value) for key, value in nulls.items() if key in MACRO_FIELDS},
        "industry_nulls": {key: int(value) for key, value in nulls.items() if key in INDUSTRY_FIELDS},
        "holder_null_rows": holder_null,
        "holder_policy": "stale-forward because no complete new announcement vintage was established",
        "duplicate_rows": duplicates,
        "last_date": str(ext["date"].max()),
        "complete": True,
    }


def holder_announcement_tail() -> str:
    paths = sorted((RESEARCH / "r87_sina_holder_full").glob("*.parquet"))
    paths.extend(sorted((RESEARCH / "r163_holder_batch").glob("*.parquet")))
    _require(bool(paths), "R387 holder cache is unavailable")
    frame = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .select(pl.col("announce_date").cast(pl.Date))
        .collect()
    )
    _require(frame.height > 0, "R387 holder cache is empty")
    return str(frame["announce_date"].max())


def validate_assets() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for kind, symbols in DEFENSIVE_SYMBOLS.items():
        path = ASSET_ROOT / f"{kind}_etf_qfq.parquet"
        _require(path.exists(), f"missing defensive asset cache: {path.name}")
        frame = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
        for symbol in symbols:
            part = frame.filter(pl.col("symbol") == symbol)
            _require(not part.is_empty(), f"missing defensive symbol {symbol}")
            _require(part["date"].max() == END, f"defensive symbol {symbol} tail is stale")
            window = part.filter(pl.col("date").is_in(NEW_SESSIONS))
            _require(window.height == len(NEW_SESSIONS), f"defensive symbol {symbol} misses new sessions")
            _require(window.height == window["date"].n_unique(), f"defensive symbol {symbol} has duplicate dates")
            _require(window["close"].null_count() == 0 and window.filter(pl.col("close") <= 0).is_empty(), f"defensive symbol {symbol} has invalid close")
        result[kind] = {"symbols": list(symbols), "last_date": str(frame["date"].max()), "new_rows": len(symbols) * len(NEW_SESSIONS)}
    return result


def validate_macro_caches() -> dict[str, Any]:
    files = {
        "credit_spread_2026": RESEARCH / "r92_credit_spread/2026.parquet",
        "bond_10y": RESEARCH / "macro/bond_10y.parquet",
        "hs300_pe": RESEARCH / "r93_erp/hs300_pe.parquet",
    }
    tails: dict[str, str] = {}
    for name, path in files.items():
        _require(path.exists(), f"missing macro cache: {name}")
        frame = pl.read_parquet(path)
        date_column = "日期" if "日期" in frame.columns else "date"
        _require(date_column in frame.columns and frame.height > 0, f"invalid macro cache: {name}")
        latest = date_value(frame[date_column].max())
        _require(latest == END, f"macro cache {name} tail is stale: {latest}")
        tails[name] = str(latest)
    return {"tails": tails, "complete": True}


def benchmark_frame() -> pl.DataFrame:
    paths = sorted((DATA / "kline_index_enriched").glob("date=*/part.parquet"))
    frame = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter((pl.col("symbol") == BENCHMARK) & pl.col("date").is_between(ANCHOR, END))
        .select(["date", "close"])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date), pl.col("close").cast(pl.Float64))
        .sort("date")
    )
    _require(frame.height == len(NEW_SESSIONS) + 1, "CSI 300 benchmark does not cover anchor plus eleven new sessions")
    expected_dates = [ANCHOR, *NEW_SESSIONS]
    _require(frame["date"].to_list() == expected_dates, "CSI 300 benchmark dates contain duplicates or gaps")
    _require(frame["close"].null_count() == 0 and frame.filter(pl.col("close") <= 0).is_empty(), "CSI 300 benchmark contains invalid close")
    return frame


def verify_inputs() -> dict[str, Any]:
    prereg_hash = verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    _require(prereg.get("round") == "R387", "R387 preregistration round mismatch")
    _require(prereg["freeze"]["parameters"] == EXPECTED_PARAMETERS, "R387 parameter freeze mismatch")
    _require(prereg["cost_protocol"]["five_x_can_hard_reject"] is False, "R387 preregistration permits a 5x hard reject")
    _require(prereg["evaluation_protocol"]["performance_hard_gate_on_eleven_days"] is False, "R387 preregistration introduced an 11-day hard gate")

    expected_inputs = prereg["input_sha256"]
    actual_inputs: dict[str, str] = {}
    input_paths = {
        "r386_runner": R386_RUNNER,
        "r386_report": R386_REPORT,
        "r386_preregistration": EVIDENCE / "r386_preregistration.json",
        "r385_runner": R385_RUNNER,
        "r385_report": R385_REPORT,
        "engine": ROOT / "backend/app/backtest/engine.py",
        "strategy_a": DATA / "strategies/ai" / f"{A_STRATEGY}.py",
        "strategy_c": DATA / "strategies/ai" / f"{C_STRATEGY}.py",
        "membership": R387_MEMBERSHIP,
        "extension_frame": R387_EXT,
        "gold_assets": ASSET_ROOT / "gold_etf_qfq.parquet",
        "treasury_assets": ASSET_ROOT / "treasury_etf_qfq.parquet",
        "dividend_assets": ASSET_ROOT / "dividend_etf_qfq.parquet",
        "credit_spread_2026": RESEARCH / "r92_credit_spread/2026.parquet",
        "bond_10y": RESEARCH / "macro/bond_10y.parquet",
        "hs300_pe": RESEARCH / "r93_erp/hs300_pe.parquet",
    }
    for name, path in input_paths.items():
        _require(path.exists(), f"R387 input missing: {name}")
        actual_inputs[name] = sha256(path)
        _require(actual_inputs[name] == expected_inputs[name], f"R387 input hash changed after preregistration: {name}")

    r386_report = json.loads(R386_REPORT.read_text(encoding="utf-8"))
    r385_report = json.loads(R385_REPORT.read_text(encoding="utf-8"))
    _require(r386_report.get("all_pass") is True, "R386 did not pass")
    _require(r386_report["decision"]["status"] == "candidate_for_paper_trading_review", "R386 candidate status changed")
    _require(r386_report["decision"]["selected_parameters"] == EXPECTED_PARAMETERS, "R386 selected parameters do not match the freeze")
    _require(r386_report["no_leverage"] is True, "R386 no-leverage flag is false")
    _require(r386_report["decision"]["orders_generated"] is False, "R386 unexpectedly generated orders")
    _require(r386_report["decision"]["paper_target_generated"] is False, "R386 unexpectedly generated paper targets")
    _require(r386_report["decision"]["production_enabled"] is False, "R386 unexpectedly enabled production")
    _require(r385_report["engine_identity"]["engine_sha256"] == actual_inputs["engine"], "current engine hash differs from R385/R386 lineage")

    membership = pl.read_parquet(R387_MEMBERSHIP)
    old_extension = pl.read_parquet(R381_EXT)
    extension = pl.read_parquet(R387_EXT)
    sessions = tuple(
        membership.filter((pl.col("query_date") > ANCHOR) & (pl.col("query_date") <= END))
        .select(pl.col("query_date").alias("date"))
        .unique()
        .sort("date")["date"]
        .to_list()
    )
    _require(sessions == NEW_SESSIONS, "R387 official membership sessions do not match the preregistered calendar")
    membership_audit = validate_membership(membership, sessions)
    enriched_audit = validate_enriched(sessions, membership)
    extension_audit = validate_extension(extension, membership, sessions, old_extension)
    asset_audit = validate_assets()
    macro_audit = validate_macro_caches()
    benchmark = benchmark_frame()
    holder_tail = holder_announcement_tail()
    _require(date_value(holder_tail) < NEW_START, "holder announcement tail unexpectedly enters the shadow window")

    return {
        "preregistration": prereg,
        "preregistration_sha256": prereg_hash,
        "r386_report": r386_report,
        "r385_report": r385_report,
        "sessions": sessions,
        "membership": membership,
        "extension": extension,
        "benchmark": benchmark,
        "input_sha256": actual_inputs,
        "data_audit": {
            "membership": membership_audit,
            "enriched": enriched_audit,
            "extension": extension_audit,
            "assets": asset_audit,
            "macro": macro_audit,
            "benchmark": {"symbol": BENCHMARK, "dates": benchmark.height, "last_date": str(benchmark["date"].max())},
            "holder_announcement_tail": holder_tail,
            "all_pass": True,
        },
    }


def _cache_meta(
    kind: str,
    strategy_id: str,
    start: date,
    cost: str,
    label: str,
    *,
    strategy_hash: str,
    overrides: dict[str, Any],
    hold: int | None,
    initial_capital: float,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    return {
        "round": "R387",
        "kind": kind,
        "strategy_id": strategy_id,
        "start": str(start),
        "end": str(END),
        "cost": cost,
        "label": label,
        "strategy_hash": strategy_hash,
        "overrides": overrides,
        "hold": hold,
        "initial_capital": initial_capital,
        **provenance,
    }


def _curve_frame(curve: list[dict[str, Any]]) -> pl.DataFrame:
    return (
        pl.DataFrame([{"date": date_value(row["date"]), "value": float(row["value"])} for row in curve])
        .sort("date")
        .unique(subset=["date"], keep="last")
    )


def _trade_window_summary(trades: list[dict[str, Any]], initial_capital: float) -> dict[str, Any]:
    entries = []
    exits = []
    for trade in trades:
        entry_day = date_value(trade["entry_date"])
        exit_day = date_value(trade["exit_date"])
        if NEW_START <= entry_day <= END:
            entries.append(trade)
        if NEW_START <= exit_day <= END:
            exits.append(trade)
    buy_notional = sum(float(item.get("entry_value") or 0.0) for item in entries)
    sell_notional = sum(float(item.get("exit_value") or 0.0) for item in exits)
    gross_turnover = (buy_notional + sell_notional) / initial_capital
    return {
        "entry_count": len(entries),
        "exit_count": len(exits),
        "buy_notional": round(buy_notional, 2),
        "sell_notional": round(sell_notional, 2),
        "gross_turnover": round(gross_turnover, 9),
        "one_way_turnover": round(gross_turnover / 2.0, 9),
    }


def run_engine(verified: dict[str, Any]) -> dict[str, Any]:
    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
    from app.config import settings
    from app.factors.store import load_into_registry
    from app.indicators import pipeline
    from app.services.industry_map import industry_loader_for
    from app.strategy.engine import StrategyEngine
    from app.tickflow.repository import DataStore, KlineRepository

    r371 = module(R371_RUNNER, "r387_r371")
    r381 = module(R381_RUNNER, "r387_r381")
    definitions = r371._write_variant_definitions()
    membership = r381.membership_for_engine(R387_MEMBERSHIP)
    membership_hash = hashlib.sha256(membership.write_csv().encode("utf-8")).hexdigest()
    ext_frame = pl.read_parquet(R387_EXT).with_columns(pl.col("date").cast(pl.Date))
    strategy_paths = {
        "A": DATA / "strategies/ai" / f"{A_STRATEGY}.py",
        "C": DATA / "strategies/ai" / f"{C_STRATEGY}.py",
        "B": ROOT / definitions["strategies"]["5"]["path"],
    }
    r385_report = verified["r385_report"]
    variant_hashes = {str(key): str(value) for key, value in r385_report["engine_identity"]["variant_sha256"].items()}
    for fade, path in (("3", ROOT / definitions["strategies"]["3"]["path"]), ("5", strategy_paths["B"]), ("10", ROOT / definitions["strategies"]["10"]["path"])):
        _require(sha256(path) == variant_hashes[fade], f"R387 frozen B variant hash changed for fade={fade}")

    provenance = {
        "membership_sha256": membership_hash,
        "extension_frame_sha256": sha256(R387_EXT),
        "engine_sha256": sha256(ROOT / "backend/app/backtest/engine.py"),
        "r371_runner_sha256": sha256(R371_RUNNER),
        "strategy_a_sha256": sha256(strategy_paths["A"]),
        "strategy_b_sha256": sha256(strategy_paths["B"]),
        "strategy_c_sha256": sha256(strategy_paths["C"]),
        "variant_sha256": variant_hashes,
    }
    outputs: dict[str, tuple[pl.DataFrame, dict[str, Any]]] = {}
    CACHE.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="r387_engine_data_"))
    old_data = settings.data_dir
    try:
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
        module(EVIDENCE / "r360_isolation.py", "r387_isolation").prepare_isolated_data_root(engine_root, DATA)
        r381.write_ext_timeseries(engine_root, ext_frame)
        for namespace in ("custom_factors", "custom_signals"):
            for source in sorted((r371.VARIANTS / namespace).glob("*")):
                shutil.copy2(source, engine_root / "user_data" / namespace / source.name)
        settings.data_dir = engine_root
        load_into_registry(engine_root)
        pipeline.invalidate_custom_signals()
        engine = StrategyEngine(
            strategy_dirs=[DATA / "strategies/ai", r371.VARIANTS / "strategies"],
            override_loader=lambda _sid: {},
            industry_loader=industry_loader_for(engine_root),
        )
        service = StrategyBacktestService(BacktestEngine(KlineRepository(DataStore(engine_root))), engine)

        def run_one(
            kind: str,
            strategy_id: str,
            start: date,
            cost: str,
            max_positions: int,
            *,
            label: str,
            strategy_path: Path,
            overrides: dict[str, Any] | None = None,
            hold: int | None = None,
            initial_capital: float = INITIAL_CAPITAL,
        ) -> tuple[pl.DataFrame, dict[str, Any]]:
            effective_overrides = overrides or {}
            meta = _cache_meta(
                kind,
                strategy_id,
                start,
                cost,
                label,
                strategy_hash=sha256(strategy_path),
                overrides=effective_overrides,
                hold=hold,
                initial_capital=initial_capital,
                provenance=provenance,
            )
            name = f"{kind}_{cost}_{label}"
            parquet = CACHE / f"{name}.parquet"
            sidecar = CACHE / f"{name}.json"
            if parquet.exists() and sidecar.exists():
                cached = json.loads(sidecar.read_text(encoding="utf-8"))
                if cached.get("meta") == meta:
                    print(f"R387 cache hit {name}", flush=True)
                    return _curve_frame(json.loads(parquet.read_text())), {**cached["run"], "cache_hit": True}
            stock = {
                "2x": {"fees": 0.0004, "commission": 0.0004, "stamp": 0.0005, "slippage": 10.0},
                "3x": {"fees": 0.0006, "commission": 0.0006, "stamp": 0.001, "slippage": 15.0},
                "5x": {"fees": 0.001, "commission": 0.001, "stamp": 0.00125, "slippage": 25.0},
            }[cost]
            config = StrategyBacktestConfig(
                strategy_id=strategy_id,
                symbols=None,
                start=start,
                end=END,
                params={},
                overrides=effective_overrides,
                matching="open_t+1",
                fees_pct=stock["fees"],
                commission_pct=stock["commission"],
                stamp_tax_pct=stock["stamp"],
                slippage_bps=stock["slippage"],
                holding_days=hold,
                initial_capital=initial_capital,
                max_positions=max_positions,
                max_exposure_pct=1.0,
                position_sizing="equal",
                mode="position",
                asset_type="stock",
                hs300=True,
            )
            started = time.perf_counter()
            with r371._patched_membership(membership):
                result = service.run(config)
            if result.error:
                raise RuntimeError(f"R387 {kind} {label} {cost}: {result.error}")
            curve = _curve_frame(result.equity_curve)
            if curve["date"].min() != start or curve["date"].max() != END:
                raise RuntimeError(f"R387 {kind} {label} {cost}: date coverage mismatch")
            elapsed = time.perf_counter() - started
            trade_summary = _trade_window_summary(result.trades, initial_capital)
            run = {
                "elapsed_seconds": round(elapsed, 3),
                "cache_hit": False,
                "full_trade_count": len(result.trades),
                "window_trades": trade_summary,
            }
            curve.write_parquet(parquet.with_suffix(".data.parquet"), compression="zstd")
            # Curve JSON keeps cache reads independent of row-level Parquet type drift.
            parquet.write_text(
                json.dumps(
                    [{"date": str(row["date"]), "value": float(row["value"])} for row in curve.iter_rows(named=True)],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            sidecar.write_text(
                json.dumps({"meta": meta, "run": run}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(f"R387 ran {kind} {label} {cost}: trades={len(result.trades)} elapsed={elapsed:.1f}s", flush=True)
            return curve, run

        for cost in COSTS:
            outputs[f"A_{cost}"] = run_one(
                "A", A_STRATEGY, START, cost, 10, label="A", strategy_path=strategy_paths["A"]
            )
            fade, hold, stop = 5, 30, -0.20
            outputs[f"B_{cost}_{B_CELL}"] = run_one(
                "B",
                definitions["strategies"][str(fade)]["id"],
                START,
                cost,
                10,
                label=B_CELL,
                strategy_path=strategy_paths["B"],
                overrides={"max_hold_days": hold, "stop_loss": stop},
                hold=hold,
            )
            for index, cohort_start in enumerate(COHORT_STARTS):
                outputs[f"C_{cost}_{index}"] = run_one(
                    "C",
                    C_STRATEGY,
                    cohort_start,
                    cost,
                    50,
                    label=f"C{index}",
                    strategy_path=strategy_paths["C"],
                    initial_capital=INITIAL_CAPITAL / 3.0,
                )
        return {
            "outputs": outputs,
            "provenance": provenance,
            "membership_sha256": membership_hash,
        }
    finally:
        settings.data_dir = old_data
        pipeline.invalidate_custom_signals()
        shutil.rmtree(work, ignore_errors=True)


def period_metrics(curve: pl.DataFrame, baseline: date = ANCHOR, end: date = END) -> dict[str, Any]:
    part = curve.filter(pl.col("date").is_between(baseline, end)).sort("date").unique(subset=["date"], keep="last")
    if baseline == ANCHOR and end == END:
        _require(part.height == len(NEW_SESSIONS) + 1, "period metrics require anchor plus eleven returns")
    else:
        _require(part.height >= 2, "full-history metrics require at least two observations")
    _require(part["date"].min() == baseline and part["date"].max() == end, "period metrics date coverage mismatch")
    values = [float(value) for value in part["value"].to_list()]
    _require(all(math.isfinite(value) and value > 0 for value in values), "period metrics contain invalid equity values")
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    volatility = st.pstdev(returns)
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    downside = [min(value, 0.0) for value in returns]
    downside_vol = math.sqrt(sum(value * value for value in downside) / len(downside))
    return {
        "start": str(part["date"][0]),
        "end": str(part["date"][-1]),
        "observations": len(values),
        "return_observations": len(returns),
        "total_return": total,
        "annual": (1.0 + total) ** (252.0 / len(returns)) - 1.0,
        "sharpe": st.mean(returns) / volatility * math.sqrt(252.0) if volatility else 0.0,
        "mdd": mdd,
        "positive_day_rate": sum(value > 0.0 for value in returns) / len(returns),
        "downside_vol_annual": downside_vol * math.sqrt(252.0),
        "best_day": max(returns),
        "worst_day": min(returns),
    }


def normalize_curve(curve: pl.DataFrame, baseline: date = ANCHOR) -> list[dict[str, Any]]:
    part = curve.filter(pl.col("date").is_between(baseline, END)).sort("date")
    base = float(part.filter(pl.col("date") == baseline)["value"][0])
    return [
        {"date": str(row["date"]), "value": float(row["value"]) / base}
        for row in part.iter_rows(named=True)
    ]


def latest_weight_row(rows: list[dict[str, Any]], cutoff: date) -> dict[str, Any]:
    eligible = [row for row in rows if date_value(row["date"]) <= cutoff]
    _require(bool(eligible), "sleeve weight history is empty")
    return max(eligible, key=lambda row: date_value(row["date"]))


def exposure_summary(bundle: dict[str, Any], sleeve_weights: list[dict[str, Any]]) -> dict[str, Any]:
    rebalances = [row for row in bundle.get("rebalances") or [] if date_value(row["date"]) <= END]
    _require(bool(rebalances), "defensive rebalance history is empty")
    active = latest_weight_row(rebalances, ANCHOR)
    target_keys = ("target_equity_weight", "target_gold_weight", "target_treasury_weight", "target_dividend_weight", "target_cash_weight")
    total_weight = sum(float(active[key]) for key in target_keys)
    sleeve = latest_weight_row(sleeve_weights, ANCHOR)
    sleeve_keys = ("weight_a", "weight_b", "weight_c")
    result = {
        "as_of": str(active["date"]),
        "total_weight": total_weight,
        "equity_weight": float(active["target_equity_weight"]),
        "gold_weight": float(active["target_gold_weight"]),
        "treasury_weight": float(active["target_treasury_weight"]),
        "dividend_weight": float(active["target_dividend_weight"]),
        "cash_weight": float(active["target_cash_weight"]),
        "max_single_asset_weight": max(float(active[key]) for key in (*target_keys[1:], target_keys[0])),
        "sleeve_weights": {key: float(sleeve[key]) for key in sleeve_keys},
        "max_single_strategy_sleeve": max(float(sleeve[key]) for key in sleeve_keys),
        "max_weight_sum": float((bundle.get("weight_summary") or {}).get("max_weight_sum", total_weight)),
    }
    _require(result["total_weight"] <= 1.0 + 1e-12, "R387 defensive active weights exceed 1.0")
    _require(result["max_weight_sum"] <= 1.0 + 1e-12, "R387 historical maximum weight exceeds 1.0")
    return result


def aggregate_trade_summary(outputs: dict[str, tuple[pl.DataFrame, dict[str, Any]]], cost: str) -> dict[str, Any]:
    keys = [key for key in outputs if key.endswith(f"_{cost}")]
    # Keys are A_2x, B_2x_cell and C_2x_i; explicit construction avoids substring ambiguity.
    keys = [f"A_{cost}", f"B_{cost}_{B_CELL}", *(f"C_{cost}_{index}" for index in range(3))]
    entries = sum(int(outputs[key][1]["window_trades"]["entry_count"]) for key in keys)
    exits = sum(int(outputs[key][1]["window_trades"]["exit_count"]) for key in keys)
    buys = sum(float(outputs[key][1]["window_trades"]["buy_notional"]) for key in keys)
    sells = sum(float(outputs[key][1]["window_trades"]["sell_notional"]) for key in keys)
    full_trades = sum(int(outputs[key][1]["full_trade_count"]) for key in keys)
    return {
        "entry_count": entries,
        "exit_count": exits,
        "buy_notional": round(buys, 2),
        "sell_notional": round(sells, 2),
        "gross_turnover": round((buys + sells) / INITIAL_CAPITAL, 9),
        "one_way_turnover": round((buys + sells) / INITIAL_CAPITAL / 2.0, 9),
        "full_trade_count": full_trades,
    }


def compose_costs(verified: dict[str, Any], engine: dict[str, Any]) -> dict[str, Any]:
    runtime = module(R385_RUNNER, "r387_r385_composer")
    runtime.END = END
    runtime.R381_MEMBERSHIP = R387_MEMBERSHIP
    runtime.R381_EXT_FRAME = R387_EXT
    ctx = runtime._context()
    assets = runtime.prepare_assets(ctx)
    outputs = engine["outputs"]
    curves = {key: value[0] for key, value in outputs.items()}
    results: dict[str, Any] = {}
    for cost in COSTS:
        c_curve = runtime.combine_c(curves, cost, ctx)
        bundle = runtime.compose_portfolio(
            ctx,
            assets,
            outputs[f"A_{cost}"][0],
            outputs[f"B_{cost}_{B_CELL}"][0],
            c_curve,
            cost,
            EXPECTED_PARAMETERS,
        )
        curve = bundle["curve"]
        _require(curve.filter(pl.col("date") == ANCHOR).height == 1, f"{cost} portfolio misses anchor date")
        _require(curve.filter(pl.col("date") == END).height == 1, f"{cost} portfolio misses end date")
        exposures = exposure_summary(bundle, bundle["sleeve_weights"])
        sleeve_metrics = {
            "A": period_metrics(outputs[f"A_{cost}"][0]),
            "B": period_metrics(outputs[f"B_{cost}_{B_CELL}"][0]),
            "C": period_metrics(c_curve),
        }
        rebalances = [
            row for row in bundle.get("rebalances") or [] if NEW_START <= date_value(row["date"]) <= END
        ]
        defensive_turnover = sum(float(row["turnover"]) for row in rebalances)
        scenario = runtime.SCENARIOS[cost]
        results[cost] = {
            "metrics": period_metrics(curve),
            "full_history_metrics": period_metrics(curve, baseline=START),
            "normalized_curve": normalize_curve(curve),
            "sleeve_metrics": sleeve_metrics,
            "sleeve_weights": exposures["sleeve_weights"],
            "exposures": exposures,
            "trades": aggregate_trade_summary(outputs, cost),
            "defensive_rebalances": rebalances,
            "defensive_turnover": round(defensive_turnover, 9),
            "defensive_cost_estimate": round(defensive_turnover * scenario["defensive_turnover"], 9),
            "portfolio_cost_assumptions": scenario,
        }
    benchmark_metrics = period_metrics(verified["benchmark"].rename({"close": "value"}))
    for _cost, result in results.items():
        result["benchmark"] = benchmark_metrics
        result["excess_total_return"] = result["metrics"]["total_return"] - benchmark_metrics["total_return"]
        result["excess_sharpe"] = result["metrics"]["sharpe"] - benchmark_metrics["sharpe"]
    return {"costs": results, "benchmark": benchmark_metrics}


def build_report() -> dict[str, Any]:
    verified = verify_inputs()
    engine = run_engine(verified)
    composed = compose_costs(verified, engine)
    two_x = composed["costs"]["2x"]
    five_x = composed["costs"]["5x"]
    no_leverage = all(
        result["exposures"]["total_weight"] <= 1.0 + 1e-12
        and result["exposures"]["max_weight_sum"] <= 1.0 + 1e-12
        for result in composed["costs"].values()
    )
    return {
        "round": "R387",
        "classification": "frozen-parameter forward shadow observation; not an independent blind test",
        "runner_sha256": sha256(Path(__file__)),
        "preregistration_sha256": verified["preregistration_sha256"],
        "window": {
            "warmup_start": str(START),
            "anchor_date": str(ANCHOR),
            "new_start": str(NEW_START),
            "end": str(END),
            "new_sessions": len(NEW_SESSIONS),
            "statistical_decision_supported": False,
        },
        "parameters": EXPECTED_PARAMETERS,
        "input_sha256": verified["input_sha256"],
        "engine_provenance": engine["provenance"],
        "data_audit": verified["data_audit"],
        "cost_protocol": {
            "main": MAIN_COST,
            "robustness": "3x",
            "warning_only": "5x",
            "five_x_hard_rejection": False,
            "five_x_total_return": five_x["metrics"]["total_return"],
        },
        "benchmark": composed["benchmark"],
        "costs": composed["costs"],
        "summary_2x": {
            "total_return": two_x["metrics"]["total_return"],
            "annual": two_x["metrics"]["annual"],
            "sharpe": two_x["metrics"]["sharpe"],
            "mdd": two_x["metrics"]["mdd"],
            "excess_total_return": two_x["excess_total_return"],
            "one_way_turnover": two_x["trades"]["one_way_turnover"],
            "max_weight_sum": two_x["exposures"]["total_weight"],
        },
        "no_leverage": no_leverage,
        "all_pass": bool(verified["data_audit"]["all_pass"] and no_leverage),
        "decision": {
            "status": "shadow_observation_complete_no_decision",
            "upgrade_supported": False,
            "blind_test_claim_supported": False,
            "strategy_decision_supported": False,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
            "reason": "Eleven trading days are a data-freshness and behavior observation window, not a statistical selection sample.",
        },
        "known_limits": verified["preregistration"]["known_limits"],
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# R387 · R386 冻结参数前瞻影子观察",
        "",
        "R387 先把名单、行情、行业、宏观和防御资产更新到 2026-11-20，再冻结 R386 参数与 A/B/C 策略重跑完整热身。",
        "只把 2026-11-06 至 2026-11-20 的 11 个交易日作为新样本展示；样本不足以支持策略取舍或升级。",
        "",
        "## 样本与口径",
        "",
        f"- 热身起点：{report['window']['warmup_start']}",
        f"- 新样本：{report['window']['new_start']} 至 {report['window']['end']}，{report['window']['new_sessions']} 个交易日",
        "- 基准：沪深300（000300.SH）",
        "- 主成本：2×；稳健性：3×；5× 只作 warning，不硬否决",
        "- 不上杠杆：最大总权重不得超过 1.0",
        "",
        "## 实际回测",
        "",
        "| 成本 | 总收益 | 年化* | Sharpe | MDD | 超额收益 | 单边换手 | 最大总权重 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    benchmark = report["benchmark"]
    for cost in COSTS:
        result = report["costs"][cost]
        metrics = result["metrics"]
        lines.append(
            f"| {cost} | {metrics['total_return']:.2%} | {metrics['annual']:.2%} | "
            f"{metrics['sharpe']:.3f} | {metrics['mdd']:.2%} | {result['excess_total_return']:.2%} | "
            f"{result['trades']['one_way_turnover']:.2%} | {result['exposures']['total_weight']:.4f} |"
        )
    lines.extend([
        "",
        f"\\* 年化按 11 个收益日机械折算，只用于统一展示，不代表一年预期。基准同期总收益 {benchmark['total_return']:.2%}。",
        "",
        "## Sleeve 同期表现（2×）",
        "",
        "| Sleeve | 总收益 | Sharpe | MDD | 权重（11/05） |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    two_x = report["costs"]["2x"]
    for key, label in (("A", "A 宏观质量"), ("B", "B 行业相对强度衰减"), ("C", "C 质量动量三 cohort")):
        metrics = two_x["sleeve_metrics"][key]
        weight = two_x["sleeve_weights"][f"weight_{key.lower()}"]
        lines.append(
            f"| {label} | {metrics['total_return']:.2%} | {metrics['sharpe']:.3f} | "
            f"{metrics['mdd']:.2%} | {weight:.2%} |"
        )
    lines.extend([
        "",
        "## 交易与暴露",
        "",
        f"- 2× 股票买卖金额：买入 {two_x['trades']['buy_notional']/1e6:.2f} 百万元，卖出 {two_x['trades']['sell_notional']/1e6:.2f} 百万元。",
        f"- 2× 股票成交：{two_x['trades']['entry_count']} 笔买入、{two_x['trades']['exit_count']} 笔卖出。",
        f"- 防御资产新样本月内再平衡：{len(two_x['defensive_rebalances'])} 次；估算成本 {two_x['defensive_cost_estimate']:.4%}。",
        f"- 最大单策略 sleeve 权重：{two_x['exposures']['max_single_strategy_sleeve']:.2%}；最大单一资产权重：{two_x['exposures']['max_single_asset_weight']:.2%}。",
        f"- 无杠杆检查：{'通过' if report['no_leverage'] else '失败'}。",
        "",
        "## 数据完整性",
        "",
        f"- PIT 名单：每日 300 只，最后快照 {report['data_audit']['membership']['last_snapshot_date']}。",
        "- 股票 enriched：11 个新交易日均为 300 只，价格缺失 0，重复 0。",
        "- 扩展因子：R381 历史前缀完全保留；新样本宏观、行业字段无缺失。",
        f"- 股东字段：按已知公告向后延续，公告尾部 {report['data_audit']['holder_announcement_tail']}，没有把未确认的新公告写入。",
        f"- 防御资产和基准均覆盖至 {report['window']['end']}。",
        "",
        "## 结论边界",
        "",
        "- 本轮只完成冻结参数下的真实前瞻影子观察，不调参、不用市场状态切换。",
        "- 11 天收益不设硬门槛，也不能据此宣称策略升级、独立盲测或可实盘。",
        "- 5× 仅记录 warning；没有订单、纸面目标或生产配置。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    ensure_outputs_absent()
    report = build_report()
    write_json_with_checksum(OUTPUT, report)
    write_text_with_checksum(RESULT, markdown(report))
    print(json.dumps(report["summary_2x"], ensure_ascii=False, indent=2))
    print(json.dumps(report["decision"], ensure_ascii=False, indent=2))
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
