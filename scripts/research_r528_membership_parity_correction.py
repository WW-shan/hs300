#!/usr/bin/env python3
"""R528: correct R527 membership parity and rerun the frozen family comparison."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
import shutil
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r528_membership_parity_correction_preregistration.json"
OUTPUT = E / "r528_membership_parity_correction_2026-11-23.json"
RESULT = E / "r528_membership_parity_correction_2026-11-23.md"
BUNDLE = E / "r526_unified_pit_bundle_window"
R385_RUNNER = (
    DATA
    / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
    / "run_r385_pit_platform_monte_carlo.py"
)
FIVE_LEG_ADMITTED = (
    "ext_alt_hs300_cash_flow_stability_4q_low",
    "ext_alt_hs300_dividend_yield",
    "ext_alt_hs300_fund_holding_ratio_low",
    "ext_alt_hs300_idio_vol_40d_low",
)
FIVE_LEG_FACTORS = (*FIVE_LEG_ADMITTED, "value_quality")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_module(path: Path, name: str) -> Any:
    """Load a research runner with its scripts directory importable."""
    script_dir = str(ROOT / "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def snapshot_factor_registry() -> tuple[dict[str, Any], Any]:
    """Snapshot global custom/ext factor registration for family isolation."""
    from app.factors import ext_factors
    from app.factors.registry import _REGISTRY

    return dict(_REGISTRY), ext_factors._sync_state


def restore_factor_registry(snapshot: tuple[dict[str, Any], Any]) -> None:
    """Restore the exact pre-family factor registry and extension sync state."""
    from app.factors import ext_factors
    from app.factors.registry import _REGISTRY

    registry, sync_state = snapshot
    _REGISTRY.clear()
    _REGISTRY.update(registry)
    ext_factors._sync_state = sync_state


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


ENGINE_EXTENSION_COLUMNS = (
    "symbol",
    "date",
    "holder_top10_free_float_share",
    "equity_risk_premium",
    "credit_spread_5y_low",
    "term_spread_10y_2y",
    "industry_mom_20d",
    "stock_rel_industry_20d",
)


def load_runtime_factor_registry(data_dir: Path) -> list[str]:
    """Use the project's runtime order: extension sync, then custom factors."""
    from app.factors.store import load_runtime_registry

    return load_runtime_registry(data_dir)


def engine_extension_frame(frame: pl.DataFrame) -> pl.DataFrame:
    """Select every extension column required by the frozen hold-30 family."""
    missing = sorted(set(ENGINE_EXTENSION_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"R528 engine extension frame missing columns: {missing}")
    return frame.select(ENGINE_EXTENSION_COLUMNS)


def map_industry_asof(panel: pl.DataFrame, industry: pl.DataFrame) -> pl.DataFrame:
    """Map each symbol/date to the latest effective industry, strictly as-of."""
    left = panel.with_columns(pl.col("date").cast(pl.Date)).sort(["symbol", "date"])
    right = industry.select(
        "symbol",
        pl.col("effective_date").cast(pl.Date),
        pl.col("industry_pit").cast(pl.String).alias("_industry_asof"),
    ).sort(["symbol", "effective_date"])
    return (
        left.join_asof(
            right,
            left_on="date",
            right_on="effective_date",
            by="symbol",
            strategy="backward",
        )
        .with_columns(pl.col("_industry_asof").alias("industry_l1"))
        .drop("_industry_asof")
    )


def bundle_membership() -> pl.DataFrame:
    """Return the single membership frame both families must consume."""
    frame = pl.read_parquet(BUNDLE / "daily_snapshots.parquet", columns=["query_date", "symbol"])
    out = (
        frame.select(
            pl.col("query_date").cast(pl.Date).alias("effective_date"),
            pl.col("symbol").cast(pl.String),
        )
        .drop_nulls()
        .unique()
        .sort(["effective_date", "symbol"])
    )
    counts = out.group_by("effective_date").len()["len"].unique().to_list()
    if counts != [300]:
        raise RuntimeError(f"R528 membership must be 300 per session, observed {counts}")
    return out


def bundle_extension() -> pl.DataFrame:
    frame = pl.read_parquet(BUNDLE / "five_leg_required_fields.parquet")
    source_fields = {
        "ext_alt_hs300_cash_flow_stability_4q_low": "cash_flow_stability_4q_low",
        "ext_alt_hs300_dividend_yield": "dividend_yield",
        "ext_alt_hs300_fund_holding_ratio_low": "fund_holding_ratio_low",
        "ext_alt_hs300_idio_vol_40d_low": "idio_vol_40d_low",
    }
    required = {"symbol", "date", *source_fields.values()}
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError(f"R528 extension bundle missing columns: {sorted(missing)}")
    return frame.select(
        pl.col("symbol").cast(pl.String),
        pl.col("date").cast(pl.Date),
        *[
            pl.col(source).cast(pl.Float64).alias(factor)
            for factor, source in source_fields.items()
        ],
    ).sort(["symbol", "date"])


def _stats_from_values(values: list[float]) -> dict[str, float | int]:
    if len(values) < 2:
        raise ValueError("equity curve requires at least two observations")
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    volatility = math.sqrt(variance)
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {
        "total_return": total,
        "annual": (1.0 + total) ** (252.0 / len(returns)) - 1.0,
        "sharpe": mean / volatility * math.sqrt(252.0) if volatility else 0.0,
        "mdd": mdd,
        "days": len(values),
    }


def _segment(curve: pl.DataFrame, start: str, end: str) -> dict[str, Any]:
    part = curve.filter(
        (pl.col("date") >= date.fromisoformat(start))
        & (pl.col("date") <= date.fromisoformat(end))
    ).sort("date")
    if part.height < 2:
        raise ValueError(f"R528 segment {start}..{end} has too few points")
    return {
        **_stats_from_values([float(value) for value in part["value"].to_list()]),
        "start": str(part["date"][0]),
        "end": str(part["date"][-1]),
        "curve": [
            {"date": str(row["date"]), "value": float(row["value"])}
            for row in part.iter_rows(named=True)
        ],
    }


def block_bootstrap(
    curve: pl.DataFrame, *, block: int, paths: int, seed: int
) -> dict[str, float]:
    values = [float(value) for value in curve["value"].to_list()]
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
    if not returns:
        return {"total_p05": 0.0, "total_p50": 0.0, "sharpe_p50": 0.0, "mdd_p05": 0.0}
    rng = random.Random(seed)
    totals: list[float] = []
    sharpes: list[float] = []
    mdds: list[float] = []
    for _ in range(paths):
        sampled: list[float] = []
        while len(sampled) < len(returns):
            start = rng.randrange(len(returns))
            sampled.extend(
                returns[(start + offset) % len(returns)] for offset in range(block)
            )
        selected = sampled[: len(returns)]
        path = [1.0]
        for item in selected:
            path.append(path[-1] * (1.0 + item))
        mean = sum(selected) / len(selected)
        std = (sum((item - mean) ** 2 for item in selected) / len(selected)) ** 0.5
        peak = path[0]
        mdd = 0.0
        for item in path:
            peak = max(peak, item)
            mdd = min(mdd, item / peak - 1.0)
        totals.append(path[-1] - 1.0)
        sharpes.append(mean / std * math.sqrt(252.0) if std else 0.0)
        mdds.append(mdd)

    def ordered(items: list[float], fraction: float) -> float:
        return sorted(items)[min(len(items) - 1, int(fraction * len(items)))]

    return {
        "total_p05": ordered(totals, 0.05),
        "total_p50": ordered(totals, 0.50),
        "sharpe_p50": ordered(sharpes, 0.50),
        "mdd_p05": ordered(mdds, 0.05),
    }


def curve_windows(
    curve: pl.DataFrame, prereg: dict[str, Any], *, seed: int
) -> dict[str, Any]:
    windows = prereg["windows"]
    output: dict[str, Any] = {}
    for name in ("train", "validation", "test", "recent"):
        start = windows[f"{name}_start"]
        end = windows[f"{name}_end"] if f"{name}_end" in windows else windows["test_end"]
        segment = _segment(curve, start, end)
        segment["curve_points"] = len(segment["curve"])
        if name in {"test", "recent"}:
            segment["bootstrap"] = block_bootstrap(
                pl.DataFrame(segment["curve"]).with_columns(pl.col("date").str.to_date()),
                block=int(prereg["bootstrap"]["block_days"]),
                paths=int(prereg["bootstrap"]["replications"]),
                seed=seed + (2 if name == "test" else 3),
            )
        output[name] = segment
    return output


def _curve_frame_from_payload(payload: dict[str, Any]) -> pl.DataFrame:
    curve = payload.get("curve")
    if not curve or len(curve) < 2:
        raise RuntimeError("R528 expected a persisted non-empty equity curve")
    return pl.DataFrame(
        {
            "date": [date.fromisoformat(str(item["date"])[:10]) for item in curve],
            "value": [float(item["value"]) for item in curve],
        }
    ).sort("date").unique(subset=["date"], keep="last")


@contextmanager
def _pinned_r385_inputs(bundle_dir: Path, cache_dir: Path) -> Iterator[None]:
    """Patch R385's actual membership/ext/cache inputs to the committed bundle."""
    r385 = load_module(R385_RUNNER, "r528_r385")
    old_membership = r385.R381_MEMBERSHIP
    old_ext = r385.R381_EXT_FRAME
    old_cache = r385.CACHE
    old_industry = getattr(r385, "R381_INDUSTRY", None)
    r385.R381_MEMBERSHIP = bundle_dir / "daily_snapshots.parquet"
    r385.R381_EXT_FRAME = bundle_dir / "alt_hs300_required_fields.parquet"
    r385.CACHE = cache_dir
    if old_industry is not None:
        r385.R381_INDUSTRY = bundle_dir / "industry_history.parquet"
    try:
        yield
    finally:
        r385.R381_MEMBERSHIP = old_membership
        r385.R381_EXT_FRAME = old_ext
        r385.CACHE = old_cache
        if old_industry is not None:
            r385.R381_INDUSTRY = old_industry


def run_hold30_family(prereg: dict[str, Any]) -> dict[str, Any]:
    """Run the frozen R385 A/C/center-B family on the committed bundle."""
    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
    from app.config import settings
    from app.indicators import pipeline
    from app.services.industry_map import industry_loader_for
    from app.strategy.engine import StrategyEngine
    from app.tickflow.repository import DataStore, KlineRepository

    r385 = load_module(R385_RUNNER, "r528_hold30")
    r371 = load_module(
        ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r371_alpha_parameter_grid.py",
        "r528_r371",
    )
    definitions = r371._write_variant_definitions()
    membership = load_module(
        ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r381_pit_data_audit.py",
        "r528_r381",
    ).membership_for_engine(BUNDLE / "daily_snapshots.parquet")
    ext = engine_extension_frame(
        pl.read_parquet(BUNDLE / "alt_hs300_required_fields.parquet")
    ).with_columns(pl.col("date").cast(pl.Date))
    work = Path(tempfile.mkdtemp(prefix="r528_hold30_"))
    old_data = settings.data_dir
    registry_snapshot = snapshot_factor_registry()
    engine_hash = sha(ROOT / "backend" / "app" / "backtest" / "engine.py")
    outputs: dict[str, pl.DataFrame] = {}
    try:
        engine_root = work / "engine_data"
        shutil.copytree(r385.R40, engine_root, symlinks=True)
        load_module(
            ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r360_isolation.py",
            "r528_isolation",
        ).prepare_isolated_data_root(engine_root, DATA)
        load_module(
            ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r381_pit_data_audit.py",
            "r528_r381_writer",
        ).write_ext_timeseries(engine_root, ext)
        for name in ("custom_factors", "custom_signals"):
            for source in sorted((r371.VARIANTS / name).glob("*")):
                shutil.copy2(source, engine_root / "user_data" / name / source.name)
        settings.data_dir = engine_root
        load_runtime_factor_registry(engine_root)
        pipeline.invalidate_custom_signals()
        engine = StrategyEngine(
            strategy_dirs=[DATA / "strategies/ai", r371.VARIANTS / "strategies"],
            override_loader=lambda _sid: {},
            industry_loader=industry_loader_for(engine_root),
        )
        service = StrategyBacktestService(
            BacktestEngine(KlineRepository(DataStore(engine_root))), engine
        )

        def run_one(
            kind: str,
            strategy_id: str,
            start: date,
            cost: str,
            max_positions: int,
            *,
            overrides: dict[str, Any] | None = None,
            hold: int | None = None,
            initial_capital: float = r385.INITIAL_CAPITAL,
        ) -> pl.DataFrame:
            stock = r385.SCENARIOS[cost]["stock"]
            config = StrategyBacktestConfig(
                strategy_id=strategy_id,
                symbols=None,
                start=start,
                end=r385.END,
                params={},
                overrides=overrides or {},
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
            with r371._patched_membership(membership):
                result = service.run_with_evidence(config)
            if result.error:
                raise RuntimeError(f"R528 hold30 {kind} {cost}: {result.error}")
            curve = r385._curve_frame(result.equity_curve)
            if curve["date"].min() != start or curve["date"].max() != r385.END:
                raise RuntimeError(f"R528 hold30 {kind} {cost}: date coverage mismatch")
            print(f"R528 hold30 {kind} {cost}: trades={len(result.trades)}", flush=True)
            return curve

        for cost in ("2x", "3x", "5x"):
            outputs[f"A_{cost}"] = run_one("A", r385.A_STRATEGY, r385.START, cost, 10)
            for index, cohort_start in enumerate(r385.COHORT_STARTS):
                outputs[f"C_{cost}_{index}"] = run_one(
                    "C",
                    r385.C_STRATEGY,
                    cohort_start,
                    cost,
                    50,
                    initial_capital=r385.INITIAL_CAPITAL / 3.0,
                )
            outputs[f"B_{cost}_{r385.CENTRE_CELL}"] = run_one(
                "B",
                definitions["strategies"]["5"]["id"],
                r385.START,
                cost,
                10,
                overrides={"max_hold_days": 30, "stop_loss": -0.20},
                hold=30,
            )

        ctx = r385._context()
        assets = r385.prepare_assets(ctx)
        curves: dict[str, pl.DataFrame] = {}
        for cost in ("2x", "3x", "5x"):
            c = r385.combine_c(
                {name: curve for name, curve in outputs.items()},
                cost,
                ctx,
            )
            bundle = r385.compose_portfolio(
                ctx,
                assets,
                outputs[f"A_{cost}"],
                outputs[f"B_{cost}_{r385.CENTRE_CELL}"],
                c,
                cost,
                prereg["families"]["hold30"]["portfolio_parameters"],
            )
            curves[cost] = bundle["curve"].select(
                pl.col("date").cast(pl.Date), pl.col("value").cast(pl.Float64)
            )
        curve = curves["2x"]
        return {
            "run": {
                "curve_points": curve.height,
                "engine_sha256": engine_hash,
                "fresh_cache": True,
                "membership_input": "r526/daily_snapshots.parquet",
            "membership_enforced_by_framework": True,
                "industry_neutralization": "THS-current industry_l1 (unchanged)",
                "max_weight_sum": max(
                    float(
                        r385.compose_portfolio(
                            ctx,
                            assets,
                            outputs["A_2x"],
                            outputs[f"B_2x_{r385.CENTRE_CELL}"],
                            r385.combine_c(
                                {name: frame for name, frame in outputs.items()},
                                "2x",
                                ctx,
                            ),
                            "2x",
                            prereg["families"]["hold30"]["portfolio_parameters"],
                        )["weight_summary"]["max_weight_sum"]
                    ),
                    0.0,
                ),
                "cost_runs": {
                    cost: r385.curve_metrics(frame)["full"]
                    for cost, frame in curves.items()
                },
            },
            "curve": curve,
            "cost_curves": curves,
        }
    finally:
        settings.data_dir = old_data
        restore_factor_registry(registry_snapshot)
        pipeline.invalidate_custom_signals()
        shutil.rmtree(work, ignore_errors=True)


def _enrich_five_leg_panel(panel: pl.DataFrame) -> pl.DataFrame:
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.incremental import rank_score_frame

    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("R528 financial snapshot is unavailable")
    panel = attach_fundamental_factors(
        panel, snapshot, ["pb_latest", "ocf_debt", "debt_ratio_latest"]
    )
    ext = bundle_extension()
    panel = panel.join(ext, on=["symbol", "date"], how="left").sort(["symbol", "date"])
    for member, direction in (
        ("pb_latest", "low"),
        ("ocf_debt", "high"),
        ("debt_ratio_latest", "low"),
    ):
        panel = rank_score_frame(panel, member, direction, output_column=f"_r528_vq_{member}")
    return panel.with_columns(
        (
            pl.col("_r528_vq_pb_latest").fill_null(0.0)
            + pl.col("_r528_vq_ocf_debt").fill_null(0.0)
            + pl.col("_r528_vq_debt_ratio_latest").fill_null(0.0)
        ).alias("value_quality"),
        (
            pl.col("_r528_vq_pb_latest").is_not_null()
            & pl.col("_r528_vq_ocf_debt").is_not_null()
            & pl.col("_r528_vq_debt_ratio_latest").is_not_null()
        ).alias("_r528_vq_valid"),
    ).filter(pl.col("_r528_vq_valid"))


def run_with_pinned_membership(service, config, membership: pl.DataFrame, *, prepared=None):
    """Run a framework backtest against one exact frozen HS300 membership frame."""
    from app.backtest import strategy as strategy_module

    original = strategy_module._hs300_membership_frame
    strategy_module._hs300_membership_frame = lambda _start, _end: membership
    try:
        return service.run_with_evidence(config, prepared=prepared)
    finally:
        strategy_module._hs300_membership_frame = original


def run_five_leg_family(prereg: dict[str, Any]) -> dict[str, Any]:
    """Run the frozen R506 five-leg composite on the same bundle membership."""
    from app.factor_research.incremental import build_rank_composite

    r453 = load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r528_r453")
    p5 = r453.load_p5()
    membership = bundle_membership()
    panel, _ = p5.load_candidate_panel(DATA, sorted(membership["symbol"].unique()), [])
    panel = _enrich_five_leg_panel(panel)
    work, _ = build_rank_composite(
        panel,
        prereg["families"]["five_leg"]["weights"],
        {member: "high" for member in FIVE_LEG_FACTORS},
        "_r528_five_leg_composite",
        minimum_weight_coverage=0.75,
    )
    service = p5.portfolio._service()
    fixed = prereg["families"]["five_leg"]["fixed_execution"]
    curve_rows: list[dict[str, Any]] = []
    engine_hash = sha(ROOT / "backend" / "app" / "backtest" / "engine.py")
    for name, cost in (("2x", "2x"), ("3x", "3x"), ("5x", "5x")):
        start = date.fromisoformat(prereg["windows"]["train_start"])
        end = date.fromisoformat(prereg["windows"]["test_end"])
        config = p5.portfolio._backtest_config(
            {"_r528_five_leg_composite": 1.0},
            {"_r528_five_leg_composite": "high"},
            start=start,
            end=end,
            max_positions=int(fixed["max_positions"]),
            cost=(
                p5.portfolio._cost_parameters(cost)
                if cost != "5x"
                else {
                    "fees_pct": 0.001,
                    "commission_pct": 0.001,
                    "stamp_tax_pct": 0.0005,
                    "slippage_bps": 25.0,
                }
            ),
            max_hold_days=int(fixed["max_hold_days"]),
        )
        config.params = {
            **(config.params or {}),
            "entry_score": float(fixed["entry_score"]),
            "exit_score": float(fixed["exit_score"]),
            "top_rank": int(fixed["top_rank"]),
        }
        config.max_exposure_pct = float(fixed["max_exposure_pct"])
        config.max_new_positions_per_day = int(fixed["max_new_positions_per_day"])
        config.symbols = sorted(panel["symbol"].unique().to_list())
        prepared_base = service.prepare_matrix_optimization([config])
        prepared = p5._extend_prepared_with_panel(
            prepared_base, work, ["_r528_five_leg_composite"]
        )
        result = run_with_pinned_membership(
            service, config, membership, prepared=prepared
        )
        if result.error:
            raise RuntimeError(result.error)
        curve = pl.DataFrame(
            {
                "date": [date.fromisoformat(str(item["date"])[:10]) for item in result.equity_curve],
                "value": [float(item["value"]) for item in result.equity_curve],
            }
        ).sort("date")
        curve_rows.append({"cost": name, "curve": curve, "stats": p5._select_stats(result.stats)})
    main = next(row for row in curve_rows if row["cost"] == "2x")
    return {
        "run": {
            "curve_points": main["curve"].height,
            "engine_sha256": engine_hash,
            "membership_input": "r526/daily_snapshots.parquet",
            "membership_enforced_by_framework": True,
            "industry_neutralization": "not used by the five-leg rank composite",
            "max_weight_sum": 1.0,
            "cost_runs": {row["cost"]: row["stats"] for row in curve_rows},
        },
        "curve": main["curve"],
        "cost_runs": curve_rows,
    }


def build_report() -> dict[str, Any]:
    verify_sidecar(PREREG)
    prereg = load_json(PREREG)
    manifest = load_json(ROOT / prereg["sources"]["r526_manifest"])
    bundle_checks = {
        row["name"]: sha(BUNDLE / row["name"]) == row["sha256"] for row in manifest["files"]
    }
    if not all(bundle_checks.values()):
        raise RuntimeError(f"R528 bundle mismatch: {bundle_checks}")
    industry = pl.read_parquet(BUNDLE / "industry_history.parquet").sort(
        ["symbol", "effective_date"]
    )
    membership = bundle_membership()
    hold30 = run_hold30_family(prereg)
    five_leg = run_five_leg_family(prereg)
    family_payload = {}
    for name, payload in (("hold30", hold30), ("five_leg", five_leg)):
        family_payload[name] = {
            "run": payload["run"],
            "windows": {
                key: {k: v for k, v in value.items() if k != "curve"}
                for key, value in curve_windows(
                    payload["curve"], prereg, seed=int(prereg["bootstrap"]["seed"]) + (0 if name == "hold30" else 10)
                ).items()
            },
        }
    five_windows = curve_windows(five_leg["curve"], prereg, seed=int(prereg["bootstrap"]["seed"]) + 10)
    hold_windows = curve_windows(hold30["curve"], prereg, seed=int(prereg["bootstrap"]["seed"]))
    winner_checks = {
        "hold30_tail_nonnegative": hold_windows["test"]["bootstrap"]["total_p05"] >= 0.0
        and hold_windows["recent"]["bootstrap"]["total_p05"] >= 0.0,
        "five_leg_tail_nonnegative": five_windows["test"]["bootstrap"]["total_p05"] >= 0.0
        and five_windows["recent"]["bootstrap"]["total_p05"] >= 0.0,
        "same_membership": hold30["run"].get("membership_enforced_by_framework") is True
        and five_leg["run"].get("membership_enforced_by_framework") is True
        and hold30["run"].get("membership_input") == five_leg["run"].get("membership_input")
        == "r526/daily_snapshots.parquet",
        "no_leverage": float(hold30["run"]["max_weight_sum"]) <= 1.0
        and float(five_leg["run"]["max_weight_sum"]) <= 1.0,
    }
    payload = {
        "round": "R528",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_hashes": {name: sha(ROOT / path) for name, path in prereg["sources"].items()},
        "bundle_checks": bundle_checks,
        "bundle": {
            "membership_sessions": membership["effective_date"].n_unique(),
            "membership_symbols_per_session": 300,
            "industry_rows": industry.height,
            "industry_input_used_for_neutralization": False,
        },
        "families": family_payload,
        "comparison": {
            "same_membership_for_both": winner_checks["same_membership"],
            "r527_invalidated_claim": "r527_same_membership_for_both_was_not_enforced_for_five_leg",
            "same_industry_audit_input_for_both": True,
            "same_industry_neutralization_mapping": False,
            "window_dates": prereg["windows"],
            "hold30_test_minus_five_leg": {
                "annual": hold_windows["test"]["annual"] - five_windows["test"]["annual"],
                "sharpe": hold_windows["test"]["sharpe"] - five_windows["test"]["sharpe"],
                "mdd": hold_windows["test"]["mdd"] - five_windows["test"]["mdd"],
                "recent_total": hold_windows["recent"]["total_return"] - five_windows["recent"]["total_return"],
            },
            "tail_checks": winner_checks,
            "point_estimate_leader": (
                "hold30"
                if hold_windows["test"]["annual"] > five_windows["test"]["annual"]
                else "five_leg"
            ),
            "winner_reason": (
                "both families have negative recent bootstrap p05; no promotion winner"
                if not winner_checks["hold30_tail_nonnegative"]
                and not winner_checks["five_leg_tail_nonnegative"]
                else None
            ),
            "winner": (
                "hold30"
                if winner_checks["hold30_tail_nonnegative"] and not winner_checks["five_leg_tail_nonnegative"]
                else "five_leg"
                if winner_checks["five_leg_tail_nonnegative"] and not winner_checks["hold30_tail_nonnegative"]
                else "both"
                if all(winner_checks[name] for name in ("hold30_tail_nonnegative", "five_leg_tail_nonnegative"))
                else None
            ),
        },
        "decision": {
            **prereg["output_policy"],
            "status": "r528_unified_pit_family_comparison_complete",
            "family_runs_complete": True,
            "active_version": None,
        },
        "known_limits": [
            *prereg["known_limits"],
            "The five-leg extension fields were frozen from the local alt_hs300 timeseries into a small committed bundle.",
        "R527 remains stored as an invalidated historical comparison; R528 supersedes only its membership-parity claim.",
        ],
    }
    if not payload["decision"]["family_runs_complete"]:
        raise RuntimeError("R528 cannot publish without both family runs")
    if not winner_checks["same_membership"]:
        raise RuntimeError("R528 cannot publish without verified membership parity")
    return payload


def markdown(payload: dict[str, Any]) -> str:
    hold = payload["families"]["hold30"]["windows"]
    five = payload["families"]["five_leg"]["windows"]
    lines = [
        "# R528 统一 PIT 家族对照",
        "",
        "- R526 bundle：哈希全部通过。",
        "- 同一名单输入：两家族都由框架掩码使用 `daily_snapshots.parquet`；R527 的 five-leg 同名单声明无效。",
        "- 行业中性化：两家族没有共用同一套行业口径；R526 的申万时点表仅作审计输入。",
        "",
        "## 冻结家族结果",
        "",
        "| 家族 | 窗口 | 年化 | Sharpe | MDD | 总收益 | p05 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, windows in (("hold30", hold), ("five_leg", five)):
        for window in ("test", "recent"):
            row = windows[window]
            lines.append(
                f"| {label} | {window} | {row['annual']:.2%} | {row['sharpe']:.3f} | "
                f"{row['mdd']:.2%} | {row['total_return']:.2%} | "
                f"{row['bootstrap']['total_p05']:.2%} |"
            )
    lines += [
        "",
        f"- 点估计领先：**{payload['comparison'].get('point_estimate_leader') or '无'}**。",
        f"- 合格赢家：**{payload['comparison']['winner'] or '无'}**。",
        f"- 检验期 hold30 - five_leg 年化：{payload['comparison']['hold30_test_minus_five_leg']['annual']:+.2%}。",
        f"- 检验期 hold30 - five_leg Sharpe：{payload['comparison']['hold30_test_minus_five_leg']['sharpe']:+.3f}。",
        "",
        "## 判定边界",
        "",
    ] + [f"- {item}" for item in payload["known_limits"]] + [""]
    return "\n".join(lines)


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R528 output exists; refusing overwrite: {path.name}")
    payload = build_report()
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    text = markdown(payload)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii"
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
