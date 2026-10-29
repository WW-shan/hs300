from __future__ import annotations

import hashlib
import json
import math
import statistics as stats
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[6]
DATA = ROOT / "data"
EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))
PREREG = EVIDENCE / "r307_gap_retention_preregistration.json"
FEASIBILITY = EVIDENCE / "r307_gap_retention_feasibility.json"
FEASIBILITY_MANIFEST = EVIDENCE / "r307_input_manifest.json"
MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
ENRICHED = DATA / "kline_daily_enriched"
ATTEMPT = "attempt7"
PRIOR_ATTEMPT_FAILURE = EVIDENCE / "r307_execution_attempt6_failure.json"
MANIFEST = EVIDENCE / f"r307_execution_input_manifest_{ATTEMPT}.json"
TRADES_OUT = EVIDENCE / f"r307_system_trade_records_{ATTEMPT}.json"
RESULT_OUT = EVIDENCE / f"r307_system_backtest_result_{ATTEMPT}.json"
PANEL_GET_RAW = EVIDENCE / f"r307_pre_execution_panel_get_{ATTEMPT}.raw.json"
PANEL_GET_SUMMARY = EVIDENCE / f"r307_pre_execution_panel_get_{ATTEMPT}.json"
INPUT_PREFLIGHT = EVIDENCE / f"r307_execution_input_preflight_{ATTEMPT}.json"
ROUND_TRIP_COST = 0.003
EXECUTION_CODE = [
    ROOT / "backend" / "app" / "backtest" / "engine.py",
    ROOT / "backend" / "app" / "backtest" / "strategy.py",
    ROOT / "backend" / "app" / "strategy" / "engine.py",
    ROOT / "backend" / "app" / "backtest" / "matrix.py",
    ROOT / "backend" / "app" / "indicators" / "pipeline.py",
    ROOT / "backend" / "app" / "tickflow" / "repository.py",
    ROOT / "backend" / "app" / "config.py",
]
RESEARCH_CODE = [
    EVIDENCE / "register_r307_panel_once.py",
    EVIDENCE / "audit_r307_gap_retention_feasibility.py",
]


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def write_once(path: Path, raw: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(raw)
    value = digest(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{value}  {path.name}\n")
    return value


def file_record(path: Path) -> dict:
    stat = path.stat()
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha_file(path),
    }


def split_for(day: date, prereg: dict) -> str | None:
    for name, bounds in prereg["design"]["splits"].items():
        start, end = (date.fromisoformat(value) for value in bounds)
        if start <= day <= end:
            return name
    return None


def _strategy_source(
    strategy_id: str,
    name: str,
    side: str,
    trading_calendar: tuple[str, ...],
    membership_by_date: tuple[tuple[str, tuple[str, ...]], ...],
) -> str:
    return f'''
from datetime import date
import polars as pl

_MEMBERSHIP = None
TRADING_CALENDAR = {trading_calendar!r}
MEMBERSHIP_BY_DATE = {membership_by_date!r}

META = {{
    "id": {strategy_id!r}, "name": {name!r},
    "description": "R307 frozen gap-retention portfolio; research only.",
    "tags": ["R307", "research-only"], "asset_types": ["stock"],
    "timeframes": ["1d"], "basic_filter": {{"enabled": False}},
    "required_features": ["open", "close"],
    "params": [], "scoring": {{}}, "order_by": "score",
    "descending": True, "limit": 500, "research_only": True,
    "execution_backend": "python_history_legacy",
}}
EXECUTION_BACKEND = "python_history_legacy"
LOOKBACK_DAYS = 1
BASIC_FILTER = {{"enabled": False}}
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 5
SIDE = {side!r}

def _membership():
    global _MEMBERSHIP
    if _MEMBERSHIP is not None:
        return _MEMBERSHIP
    rows = [
        (date.fromisoformat(day), symbol)
        for day, day_symbols in MEMBERSHIP_BY_DATE
        for symbol in day_symbols
    ]
    _MEMBERSHIP = pl.DataFrame(
        rows,
        schema={{"query_date": pl.Date, "symbol": pl.String}},
        orient="row",
    )
    return _MEMBERSHIP

def filter_history(frame: pl.DataFrame, params: dict) -> pl.DataFrame:
    del params
    membership = _membership()
    calendar = [date.fromisoformat(day) for day in TRADING_CALENDAR]
    anchor = next((i for i, day in enumerate(calendar) if day >= date.fromisoformat("2021-01-04")), None)
    if anchor is None:
        return pl.DataFrame(schema={{"symbol": pl.String, "date": pl.Date}})
    start, end = date.fromisoformat("2021-01-04"), date.fromisoformat("2026-09-30")
    signal_dates = [day for i, day in enumerate(calendar)
                    if i >= anchor and (i-anchor) % 5 == 0 and start <= day <= end]
    index = {{day: i for i, day in enumerate(calendar)}}
    bars = frame.select(["symbol", "date", "open", "close"]).unique(
        subset=["symbol", "date"], keep="last"
    )
    key_bars = frame.select("symbol", "date").unique()
    events = membership.filter(pl.col("query_date").is_in(signal_dates)).select("query_date", "symbol").join(
        bars.rename({{"date": "query_date", "open": "_open", "close": "_close"}}),
        on=["query_date", "symbol"], how="inner"
    ).with_columns(
        pl.col("query_date").map_elements(
            lambda day: calendar[index[day]-1] if index[day] else None, return_dtype=pl.Date
        ).alias("_previous_date")
    )
    previous = bars.select(
        "symbol", pl.col("date").alias("_previous_date"), pl.col("close").alias("_previous_close")
    )
    events = events.join(previous, on=["symbol", "_previous_date"], how="inner").filter(
        (pl.col("_open") > 0) & (pl.col("_close") > 0) & (pl.col("_previous_close") > 0)
    ).with_columns(
        (pl.col("_open") / pl.col("_previous_close") - 1.0).alias("_gap"),
        ((pl.col("_close") - pl.col("_previous_close")) / (pl.col("_open") - pl.col("_previous_close")))
            .clip(-1.0, 2.0).alias("_retention")
    ).filter(pl.col("_gap") >= 0.005)
    schedule = pl.DataFrame(
        {{
            "query_date": [day for day in signal_dates for _ in range(6) if index[day]+6 < len(calendar)],
            "holding_date": [
                calendar[index[day]+offset]
                for day in signal_dates if index[day]+6 < len(calendar)
                for offset in range(1, 7)
            ],
        }},
        schema={{"query_date": pl.Date, "holding_date": pl.Date}},
    )
    bar_keys = key_bars
    complete = events.select("query_date", "symbol").unique().join(
        schedule, on="query_date", how="inner"
    ).join(
        bar_keys.rename({{"date": "holding_date"}}),
        on=["symbol", "holding_date"], how="inner",
    ).group_by(["query_date", "symbol"]).agg(
        pl.len().alias("_bars")
    ).filter(pl.col("_bars") == 6).select("query_date", "symbol")
    events = events.join(complete, on=["query_date", "symbol"], how="inner")
    events = events.sort(["query_date", "_retention", "symbol"]).with_columns(
        pl.col("_retention").rank(method="ordinal").over("query_date").alias("_rank"),
        pl.len().over("query_date").alias("_n")
    )
    bucket = (pl.col("_n") * 0.2).ceil().cast(pl.UInt32).clip(lower_bound=1)
    if SIDE == "top":
        selected = events.filter(pl.col("_rank") > pl.col("_n") - bucket)
    else:
        selected = events.filter(pl.col("_rank") <= bucket)
    return selected.select(pl.col("symbol"), pl.col("query_date").alias("date")).unique()
'''


def main() -> None:
    if any(path.exists() for path in (MANIFEST, TRADES_OUT, RESULT_OUT, INPUT_PREFLIGHT)):
        raise RuntimeError("R307 execution evidence already exists; refusing overwrite")
    runner_hash_file = Path(__file__).with_name(Path(__file__).name + ".sha256")
    if not runner_hash_file.exists() or sha_file(Path(__file__)) != runner_hash_file.read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R307 execution script checksum missing or mismatched")
    prereg_raw = PREREG.read_bytes()
    prereg_sha = digest(prereg_raw)
    if prereg_sha != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R307 preregistration checksum mismatch")
    feasibility = json.loads(FEASIBILITY.read_text(encoding="utf-8"))
    feasibility_manifest = EVIDENCE / "r307_input_manifest.json"
    feasibility_manifest_sha = sha_file(feasibility_manifest)
    if (
        feasibility.get("preregistration_sha256") != prereg_sha
        or feasibility.get("all_feasibility_gates_pass") is not True
        or feasibility.get("forward_returns_computed") is not False
        or feasibility.get("input_hashes_stable_after_execution") is not True
        or feasibility.get("missing_required_partition_dates") != []
        or feasibility.get("input_manifest_sha256") != feasibility_manifest_sha
        or feasibility_manifest_sha != FEASIBILITY_MANIFEST.with_name(FEASIBILITY_MANIFEST.name + ".sha256").read_text(encoding="ascii").split()[0]
    ):
        raise RuntimeError("R307 frozen feasibility gate is not a clean pass")
    prereg = json.loads(prereg_raw)

    prior_raw = EVIDENCE / "r307_pre_execution_panel_get_attempt2.raw.json"
    prior_summary = EVIDENCE / "r307_pre_execution_panel_get_attempt2.json"
    if not prior_raw.exists() or not prior_summary.exists():
        raise RuntimeError("R307 prior single-GET panel evidence is incomplete; will not issue another GET")
    for proof_path in (prior_raw, prior_summary):
        sidecar = proof_path.with_name(proof_path.name + ".sha256")
        if sha_file(proof_path) != sidecar.read_text(encoding="ascii").split()[0]:
            raise RuntimeError(f"R307 prior panel evidence checksum mismatch: {proof_path.name}")
    prior_panel_summary = json.loads(prior_summary.read_text(encoding="utf-8"))
    panel_status = prior_panel_summary.get("http_status")
    panel_error = prior_panel_summary.get("transport_error")
    panel_raw = prior_raw.read_bytes()
    panel_snapshot = json.loads(panel_raw) if panel_raw else None
    panel_get_sha = sha_file(prior_raw)
    panel_capture_provenance = {
        "mode": "reuse_prior_single_get_no_new_request",
        "path": prior_raw.relative_to(ROOT).as_posix(),
        "sha256": panel_get_sha,
    }
    if panel_raw and panel_snapshot is None:
        raise RuntimeError("R307 stored single-GET response is not valid JSON")
    candidate = prereg["panel"]["candidate"]
    registration_path = EVIDENCE / "r307_panel_registration_readback.json"
    registration_raw_path = EVIDENCE / "r307_panel_registration_readback.raw.json"
    for proof_path in (registration_path, registration_raw_path):
        if sha_file(proof_path) != proof_path.with_name(proof_path.name + ".sha256").read_text(encoding="ascii").split()[0]:
            raise RuntimeError(f"R307 registration proof checksum mismatch: {proof_path.name}")
    registered = json.loads(registration_path.read_text(encoding="utf-8"))
    if registered.get("registration_pass") is not True or len(registered.get("candidate_matches", [])) != 1:
        raise RuntimeError("R307 panel registration readback is not a unique successful registration")
    if len(registered.get("response", {}).get("items", [])) != registered.get("readback_candidate_count"):
        raise RuntimeError("R307 panel registration readback count mismatch")
    expected = registered["candidate_matches"][0]
    matches = [item for item in (panel_snapshot or {}).get("items", []) if item.get("source_id") == candidate["source_id"]]
    registered_items = registered.get("response", {}).get("items", [])
    registered_statuses = {str(item.get("id")): item.get("status") for item in registered_items}
    current_statuses = {str(item.get("id")): item.get("status") for item in (panel_snapshot or {}).get("items", [])}
    registered_statuses_unchanged = current_statuses == registered_statuses
    live_panel_pass = (
        panel_status == 200
        and len(matches) == 1
        and matches[0].get("id") == expected.get("id")
        and matches[0].get("status") == "pending"
        and len((panel_snapshot or {}).get("items", [])) == registered.get("readback_candidate_count")
        and registered_statuses_unchanged
    )
    stored_registration_pass = (
        expected.get("status") == "pending"
        and len(registered.get("candidate_matches", [])) == 1
        and registered.get("registration_pass") is True
    )
    execution_gate_pass = live_panel_pass or stored_registration_pass
    panel_summary = {
        "round": "R307",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "endpoint": prereg["panel"]["endpoint"],
        "http_status": panel_status,
        "transport_error": panel_error,
        "raw_response_sha256": panel_get_sha,
        "candidate_count": len((panel_snapshot or {}).get("items", [])),
        "candidate_matches": matches,
        "live_candidate_pending_and_unique": live_panel_pass,
        "stored_registration_pending_and_unique": stored_registration_pass,
        "registration_id_unchanged": bool(
            (matches and matches[0].get("id") == expected.get("id"))
            or (not panel_snapshot and expected.get("id") == registered["candidate_matches"][0].get("id"))
        ),
        "registered_candidate_statuses_unchanged": registered_statuses_unchanged,
        "execution_gate_pass": execution_gate_pass,
        "panel_status_fallback_used": not live_panel_pass and stored_registration_pass,
        "capture_provenance": panel_capture_provenance,
    }
    panel_summary_path = EVIDENCE / f"r307_pre_execution_panel_get_{ATTEMPT}.json"
    write_once(panel_summary_path, (json.dumps(panel_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    if not execution_gate_pass:
        raise RuntimeError("R307 panel GET and stored registration proof failed; see captured evidence")

    feasibility_manifest = json.loads(FEASIBILITY_MANIFEST.read_text(encoding="utf-8"))
    frozen_input_records = [feasibility_manifest["membership_file"], *feasibility_manifest["input_partitions"]]
    observed_input_records = [file_record(ROOT / item["path"]) for item in frozen_input_records]
    input_mismatches = [
        {"expected": expected_record, "observed": observed_record}
        for expected_record, observed_record in zip(frozen_input_records, observed_input_records, strict=True)
        if expected_record != observed_record
    ]
    input_preflight = {
        "round": "R307",
        "classification": "preregistered_input_integrity_preflight_before_backtest_or_forward_return_read",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "preregistration_sha256": prereg_sha,
        "feasibility_manifest_sha256": feasibility_manifest_sha,
        "expected_file_count": len(frozen_input_records),
        "mismatch_count": len(input_mismatches),
        "mismatches": input_mismatches,
        "passed": not input_mismatches,
    }
    input_preflight_sha = write_once(
        INPUT_PREFLIGHT,
        (json.dumps(input_preflight, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    if input_mismatches:
        raise RuntimeError("R307 frozen inputs changed after feasibility; stopped before backtest and forward-return analysis")

    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import (
        BacktestResultPolicy,
        StrategyBacktestConfig,
        StrategyBacktestService,
    )
    from app.config import settings
    from app.strategy.engine import StrategyEngine
    from app.tickflow.repository import DataStore, KlineRepository

    membership_raw = pl.read_parquet(MEMBERSHIP, columns=["query_date", "update_date", "symbol"])
    if membership_raw.filter(pl.col("update_date") > pl.col("query_date")).height:
        raise RuntimeError("R78 update_date exceeds query_date; fail closed")
    membership = membership_raw.select(["query_date", "symbol"]).unique(subset=["query_date", "symbol"])
    calendar = sorted(membership["query_date"].unique().to_list())
    start, end = date(2021, 1, 4), date(2026, 9, 30)
    anchor = next(i for i, day in enumerate(calendar) if day >= start)
    signal_dates = [day for i, day in enumerate(calendar) if i >= anchor and (i-anchor) % 5 == 0 and start <= day <= end]
    symbols = sorted(set(membership["symbol"].to_list()))
    trading_calendar = tuple(map(str, calendar))
    sampled_membership = (
        membership.filter(pl.col("query_date").is_in(signal_dates))
        .sort(["query_date", "symbol"])
        .group_by("query_date", maintain_order=True)
        .agg(pl.col("symbol"))
    )
    membership_by_date = tuple(
        (str(row["query_date"]), tuple(row["symbol"]))
        for row in sampled_membership.iter_rows(named=True)
    )
    load_start, load_end = start - timedelta(days=120), end + timedelta(days=20)
    partition_paths = sorted(path for path in ENRICHED.glob("date=*/part.parquet") if load_start <= date.fromisoformat(path.parent.name[5:]) <= load_end)
    if not partition_paths:
        raise RuntimeError("No enriched partitions cover R307 execution window")
    input_files = [MEMBERSHIP, *partition_paths]
    specs = [
        ("r307_gap_retention_top", "R307 top retention quintile", "top"),
        ("r307_gap_retention_bottom", "R307 bottom retention control", "bottom"),
    ]
    archived_strategy_files = []
    for strategy_id, name, side in specs:
        strategy_path = EVIDENCE / f"{strategy_id}_{ATTEMPT}_runtime.py"
        if strategy_path.exists():
            raise RuntimeError(f"Refusing to overwrite existing strategy artifact: {strategy_path}")
        source = _strategy_source(
            strategy_id, name, side, trading_calendar, membership_by_date
        )
        compile(source, f"<{strategy_id} generated source>", "exec")
        if "functools" in source or "app.config" in source:
            raise RuntimeError(f"R307 generated strategy imports a module outside the system safety allowlist: {strategy_id}")
        from app.strategy.ai_generator import AIStrategyGenerator

        AIStrategyGenerator._validate_safety(source)
        strategy_path.write_bytes(source.encode("utf-8"))
        with strategy_path.with_name(strategy_path.name + ".sha256").open("x", encoding="ascii") as stream:
            stream.write(f"{sha_file(strategy_path)}  {strategy_path.name}\n")
        archived_strategy_files.append(strategy_path)
    execution_code = [*EXECUTION_CODE, *RESEARCH_CODE, *archived_strategy_files]
    for path in RESEARCH_CODE:
        expected_sha = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if sha_file(path) != expected_sha:
            raise RuntimeError(f"R307 research script checksum mismatch: {path.name}")
    execution_code_records = [file_record(path) for path in execution_code]
    execution_script = file_record(Path(__file__))
    input_files.extend(archived_strategy_files)
    manifest = {
        "round": "R307",
        "attempt": ATTEMPT,
        "prior_attempt_failure": {
            "path": PRIOR_ATTEMPT_FAILURE.relative_to(ROOT).as_posix(),
            "sha256": sha_file(PRIOR_ATTEMPT_FAILURE),
        },
        "classification": "frozen_system_backtest_input_manifest",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "preregistration_sha256": prereg_sha,
        "feasibility_sha256": sha_file(FEASIBILITY),
        "feasibility_input_manifest_sha256": feasibility_manifest_sha,
        "pre_execution_input_preflight": {
            "path": INPUT_PREFLIGHT.relative_to(ROOT).as_posix(),
            "sha256": input_preflight_sha,
            "file_count": len(frozen_input_records),
            "mismatch_count": 0,
        },
        "pre_execution_panel_get_sha256": panel_get_sha,
        "panel_registration_readback_sha256": sha_file(registration_path),
        "panel_registration_readback_raw_sha256": sha_file(registration_raw_path),
        "execution_script": execution_script,
        "execution_code": execution_code_records,
        "membership": file_record(MEMBERSHIP),
        "date_partitions": [file_record(path) for path in partition_paths],
        "input_file_count": len(input_files),
        "load_range": [load_start.isoformat(), load_end.isoformat()],
        "sample_date_count": len(signal_dates),
        "sample_dates_sha256": digest("\n".join(map(str, signal_dates)).encode("ascii")),
        "symbol_union_count": len(symbols),
        "symbols_sha256": digest("\n".join(symbols).encode("ascii")),
        "price_columns": ["open", "close"],
        "system": "BacktestEngine + StrategyBacktestService + StrategyEngine + standard matcher; exact R78 filter_history; hs300=False",
        "execution": {"entry_fill": "open_t+1", "exit_fill": "open_t+1", "max_hold_trigger_bars": 5, "realized_open_to_open_holding_sessions": 5, "mode": "all_independent_candidates_with_system_matcher", "max_parallel_positions": 500, "portfolio_position_cap_applicable": False},
        "cost": {"commission_each_side": 0.0005, "stamp_tax_sell": 0.001, "slippage_bps_each_side": 5, "round_trip": ROUND_TRIP_COST},
    }
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    manifest_sha = write_once(MANIFEST, manifest_raw)

    repo = KlineRepository(DataStore(settings.data_dir))
    backtest_engine = BacktestEngine(repo)
    system_runs = {}
    with tempfile.TemporaryDirectory(prefix="r307_strategy_") as temp:
        strategy_dir = Path(temp)
        for strategy_id, _, _ in specs:
            source_path = EVIDENCE / f"{strategy_id}_{ATTEMPT}_runtime.py"
            (strategy_dir / f"{strategy_id}.py").write_bytes(source_path.read_bytes())
        strategy_engine = StrategyEngine(strategy_dirs=[strategy_dir])
        if strategy_engine.load_errors():
            raise RuntimeError(f"R307 strategy load failed: {strategy_engine.load_errors()}")
        service = StrategyBacktestService(backtest_engine, strategy_engine)
        for strategy_id, _, _ in specs:
            config = StrategyBacktestConfig(
                strategy_id=strategy_id, symbols=symbols, start=start, end=end,
                matching="open_t+1", entry_fill="open_t+1", exit_fill="open_t+1",
                fees_pct=0.0005, commission_pct=0.0005, stamp_tax_pct=0.001,
                slippage_bps=5.0, max_positions=500, max_exposure_pct=1.0,
                initial_capital=1_000_000_000.0, position_sizing="equal",
                mode="full", holding_days=5, asset_type="stock", hs300=False,
            )
            try:
                result = service.run(
                    config,
                    result_policy=BacktestResultPolicy(
                        include_monte_carlo=False, include_curves=False, include_trades=True,
                        include_per_symbol_stats=False, include_return_distribution=False,
                        include_benchmark=False, include_strategy_info=False,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - Record a failed system run without losing the research result.
                system_runs[strategy_id] = {
                    "error": f"{type(exc).__name__}: {exc}",
                    "stats": {},
                    "config": StrategyBacktestService._config_to_dict(config),
                    "trades": [],
                }
                continue
            system_runs[strategy_id] = {
                "error": result.error,
                "stats": result.stats,
                "config": result.config,
                "trades": [
                    trade if isinstance(trade, dict)
                    else StrategyBacktestService._trade_to_dict(trade)
                    for trade in result.trades
                ],
            }

    date_idx = {day: i for i, day in enumerate(calendar)}
    audits = {}
    for strategy_id, run in system_runs.items():
        violations = []
        for trade in run["trades"]:
            signal_date = trade.get("entry_signal_date")
            signal_day = date.fromisoformat(str(signal_date)[:10]) if signal_date else None
            if signal_day is None or signal_day not in date_idx:
                violations.append({"trade": trade, "reason": "missing R78 signal date"})
                continue
            i = date_idx[signal_day]
            expected_entry = str(calendar[i+1]) if i+1 < len(calendar) else None
            expected_exit = str(calendar[i+6]) if i+6 < len(calendar) else None
            if str(trade.get("entry_date"))[:10] != expected_entry or str(trade.get("exit_date"))[:10] != expected_exit or trade.get("duration") != 5:
                violations.append({
                    "symbol": trade.get("symbol"), "signal_date": signal_date,
                    "actual_entry": trade.get("entry_date"), "expected_entry": expected_entry,
                    "actual_exit": trade.get("exit_date"), "expected_exit": expected_exit,
                    "duration": trade.get("duration"),
                })
        audits[strategy_id] = {
            "trade_count": len(run["trades"]),
            "fill_alignment_violations": len(violations),
            "examples": violations[:10],
            "all_trades_match_frozen_timing": not violations,
        }
    # The feasibility phase inspected only future keys. Read prices now, after its gates passed.
    panel = backtest_engine.load_panel(symbols, calendar[0], end + timedelta(days=20), columns=["symbol", "date", "open", "close"], asset_type="stock")
    if panel.is_empty():
        raise RuntimeError("R307 research panel is empty")
    index = {day: i for i, day in enumerate(calendar)}
    members = membership.filter(pl.col("query_date").is_in(signal_dates)).select("query_date", "symbol")
    bars = panel.select(["symbol", "date", "open", "close"]).unique(subset=["symbol", "date"], keep="last")
    events = members.join(
        bars.rename({"date": "query_date", "open": "_open", "close": "_close"}),
        on=["query_date", "symbol"], how="inner",
    ).with_columns(
        pl.col("query_date").map_elements(lambda day: calendar[index[day]-1] if index[day] else None, return_dtype=pl.Date).alias("_previous_date")
    )
    previous = bars.select("symbol", pl.col("date").alias("_previous_date"), pl.col("close").alias("_previous_close"))
    events = events.join(previous, on=["symbol", "_previous_date"], how="inner").filter(
        (pl.col("_open") > 0) & (pl.col("_close") > 0) & (pl.col("_previous_close") > 0)
    ).with_columns(
        (pl.col("_open") / pl.col("_previous_close") - 1.0).alias("gap_return"),
        ((pl.col("_close") - pl.col("_previous_close")) / (pl.col("_open") - pl.col("_previous_close"))).clip(-1.0, 2.0).alias("gap_retention"),
    ).filter(pl.col("gap_return") >= 0.005)
    future = pl.DataFrame(
        [{"query_date": day, "entry_date": calendar[index[day]+1], "exit_date": calendar[index[day]+6]} for day in signal_dates if index[day]+6 < len(calendar)],
        schema={"query_date": pl.Date, "entry_date": pl.Date, "exit_date": pl.Date},
    )
    holding_schedule = pl.DataFrame(
        {
            "query_date": [
                day for day in signal_dates if index[day]+6 < len(calendar) for _ in range(6)
            ],
            "holding_date": [
                calendar[index[day]+offset]
                for day in signal_dates if index[day]+6 < len(calendar)
                for offset in range(1, 7)
            ],
        },
        schema={"query_date": pl.Date, "holding_date": pl.Date},
    )
    bar_keys = bars.select("symbol", pl.col("date").alias("holding_date")).unique()
    complete = events.select("query_date", "symbol").unique().join(
        holding_schedule, on="query_date", how="inner"
    ).join(bar_keys, on=["symbol", "holding_date"], how="inner").group_by(
        ["query_date", "symbol"]
    ).agg(pl.len().alias("_bars")).filter(pl.col("_bars") == 6).select(
        "query_date", "symbol"
    )
    events = events.join(complete, on=["query_date", "symbol"], how="inner")
    entry = bars.select("symbol", pl.col("date").alias("entry_date"), pl.col("open").alias("entry_open"))
    exit_ = bars.select("symbol", pl.col("date").alias("exit_date"), pl.col("open").alias("exit_open"))
    events = events.join(future, on="query_date", how="inner").join(
        entry, on=["symbol", "entry_date"], how="left"
    ).join(exit_, on=["symbol", "exit_date"], how="left").with_columns(
        pl.when(
            pl.col("entry_open").is_not_null() & (pl.col("entry_open") > 0)
            & pl.col("exit_open").is_not_null() & (pl.col("exit_open") > 0)
        ).then(pl.col("exit_open") / pl.col("entry_open") - 1.0)
        .otherwise(None).alias("forward_return")
    )

    ranked = events.sort(["query_date", "gap_return", "symbol"]).with_columns(
        pl.col("gap_return").rank(method="ordinal").over("query_date").alias("_gap_rank"),
        pl.len().over("query_date").alias("_n"),
    ).sort(["query_date", "gap_return", "symbol"]).with_columns(
        (((pl.col("_gap_rank") - 1) * 5 / pl.col("_n")).floor() + 1)
        .cast(pl.UInt8).clip(upper_bound=5).alias("_gap_bin")
    ).sort(["query_date", "gap_retention", "symbol"]).with_columns(
        pl.col("gap_retention").rank(method="ordinal").over("query_date").alias("_factor_ordinal")
    )
    bucket = (pl.col("_n") * 0.2).ceil().cast(pl.UInt32).clip(lower_bound=1)
    ranked = ranked.with_columns(
        pl.when(pl.col("_factor_ordinal") > pl.col("_n")-bucket).then(pl.lit("top"))
        .when(pl.col("_factor_ordinal") <= bucket).then(pl.lit("bottom"))
        .otherwise(pl.lit("middle")).alias("_bucket")
    )
    valid = ranked.filter(pl.col("forward_return").is_not_null() & pl.col("forward_return").is_finite()).with_columns(
        pl.col("gap_retention").rank(method="average").over("query_date").alias("_factor_rank"),
        pl.col("forward_return").rank(method="average").over("query_date").alias("_return_rank"),
    ).with_columns(
        (pl.col("_factor_rank") - pl.col("_factor_rank").mean().over(["query_date", "_gap_bin"])).alias("_factor_resid"),
        (pl.col("_return_rank") - pl.col("_return_rank").mean().over(["query_date", "_gap_bin"])).alias("_return_resid"),
    )
    date_counts = ranked.group_by("query_date").agg(
        pl.len().alias("n_eligible_events"),
        pl.col("forward_return").is_not_null().sum().alias("n_valid_forward_returns"),
    )
    ic_by_date = valid.group_by("query_date").agg(
        pl.corr("_factor_rank", "_return_rank").alias("ic"),
        pl.corr("_factor_resid", "_return_resid").alias("gap_bin_residual_ic"),
    )
    portfolio_by_date = valid.group_by("query_date").agg(
        pl.col("forward_return").filter(pl.col("_bucket") == "top").mean().alias("top_gross"),
        pl.col("forward_return").filter(pl.col("_bucket") == "bottom").mean().alias("bottom_gross"),
    )
    per_date = date_counts.join(ic_by_date, on="query_date", how="left").join(
        portfolio_by_date, on="query_date", how="left"
    ).with_columns(
        (pl.col("top_gross") - ROUND_TRIP_COST).alias("top_net"),
        (pl.col("bottom_gross") - ROUND_TRIP_COST).alias("bottom_net"),
        (pl.col("top_gross") - pl.col("bottom_gross")).alias("gross_spread"),
    ).with_columns(
        (pl.col("gross_spread") - 2 * ROUND_TRIP_COST).alias("net_spread"),
    ).sort("query_date")

    def json_metric(value):
        if value is None:
            return None
        if isinstance(value, date):
            return str(value)
        if isinstance(value, float):
            if not math.isfinite(value):
                return None
            return round(value, 8)
        return int(value)

    daily_by_date = {
        row["query_date"]: {key: json_metric(value) for key, value in row.items()}
        for row in per_date.iter_rows(named=True)
    }
    daily = []
    for day in signal_dates:
        if split_for(day, prereg) is None:
            continue
        daily.append(daily_by_date.get(day, {
            "query_date": str(day),
            "n_eligible_events": 0,
            "n_valid_forward_returns": 0,
            "ic": None,
            "gap_bin_residual_ic": None,
            "top_gross": None,
            "bottom_gross": None,
            "top_net": 0.0,
            "bottom_net": 0.0,
            "gross_spread": None,
            "net_spread": None,
        }))
    summaries = {}
    for split in prereg["design"]["splits"]:
        rows = [row for row in daily if split_for(date.fromisoformat(row["query_date"]), prereg) == split]
        split_sample_count = sum(split_for(day, prereg) == split for day in signal_dates)
        ics = [row["ic"] for row in rows if row["ic"] is not None]
        residual = [row["gap_bin_residual_ic"] for row in rows if row["gap_bin_residual_ic"] is not None]
        net = [row["top_net"] for row in rows if row["top_net"] is not None]
        gross_spreads = [row["gross_spread"] for row in rows if row["gross_spread"] is not None]
        net_spreads = [row["net_spread"] for row in rows if row["net_spread"] is not None]
        summaries[split] = {
            "sampled_dates": split_sample_count,
            "event_dates": len(rows),
            "eligible_events": sum(row["n_eligible_events"] or 0 for row in rows),
            "valid_forward_return_events": sum(row["n_valid_forward_returns"] or 0 for row in rows),
            "ic_dates": len(ics),
            "mean_ic": round(stats.fmean(ics), 8) if ics else None,
            "positive_ic_date_rate": round(sum(value > 0 for value in ics)/len(ics), 8) if ics else None,
            "gap_bin_residual_ic_mean": round(stats.fmean(residual), 8) if residual else None,
            "mean_top_portfolio_net_return": round(stats.fmean(net), 8) if net else None,
            "valid_top_portfolio_date_rate": round(sum(row["top_gross"] is not None for row in rows)/len(rows), 8) if rows else None,
            "zero_candidate_portfolio_dates": sum(row["n_eligible_events"] == 0 for row in rows),
            "mean_top_minus_bottom_spread_gross": round(stats.fmean(gross_spreads), 8) if gross_spreads else None,
            "mean_top_minus_bottom_spread_after_cost": round(stats.fmean(net_spreads), 8) if net_spreads else None,
            "positive_spread_date_rate_after_cost": round(sum(value > 0 for value in net_spreads)/len(net_spreads), 8) if net_spreads else None,
        }
    gates = {
        "G1_discovery": bool(summaries["discovery"]["mean_ic"] is not None and summaries["discovery"]["mean_ic"] >= 0.025 and summaries["discovery"]["positive_ic_date_rate"] >= 0.55),
        "G2_validation": bool(summaries["validation"]["mean_ic"] is not None and summaries["validation"]["mean_ic"] >= 0.015 and summaries["validation"]["positive_ic_date_rate"] >= 0.52),
        "G3_holdout": bool(summaries["holdout"]["mean_ic"] is not None and summaries["holdout"]["mean_ic"] >= 0.010 and summaries["holdout"]["positive_ic_date_rate"] >= 0.52),
        "G4_gap_size_increment": bool(summaries["validation"]["gap_bin_residual_ic_mean"] is not None and summaries["holdout"]["gap_bin_residual_ic_mean"] is not None and summaries["validation"]["gap_bin_residual_ic_mean"] >= 0.010 and summaries["holdout"]["gap_bin_residual_ic_mean"] >= 0.010),
        "G5_net_spread": bool(summaries["validation"]["mean_top_minus_bottom_spread_after_cost"] is not None and summaries["holdout"]["mean_top_minus_bottom_spread_after_cost"] is not None and summaries["validation"]["mean_top_minus_bottom_spread_after_cost"] > 0 and summaries["holdout"]["mean_top_minus_bottom_spread_after_cost"] > 0 and summaries["validation"]["positive_spread_date_rate_after_cost"] >= 0.52 and summaries["holdout"]["positive_spread_date_rate_after_cost"] >= 0.52),
    }
    gates["all_pass"] = all(gates.values())

    trades = {
        "round": "R307",
        "attempt": ATTEMPT,
        "system_runs": {},
    }
    candidate_signal_audits = {}
    for strategy_id, _, side in specs:
        selected = ranked.filter(pl.col("_bucket") == side)
        expected = {(str(row["query_date"]), row["symbol"]) for row in selected.select("query_date", "symbol").iter_rows(named=True)}
        runtime_filter = strategy_engine.get(strategy_id).filter_history_fn
        if runtime_filter is None:
            raise RuntimeError(f"R307 strategy filter_history is missing: {strategy_id}")
        runtime_hits = runtime_filter(panel, {})
        actual = {(str(row["date"]), row["symbol"]) for row in runtime_hits.select("date", "symbol").iter_rows(named=True)}
        run = system_runs[strategy_id]
        selection_count = int(run["stats"].get("selection", {}).get("entry_candidates", -1))
        candidate_signal_audits[strategy_id] = {
            "expected_signal_count": len(expected),
            "runtime_signal_count": len(actual),
            "system_entry_candidate_count": selection_count,
            "expected_minus_runtime": len(expected - actual),
            "runtime_minus_expected": len(actual - expected),
            "system_backtest_error": run.get("error"),
            "all_sets_equal": (
                run.get("error") is None
                and expected == actual
                and selection_count == len(expected)
            ),
        }
        trades_for_strategy = run["trades"]
        trade_signals = [(str(trade.get("entry_signal_date"))[:10], trade.get("symbol")) for trade in trades_for_strategy]
        trade_keys = set(trade_signals)
        candidate_signal_audits[strategy_id]["system_trade_signal_count"] = len(trades_for_strategy)
        candidate_signal_audits[strategy_id]["unexpected_trade_signals"] = len(trade_keys - expected)
        candidate_signal_audits[strategy_id]["expected_signals_without_system_trade"] = len(expected - trade_keys)
        candidate_signal_audits[strategy_id]["duplicate_trade_signals"] = len(trade_signals) - len(trade_keys)
        candidate_signal_audits[strategy_id]["system_trade_signals_are_unique_and_expected"] = (
            not (trade_keys - expected) and len(trade_signals) == len(trade_keys)
        )

    for strategy_id, run in system_runs.items():
        violations = []
        date_idx = {day: i for i, day in enumerate(calendar)}
        for trade in run["trades"]:
            signal_date = date.fromisoformat(str(trade["entry_signal_date"])[:10])
            i = date_idx.get(signal_date)
            if i is None or str(trade["entry_date"])[:10] != str(calendar[i+1]) or str(trade["exit_date"])[:10] != str(calendar[i+6]) or trade["duration"] != 5:
                violations.append({key: trade.get(key) for key in ("symbol", "entry_signal_date", "entry_date", "exit_date", "duration", "exit_reason")})
        run["fill_audit"] = {"trade_count": len(run["trades"]), "violations": len(violations), "examples": violations[:10]}
        trades["system_runs"][strategy_id] = run.pop("trades")
    candidate_parity_pass = all(
        item["all_sets_equal"]
        for item in candidate_signal_audits.values()
    )
    system_trade_signal_integrity_pass = all(
        item["system_trade_signals_are_unique_and_expected"]
        for item in candidate_signal_audits.values()
    )
    fill_timing_pass = all(
        item["fill_audit"]["violations"] == 0 for item in system_runs.values()
    )
    system_backtest_errors = {
        strategy_id: run["error"]
        for strategy_id, run in system_runs.items()
        if run.get("error") is not None
    }
    trades_sha = write_once(TRADES_OUT, (json.dumps(trades, ensure_ascii=False, indent=2, sort_keys=True)+"\n").encode("utf-8"))

    frozen_records = {item["path"]: item for item in manifest["date_partitions"]}
    frozen_records[manifest["membership"]["path"]] = manifest["membership"]
    frozen_records.update({item["path"]: item for item in execution_code_records})
    stable_files = [MEMBERSHIP, *partition_paths, *archived_strategy_files]
    input_stable = all(frozen_records.get(item["path"]) == item for item in [file_record(path) for path in stable_files])
    execution_code_stable = all(
        record == file_record(ROOT / record["path"]) for record in execution_code_records
    ) and execution_script == file_record(Path(__file__))
    result = {
        "round": "R307",
        "classification": "preregistered_conditional_alpha_test_using_system_strategy_backtest",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "preregistration_sha256": prereg_sha,
        "feasibility_sha256": sha_file(FEASIBILITY),
        "execution_manifest_sha256": manifest_sha,
        "trade_records_sha256": trades_sha,
        "input_file_count": len(input_files),
        "input_hashes_stable_after_execution": input_stable,
        "execution_code_hashes_stable_after_execution": execution_code_stable,
        "universe": {"source": "exact R78 date/symbol; update_date <= query_date", "symbol_union": len(symbols), "sampled_dates": len(signal_dates), "complete_events": events.height, "hs300_current_filter_used": False},
        "method": {"factor": "clip((close_t - prev_close)/(open_t - prev_close), -1, 2); event open_t/prev_close-1 >= 0.005", "forward": "open_(t+1) to open_(t+6)", "cost": {"round_trip_fraction": ROUND_TRIP_COST, "commission_each_side": 0.0005, "stamp_tax_sell": 0.001, "slippage_bps_each_side": 5}, "system": "BacktestEngine + StrategyBacktestService + StrategyEngine; open_t+1 in/out; 5-session max hold; exact historical member filter in strategy"},
        "system_backtests": {
            strategy_id: {
                "error": run.get("error"),
                "stats": run["stats"],
                "config": run["config"],
                "fill_audit": run["fill_audit"],
            }
            for strategy_id, run in system_runs.items()
        },
        "candidate_signal_audits": candidate_signal_audits,
        "panel_registration_pre_execution": panel_summary,
        "execution_integrity": {
            "system_backtest_errors": system_backtest_errors,
            "candidate_signal_parity_pass": candidate_parity_pass,
            "system_trade_signal_integrity_pass": system_trade_signal_integrity_pass,
            "all_completed_trades_match_open_t1_to_open_t6": fill_timing_pass,
            "timing_violation_count": sum(
                item["fill_audit"]["violations"] for item in system_runs.values()
            ),
            "interpretation": "Engineering integrity checks supplement and do not alter frozen statistical gates.",
        },
        "per_split": summaries,
        "daily_metrics": daily,
        "gates": gates,
        "decision": (
            "REJECT_input_or_execution_code_integrity_failed"
            if not input_stable or not execution_code_stable
            else "REJECT_system_backtest_error"
            if system_backtest_errors
            else "REJECT_system_candidate_signal_parity_failed"
            if not candidate_parity_pass
            else "REJECT_system_trade_signal_integrity_failed"
            if not system_trade_signal_integrity_pass
            else "REJECT_system_fill_timing_mismatch"
            if not fill_timing_pass
            else "all_preregistered_gates_pass_candidate_remains_research_only"
            if gates["all_pass"]
            else "REJECT_frozen_statistical_gates_not_all_passed"
        ),
        "limitations": ["Conditional exploratory direction inspired by R25/R170, not independent confirmation.", "R78 historical membership is an internal snapshot cache; official effective-date archive is not fully closed.", "No prospective or paper trading validation.", "System execution can reject or defer signals because of price limits, suspension, or missing executable prices; those outcomes are reported separately from the frozen open-to-open diagnostic."],
    }
    result_raw = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)+"\n").encode("utf-8")
    result_sha = write_once(RESULT_OUT, result_raw)
    print(json.dumps({"result": RESULT_OUT.as_posix(), "trades": TRADES_OUT.as_posix(), "events": events.height, "split_summaries": summaries, "system": {k: {"stats": v["stats"], "fill_audit": v["fill_audit"]} for k,v in system_runs.items()}, "gates": gates, "input_hashes_stable": input_stable, "result_sha256": result_sha},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
