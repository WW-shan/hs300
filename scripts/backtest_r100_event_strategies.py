#!/usr/bin/env python3
"""R100: event/behaviour factor strategies under historical HS300 membership.

Covers LHB, pledge, repurchase, block trades, analyst ratings, earnings
preannouncements/express reports and a multi-event combo.  The script records
current-vs-historical membership, neutralization and 3x-cost evidence.
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import polars as pl

from app.backtest import strategy as strategy_module
from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.factors.store import load_into_registry
from app.services.industry_map import industry_loader_for
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyEngine
from app.tickflow.repository import DataStore, KlineRepository

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank"
    / "evidence" / "r100_event_strategy_backtests.json"
)
MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
FULL_START = date(2021, 6, 1)
FULL_END = date(2026, 10, 16)
PHASE_STARTS = [
    date(2021, 6, 1), date(2022, 1, 4), date(2022, 7, 1),
    date(2023, 1, 3), date(2023, 7, 3), date(2024, 1, 2),
    date(2024, 7, 1), date(2025, 1, 2), date(2025, 7, 1),
    date(2026, 1, 5),
]
RECENT_STARTS = [
    date(2025, 10, 14), date(2025, 12, 15), date(2026, 2, 13),
    date(2026, 4, 15), date(2026, 6, 15),
]
STRATEGIES = [
    "ai_hs300_lhb_quality",
    "ai_hs300_pledge_quality",
    "ai_hs300_repurchase_quality",
    "ai_hs300_block_trade_quality",
    "ai_hs300_analyst_quality",
    "ai_hs300_earnings_surprise_quality",
    "ai_hs300_event_combo",
]
FACTORS = [
    "ext_alt_hs300_lhb_net_buy_ratio_20d",
    "ext_alt_hs300_lhb_inst_net_buy_ratio_20d",
    "ext_alt_hs300_lhb_event_count_20d_low",
    "ext_alt_hs300_pledge_ratio_low",
    "ext_alt_hs300_pledge_ratio_chg_1m_low",
    "ext_alt_hs300_repurchase_event_60d",
    "ext_alt_hs300_block_trade_ratio_20d_low",
    "ext_alt_hs300_block_premium_20d",
    "ext_alt_hs300_analyst_rating_score_20d",
    "ext_alt_hs300_analyst_rating_change_20d",
    "ext_alt_hs300_forecast_surprise_60d",
    "ext_alt_hs300_express_surprise_60d",
]
VARIANTS = {
    "current_default": {"historical": False, "overrides": {}},
    "historical_default": {"historical": True, "overrides": {}},
    "historical_industry": {
        "historical": True,
        "overrides": {"neutralize": {"industry": "industry_l1"}},
    },
    "historical_industry_size": {
        "historical": True,
        "overrides": {
            "neutralize": {"industry": "industry_l1", "styles": ["log_float_mv"]}
        },
    },
}


def _membership() -> pl.DataFrame:
    return (
        pl.read_parquet(MEMBERSHIP)
        .select([
            pl.col("query_date").cast(pl.Date).alias("effective_date"),
            pl.col("symbol").cast(pl.String),
        ])
        .unique(subset=["effective_date", "symbol"], keep="last")
        .sort(["effective_date", "symbol"])
    )


@contextmanager
def _patched(membership: pl.DataFrame):
    original = strategy_module._hs300_membership_frame
    strategy_module._hs300_membership_frame = lambda _start, _end: membership
    try:
        yield
    finally:
        strategy_module._hs300_membership_frame = original


def _service() -> StrategyBacktestService:
    load_into_registry(DATA)
    engine = StrategyEngine(
        strategy_dirs=[DATA / "strategies" / "ai"],
        override_loader=lambda sid: strategy_config.load_override(DATA, sid),
        industry_loader=industry_loader_for(DATA),
    )
    return StrategyBacktestService(
        BacktestEngine(KlineRepository(DataStore(DATA))), engine
    )


def _config(
    strategy_id: str,
    start: date,
    end: date,
    *,
    overrides: dict,
    max_positions: int,
    cost_multiplier: float,
) -> StrategyBacktestConfig:
    return StrategyBacktestConfig(
        strategy_id=strategy_id,
        symbols=None,
        start=start,
        end=end,
        params={},
        overrides=overrides,
        matching="open_t+1",
        fees_pct=0.0002 * cost_multiplier,
        commission_pct=0.0002 * cost_multiplier,
        stamp_tax_pct=0.0005,
        slippage_bps=5.0 * cost_multiplier,
        max_positions=max_positions,
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        hs300=True,
    )


def _run(
    service: StrategyBacktestService,
    membership: pl.DataFrame,
    *,
    strategy_id: str,
    variant: str,
    start: date,
    end: date,
    max_positions: int = 10,
    cost_multiplier: float = 2.0,
) -> dict:
    spec = VARIANTS[variant]
    cfg = _config(
        strategy_id,
        start,
        end,
        overrides=spec["overrides"],
        max_positions=max_positions,
        cost_multiplier=cost_multiplier,
    )
    started = time.perf_counter()
    if spec["historical"]:
        with _patched(membership):
            result = service.run(cfg)
    else:
        result = service.run(cfg)
    record = {
        "strategy_id": strategy_id,
        "variant": variant,
        "start": str(start),
        "end": str(end),
        "max_positions": max_positions,
        "cost_multiplier": cost_multiplier,
        "overrides": spec["overrides"],
        "error": result.error,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "stats": result.stats,
        "factor_attribution": result.factor_attribution,
    }
    print(
        f"{strategy_id} {variant} {start} p{max_positions} cost{cost_multiplier}x: "
        f"err={result.error} ret={result.stats.get('total_return')} "
        f"sharpe={result.stats.get('sharpe')} mdd={result.stats.get('max_drawdown')} "
        f"trades={result.stats.get('n_trades')}",
        flush=True,
    )
    return record


def _summary(records: list[dict]) -> dict:
    def stat(key: str) -> dict | None:
        values = [
            float(record["stats"][key])
            for record in records
            if record.get("stats", {}).get(key) is not None
        ]
        if not values:
            return None
        ordered = sorted(values)
        return {
            "mean": round(sum(values) / len(values), 6),
            "median": round(ordered[len(ordered) // 2], 6),
            "min": round(min(values), 6),
            "max": round(max(values), 6),
            "positive": sum(value > 0 for value in values),
            "n": len(values),
        }

    return {
        "total_return": stat("total_return"),
        "annual_return": stat("annual_return"),
        "sharpe": stat("sharpe"),
        "max_drawdown": stat("max_drawdown"),
        "n_trades": stat("n_trades"),
    }


def _coverage() -> dict:
    paths = sorted((DATA / "ext_data" / "alt_hs300" / "timeseries").glob("date=*/part.parquet"))
    if not paths:
        return {}
    raw_names = {
        factor: factor.removeprefix("ext_alt_hs300_") for factor in FACTORS
    }
    frame = pl.scan_parquet([str(path) for path in paths]).select(
        list(raw_names.values())
    ).collect()
    out = {}
    for factor in FACTORS:
        values = frame[raw_names[factor]]
        out[factor] = {
            "nonnull_rate": round(float(values.is_not_null().mean()), 6),
            "nonzero_rate": round(float((values.fill_null(0) != 0).mean()), 6),
            "min": values.min(),
            "median": values.median(),
            "max": values.max(),
        }
    return out


def main() -> int:
    membership = _membership()
    service = _service()
    full: dict[str, dict] = {}
    phases: dict[str, dict[str, list[dict]]] = {}
    recent: dict[str, dict[str, list[dict]]] = {}

    for strategy_id in STRATEGIES:
        for variant in VARIANTS:
            full[f"{strategy_id}:{variant}:p10"] = _run(
                service, membership, strategy_id=strategy_id, variant=variant,
                start=FULL_START, end=FULL_END, max_positions=10,
            )
            full[f"{strategy_id}:{variant}:p20"] = _run(
                service, membership, strategy_id=strategy_id, variant=variant,
                start=FULL_START, end=FULL_END, max_positions=20,
            )
        phases[strategy_id] = {}
        recent[strategy_id] = {}
        for variant in ("historical_default", "historical_industry"):
            phases[strategy_id][variant] = [
                _run(
                    service, membership, strategy_id=strategy_id, variant=variant,
                    start=phase, end=FULL_END,
                )
                for phase in PHASE_STARTS
            ]
            recent[strategy_id][variant] = [
                _run(
                    service, membership, strategy_id=strategy_id, variant=variant,
                    start=phase, end=FULL_END,
                )
                for phase in RECENT_STARTS
            ]

    cost3x = {}
    for strategy_id in STRATEGIES:
        full_record = _run(
            service, membership, strategy_id=strategy_id,
            variant="historical_default", start=FULL_START, end=FULL_END,
            cost_multiplier=3.0,
        )
        recent_records = [
            _run(
                service, membership, strategy_id=strategy_id,
                variant="historical_default", start=phase, end=FULL_END,
                cost_multiplier=3.0,
            )
            for phase in RECENT_STARTS
        ]
        cost3x[strategy_id] = {
            "full": full_record,
            "recent_phases": recent_records,
            "recent_summary": _summary(recent_records),
        }

    payload = {
        "purpose": "R100 event/behaviour factors and strategy backtests",
        "engine": "StrategyBacktestService / project T+1 execution path",
        "cost": {
            "2x": {
                "fees_pct": 0.0004, "commission_pct": 0.0004,
                "stamp_tax_pct": 0.0005, "slippage_bps": 10.0,
            },
            "3x": {
                "fees_pct": 0.0006, "commission_pct": 0.0006,
                "stamp_tax_pct": 0.0005, "slippage_bps": 15.0,
            },
        },
        "membership": {
            "source": str(MEMBERSHIP.relative_to(ROOT)),
            "start": str(membership["effective_date"].min()),
            "end": str(membership["effective_date"].max()),
            "dates": membership["effective_date"].n_unique(),
            "symbols": membership["symbol"].n_unique(),
        },
        "periods": {
            "full": [str(FULL_START), str(FULL_END)],
            "phase_starts": [str(day) for day in PHASE_STARTS],
            "recent_starts": [str(day) for day in RECENT_STARTS],
        },
        "strategies": STRATEGIES,
        "factors": FACTORS,
        "factor_coverage": _coverage(),
        "variants": VARIANTS,
        "full": full,
        "phase_results": phases,
        "phase_summary": {
            sid: {variant: _summary(records) for variant, records in variants.items()}
            for sid, variants in phases.items()
        },
        "recent_results": recent,
        "recent_summary": {
            sid: {variant: _summary(records) for variant, records in variants.items()}
            for sid, variants in recent.items()
        },
        "cost3x": cost3x,
        "caveats": [
            "Event scores use announcement date + next trading day and linear decay.",
            "Pledge snapshots use next calendar day and expire after 14 days; missing is not zero-filled.",
            "Analyst ratings use a 180-day staleness cap; no coverage remains null.",
            "Current-membership results are recorded only for survivorship comparison.",
            "Experimental strategies are not deployment-approved by this evidence.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
