"""Deterministic GBM market stress using the project matrix execution path."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

import numpy as np
import polars as pl

from app.backtest.engine import BacktestEngine, MatcherConfig, SimulationOptions
from app.backtest.matrix import (
    MarketDataMatrix,
    MarketMatrix,
    SignalMatrix,
    build_market_data_matrix,
    build_market_matrix_from_signals,
    make_signal_matrix,
)
from app.strategy.research.factor_rank_research import FactorRankResearchMatrixStrategy

DEFAULT_MASTER_SEED = 20261125
WARMUP_DAYS = 60
EVALUATION_DAYS = 504
ASSET_COUNT = 300
INDUSTRY_SIZE = 10
INDUSTRY_COUNT = 30
MAX_POSITIONS = 10
MAX_HOLD_DAYS = 60
INDUSTRY_CAP = 3
TOP_RANK = 30
ENTRY_SCORE = 70.0
EXIT_SCORE = 40.0
INITIAL_CAPITAL = 50_000_000.0
TRADING_DAYS_PER_YEAR = 252.0
STOP_VARIANTS = (("no_stop", None), ("stop_12", -0.12))
COST_MULTIPLIERS = (2, 3, 5)
CORRELATIONS = (0.0, 0.7)
SIGNAL_MODES = (
    "random_null",
    "momentum_20",
    "momentum_60",
    "reversal_5",
    "low_volatility_20",
    "momentum_low_vol_20",
)


@dataclass(frozen=True, slots=True)
class GbmScenario:
    scenario_id: str
    annual_drift: float
    annual_volatility: float


SCENARIOS = {
    "neutral": GbmScenario("neutral", 0.00, 0.20),
    "bull": GbmScenario("bull", 0.08, 0.18),
    "bear": GbmScenario("bear", -0.08, 0.25),
    "high_volatility": GbmScenario("high_volatility", 0.00, 0.35),
}


@dataclass(frozen=True, slots=True)
class SyntheticPathSpec:
    scenario_id: str
    beta: float
    path_id: int
    master_seed: int = DEFAULT_MASTER_SEED

    def __post_init__(self) -> None:
        if self.scenario_id not in SCENARIOS:
            raise ValueError(f"unknown GBM scenario: {self.scenario_id}")
        if not math.isfinite(self.beta) or not 0.0 <= self.beta <= 1.0:
            raise ValueError("beta must be finite and between 0 and 1")
        if self.path_id < 0:
            raise ValueError("path_id must be non-negative")
        if self.master_seed < 0:
            raise ValueError("master_seed must be non-negative")


@dataclass(frozen=True, slots=True)
class SyntheticMarket:
    spec: SyntheticPathSpec
    dates: tuple[date, ...]
    symbols: tuple[str, ...]
    industries: dict[str, str]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    amount: np.ndarray
    turnover_rate: np.ndarray
    scores: dict[str, np.ndarray]
    signal_seed: int
    realized_equal_weight_return: float
    realized_equal_weight_volatility: float

    @property
    def shape(self) -> tuple[int, int]:
        return self.close.shape


def trading_days(start: date, count: int) -> tuple[date, ...]:
    if count <= 0:
        raise ValueError("trading-day count must be positive")
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return tuple(days)


def _percentile_scores(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    valid = np.asarray(valid, dtype=bool)
    scores = np.zeros(values.shape, dtype=np.float32)
    selected = np.flatnonzero(valid & np.isfinite(values))
    if selected.size == 0:
        return scores
    if selected.size == 1:
        scores[selected] = 50.0
        return scores
    order = np.argsort(np.argsort(values[selected], kind="stable"), kind="stable")
    scores[selected] = (order.astype(np.float32) / np.float32(selected.size - 1)) * 100.0
    return scores


def compute_signal_scores(
    close: np.ndarray,
    *,
    warmup_days: int,
    random_seed: int = 0,
) -> dict[str, np.ndarray]:
    """Return 0..100 cross-sectional scores known at each close."""
    prices = np.asarray(close, dtype=np.float64)
    if prices.ndim != 2 or prices.shape[0] < 2:
        raise ValueError("close must be a two-dimensional array with at least two rows")
    if not np.isfinite(prices).all() or np.any(prices <= 0):
        raise ValueError("synthetic close prices must be finite and positive")
    if warmup_days < 0 or warmup_days >= prices.shape[0]:
        raise ValueError("warmup_days must be non-negative and shorter than prices")

    days, assets = prices.shape
    returns = np.zeros_like(prices)
    returns[1:] = prices[1:] / prices[:-1] - 1.0
    momentum_20 = np.zeros_like(prices)
    momentum_60 = np.zeros_like(prices)
    reversal_5 = np.zeros_like(prices)
    low_volatility_20 = np.zeros_like(prices)
    rng = np.random.default_rng(random_seed)
    random_scores = np.zeros_like(prices, dtype=np.float32)
    random_active = rng.uniform(0.0, 100.0, size=(days - warmup_days, assets))

    for time_id in range(warmup_days, days):
        if time_id >= 20:
            momentum_20[time_id] = prices[time_id] / prices[time_id - 20] - 1.0
        if time_id >= 60:
            momentum_60[time_id] = prices[time_id] / prices[time_id - 60] - 1.0
        if time_id >= 5:
            reversal_5[time_id] = -(prices[time_id] / prices[time_id - 5] - 1.0)
        if time_id >= 20:
            low_volatility_20[time_id] = -np.std(
                returns[time_id - 19 : time_id + 1],
                axis=0,
                ddof=1,
            )

    scores: dict[str, np.ndarray] = {}
    for time_id in range(warmup_days, days):
        valid = np.isfinite(prices[time_id])
        momentum_20_score = _percentile_scores(momentum_20[time_id], valid)
        momentum_60_score = _percentile_scores(momentum_60[time_id], valid)
        reversal_5_score = _percentile_scores(reversal_5[time_id], valid)
        low_volatility_score = _percentile_scores(low_volatility_20[time_id], valid)
        composite_score = (
            momentum_20_score.astype(np.float64)
            + low_volatility_score.astype(np.float64)
        ) / 2.0
        scores.setdefault("momentum_20", np.zeros_like(prices, dtype=np.float32))[time_id] = momentum_20_score
        scores.setdefault("momentum_60", np.zeros_like(prices, dtype=np.float32))[time_id] = momentum_60_score
        scores.setdefault("reversal_5", np.zeros_like(prices, dtype=np.float32))[time_id] = reversal_5_score
        scores.setdefault("low_volatility_20", np.zeros_like(prices, dtype=np.float32))[time_id] = low_volatility_score
        scores.setdefault("momentum_low_vol_20", np.zeros_like(prices, dtype=np.float32))[time_id] = composite_score.astype(np.float32)
        random_scores[time_id] = random_active[time_id - warmup_days]
    scores["random_null"] = random_scores
    return {name: scores[name] for name in SIGNAL_MODES}


def _seed_sequence(spec: SyntheticPathSpec) -> np.random.SeedSequence:
    scenario_index = tuple(SCENARIOS).index(spec.scenario_id)
    return np.random.SeedSequence([spec.master_seed, scenario_index, spec.path_id])


def generate_synthetic_market(
    spec: SyntheticPathSpec,
    *,
    asset_count: int = ASSET_COUNT,
    warmup_days: int = WARMUP_DAYS,
    evaluation_days: int = EVALUATION_DAYS,
    start: date = date(2024, 1, 2),
    start_price: float = 20.0,
    symbols: Sequence[str] | None = None,
    industries: Mapping[str, str] | None = None,
    dates: Sequence[date] | None = None,
) -> SyntheticMarket:
    if asset_count < 2:
        raise ValueError("asset_count must be at least two")
    if warmup_days < 0 or evaluation_days < 2:
        raise ValueError("warmup_days must be non-negative and evaluation_days at least two")
    evaluation_dates = tuple(dates) if dates is not None else None
    if evaluation_dates is not None:
        if len(evaluation_dates) != evaluation_days:
            raise ValueError("dates length must equal evaluation_days")
        if len(set(evaluation_dates)) != len(evaluation_dates):
            raise ValueError("evaluation dates must be unique")
        if any(not isinstance(value, date) for value in evaluation_dates):
            raise ValueError("evaluation dates must be date objects")
        start = evaluation_dates[0]
    if not math.isfinite(start_price) or start_price <= 0:
        raise ValueError("start_price must be finite and positive")
    if symbols is not None:
        if len(symbols) != asset_count:
            raise ValueError("symbols length must equal asset_count")
        if len(set(symbols)) != len(symbols):
            raise ValueError("synthetic symbols must be unique")
        if any(not symbol for symbol in symbols):
            raise ValueError("synthetic symbols must be non-empty")
    if industries is not None:
        if symbols is None and len(industries) != asset_count:
            raise ValueError("industries require matching symbols")
        if symbols is not None and set(industries) != set(symbols):
            raise ValueError("industry map must contain exactly the synthetic symbols")

    scenario = SCENARIOS[spec.scenario_id]
    total_days = warmup_days + evaluation_days
    seed_sequence = _seed_sequence(spec)
    market_seq, idio_seq, overnight_seq, intraday_seq, amount_seq, signal_seq = seed_sequence.spawn(6)
    market_rng = np.random.default_rng(market_seq)
    idio_rng = np.random.default_rng(idio_seq)
    overnight_rng = np.random.default_rng(overnight_seq)
    intraday_rng = np.random.default_rng(intraday_seq)
    amount_rng = np.random.default_rng(amount_seq)
    signal_seed = int(signal_seq.generate_state(1, dtype=np.uint32)[0])

    common_shock = market_rng.standard_normal(total_days)
    idiosyncratic_shock = idio_rng.standard_normal((total_days, asset_count))
    total_shock = (
        spec.beta * common_shock[:, None]
        + math.sqrt(1.0 - spec.beta**2) * idiosyncratic_shock
    )
    daily_drift = scenario.annual_drift / TRADING_DAYS_PER_YEAR
    daily_volatility = scenario.annual_volatility / math.sqrt(TRADING_DAYS_PER_YEAR)
    log_increments = (
        daily_drift
        - 0.5 * daily_volatility**2
        + daily_volatility * total_shock
    )
    full_close = np.empty((total_days, asset_count), dtype=np.float64)
    full_close[0] = start_price
    full_close[1:] = start_price * np.exp(np.cumsum(log_increments[1:], axis=0))

    overnight_volatility = 0.10 / math.sqrt(TRADING_DAYS_PER_YEAR)
    full_open = np.empty_like(full_close)
    full_open[0] = full_close[0]
    full_open[1:] = full_close[:-1] * np.exp(
        overnight_volatility * overnight_rng.standard_normal((total_days - 1, asset_count))
    )
    spread = np.abs(
        intraday_rng.standard_normal((total_days, asset_count))
    ) * (0.10 / math.sqrt(TRADING_DAYS_PER_YEAR))
    upper = np.maximum(full_open, full_close)
    lower = np.minimum(full_open, full_close)
    full_high = upper * (1.0 + spread)
    full_low = lower * (1.0 - spread)
    full_amount = 10_000_000.0 * np.exp(
        0.35 * amount_rng.standard_normal((total_days, asset_count))
    )
    full_volume = full_amount / full_close
    full_turnover = np.clip(
        1.0 + np.abs(intraday_rng.standard_normal((total_days, asset_count))),
        0.01,
        None,
    )
    full_returns = np.zeros_like(full_close)
    full_returns[1:] = full_close[1:] / full_close[:-1] - 1.0
    active_returns = full_returns[warmup_days : warmup_days + evaluation_days]
    equal_weight_returns = np.mean(active_returns, axis=1)
    realized_annual_return = (
        float(np.prod(1.0 + equal_weight_returns) ** (TRADING_DAYS_PER_YEAR / len(equal_weight_returns)) - 1.0)
        if evaluation_days > 0
        else 0.0
    )
    realized_annual_volatility = float(
        np.std(equal_weight_returns, ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR)
    )

    stop = warmup_days + evaluation_days
    resolved_symbols = tuple(symbols) if symbols is not None else tuple(
        f"SYN{asset_id:03d}" for asset_id in range(asset_count)
    )
    resolved_industries = dict(industries) if industries is not None else {
        symbol: f"SYN_IND_{(asset_id // INDUSTRY_SIZE):02d}"
        for asset_id, symbol in enumerate(resolved_symbols)
    }
    all_scores = compute_signal_scores(
        full_close,
        warmup_days=warmup_days,
        random_seed=signal_seed,
    )
    active_scores = {
        name: np.array(values[warmup_days:stop], dtype=np.float32, copy=True)
        for name, values in all_scores.items()
    }
    return SyntheticMarket(
        spec=spec,
        dates=evaluation_dates or trading_days(start, evaluation_days),
        symbols=resolved_symbols,
        industries=resolved_industries,
        open=np.array(full_open[warmup_days:stop], dtype=np.float32, copy=True),
        high=np.array(full_high[warmup_days:stop], dtype=np.float32, copy=True),
        low=np.array(full_low[warmup_days:stop], dtype=np.float32, copy=True),
        close=np.array(full_close[warmup_days:stop], dtype=np.float32, copy=True),
        volume=np.array(full_volume[warmup_days:stop], dtype=np.float32, copy=True),
        amount=np.array(full_amount[warmup_days:stop], dtype=np.float32, copy=True),
        turnover_rate=np.array(full_turnover[warmup_days:stop], dtype=np.float32, copy=True),
        scores=active_scores,
        signal_seed=signal_seed,
        realized_equal_weight_return=realized_annual_return,
        realized_equal_weight_volatility=realized_annual_volatility,
    )


def build_synthetic_panel(market: SyntheticMarket) -> pl.DataFrame:
    days, assets = market.shape
    dates = np.repeat(np.asarray(market.dates, dtype="datetime64[D]"), assets)
    symbols = np.tile(np.asarray(market.symbols, dtype=object), days)
    payload: dict[str, Any] = {
        "date": pl.Series("date", dates, dtype=pl.Date),
        "symbol": symbols,
        "name": symbols,
        "open": market.open.ravel(),
        "high": market.high.ravel(),
        "low": market.low.ravel(),
        "close": market.close.ravel(),
        "volume": market.volume.ravel(),
        "amount": market.amount.ravel(),
        "turnover_rate": market.turnover_rate.ravel(),
        "signal_limit_up": np.zeros(days * assets, dtype=bool),
        "signal_limit_down": np.zeros(days * assets, dtype=bool),
    }
    payload.update({name: values.ravel() for name, values in market.scores.items()})
    return pl.DataFrame(payload)


def build_project_market(market: SyntheticMarket) -> MarketDataMatrix:
    return build_market_data_matrix(
        build_synthetic_panel(market),
        field_columns=set(market.scores),
    )


def _strategy_params(market: SyntheticMarket, *, top_rank: int, industry_cap: int) -> dict[str, Any]:
    return {
        "entry_score": ENTRY_SCORE,
        "exit_score": EXIT_SCORE,
        "top_rank": top_rank,
        "industry_by_symbol": market.industries,
        "max_industry_positions": industry_cap,
    }


def build_strategy_signals(
    market: SyntheticMarket,
    signal_mode: str,
    *,
    base_market: MarketDataMatrix | None = None,
    top_rank: int = TOP_RANK,
    industry_cap: int = INDUSTRY_CAP,
) -> SignalMatrix:
    if signal_mode not in market.scores:
        raise ValueError(f"unknown synthetic signal mode: {signal_mode}")
    base = base_market or build_project_market(market)
    strategy = FactorRankResearchMatrixStrategy(scoring={signal_mode: 1.0})
    return strategy.compute_signals(
        base,
        _strategy_params(market, top_rank=top_rank, industry_cap=industry_cap),
    )


def build_strategy_market(
    market: SyntheticMarket,
    signal_mode: str,
    *,
    base_market: MarketDataMatrix | None = None,
    top_rank: int = TOP_RANK,
    industry_cap: int = INDUSTRY_CAP,
) -> MarketMatrix:
    base = base_market or build_project_market(market)
    signals = build_strategy_signals(
        market,
        signal_mode,
        base_market=base,
        top_rank=top_rank,
        industry_cap=industry_cap,
    )
    return build_market_matrix_from_signals(
        base,
        signals,
        entry_delay_bars=1,
        exit_delay_bars=1,
    )


def build_benchmark_market(
    market: SyntheticMarket,
    *,
    base_market: MarketDataMatrix | None = None,
) -> MarketMatrix:
    base = base_market or build_project_market(market)
    entry = np.zeros(base.shape, dtype=np.uint8)
    exit_ = np.zeros(base.shape, dtype=np.uint8)
    entry[0] = 1
    signals = make_signal_matrix(
        base.shape,
        entry=entry,
        exit=exit_,
        score=np.full(base.shape, 100.0, dtype=np.float32),
        entry_signal_code=np.where(entry, 1, -1).astype(np.int16),
        exit_signal_code=np.full(base.shape, -1, dtype=np.int16),
        entry_signal_ids=("synthetic_equal_weight_entry",),
        exit_signal_ids=("synthetic_equal_weight_exit",),
    )
    return build_market_matrix_from_signals(
        base,
        signals,
        entry_delay_bars=1,
        exit_delay_bars=1,
    )


def cost_parameters(cost_multiplier: int) -> dict[str, float]:
    if cost_multiplier == 2:
        commission, slippage = 0.0004, 10.0
    elif cost_multiplier == 3:
        commission, slippage = 0.0006, 15.0
    elif cost_multiplier == 5:
        commission, slippage = 0.0010, 25.0
    else:
        raise ValueError(f"unsupported cost multiplier: {cost_multiplier}")
    return {
        "fees_pct": commission,
        "commission_pct": commission,
        "stamp_tax_pct": 0.0005,
        "slippage_bps": slippage,
    }


def matcher_config(
    cost_multiplier: int,
    *,
    stop_loss_pct: float | None,
    max_positions: int = MAX_POSITIONS,
    max_hold_days: int | None = MAX_HOLD_DAYS,
    initial_capital: float = INITIAL_CAPITAL,
) -> MatcherConfig:
    if max_positions < 1:
        raise ValueError("max_positions must be positive")
    if max_hold_days is not None and max_hold_days < 1:
        raise ValueError("max_hold_days must be positive or None")
    return MatcherConfig(
        matching="open_t+1",
        **cost_parameters(cost_multiplier),
        stop_loss_pct=stop_loss_pct,
        trailing_stop_pct=None,
        max_hold_days=max_hold_days,
        max_positions=max_positions,
        max_exposure_pct=1.0,
        initial_capital=initial_capital,
        position_sizing="equal",
    )


_SIMULATION_OPTIONS = SimulationOptions(
    include_monte_carlo=False,
    include_curves=False,
    include_trades=True,
    include_per_symbol_stats=False,
    include_return_distribution=False,
)


def _simulation_metrics(
    engine: BacktestEngine,
    matrix: MarketMatrix,
    config: MatcherConfig,
    *,
    years: float,
) -> dict[str, float | int]:
    result = engine.simulate_market_matrix(matrix, config, options=_SIMULATION_OPTIONS)
    stats = result.stats
    trades = result.trades
    one_way_turnover = (
        sum(abs(float(trade.entry_value)) for trade in trades)
        / config.initial_capital
        / years
        if years > 0
        else 0.0
    )
    return {
        "total_return": float(stats.get("total_return", 0.0)),
        "annual_return": float(stats.get("annual_return", 0.0)),
        "sharpe": float(stats.get("sharpe", 0.0)),
        "max_drawdown": float(stats.get("max_drawdown", 0.0)),
        "avg_exposure": float(stats.get("avg_exposure", 0.0)),
        "max_exposure": float(stats.get("max_exposure", 0.0)),
        "n_trades": int(stats.get("n_trades", 0)),
        "turnover": float(one_way_turnover),
    }


def with_fixed_score(
    market: SyntheticMarket,
    *,
    scores: np.ndarray,
    score_name: str = "fixed_strategy",
) -> SyntheticMarket:
    values = np.asarray(scores, dtype=np.float32)
    if values.shape != market.shape:
        raise ValueError("fixed scores must match market shape")
    if not np.isfinite(values).all():
        raise ValueError("fixed scores must be finite")
    if np.any(values < 0.0) or np.any(values > 100.0):
        raise ValueError("fixed scores must be between 0 and 100")
    return replace(market, scores={score_name: np.array(values, copy=True)}), score_name


def run_fixed_score_path(
    spec: SyntheticPathSpec,
    *,
    scores: np.ndarray,
    score_name: str = "fixed_strategy",
    asset_count: int = ASSET_COUNT,
    warmup_days: int = WARMUP_DAYS,
    evaluation_days: int = EVALUATION_DAYS,
    max_positions: int = MAX_POSITIONS,
    max_hold_days: int = MAX_HOLD_DAYS,
    top_rank: int = TOP_RANK,
    industry_cap: int = INDUSTRY_CAP,
    stop_loss_pct: float | None = -0.12,
    cost_multipliers: tuple[int, ...] = COST_MULTIPLIERS,
    symbols: Sequence[str] | None = None,
    industries: Mapping[str, str] | None = None,
    dates: Sequence[date] | None = None,
) -> dict[str, Any]:
    market = generate_synthetic_market(
        spec,
        asset_count=asset_count,
        warmup_days=warmup_days,
        evaluation_days=evaluation_days,
        symbols=symbols,
        industries=industries,
        dates=dates,
    )
    market, resolved_name = with_fixed_score(market, scores=scores, score_name=score_name)
    base = build_project_market(market)
    engine = BacktestEngine(None)
    years = evaluation_days / TRADING_DAYS_PER_YEAR
    benchmark_matrix = build_benchmark_market(market, base_market=base)
    benchmarks = {
        cost: _simulation_metrics(
            engine,
            benchmark_matrix,
            matcher_config(
                cost,
                stop_loss_pct=None,
                max_positions=asset_count,
                max_hold_days=None,
            ),
            years=years,
        )
        for cost in cost_multipliers
    }
    strategy_matrix = build_strategy_market(
        market,
        resolved_name,
        base_market=base,
        top_rank=top_rank,
        industry_cap=industry_cap,
    )
    observations: list[dict[str, Any]] = []
    for cost_multiplier in cost_multipliers:
        metrics = _simulation_metrics(
            engine,
            strategy_matrix,
            matcher_config(
                cost_multiplier,
                stop_loss_pct=stop_loss_pct,
                max_positions=max_positions,
                max_hold_days=max_hold_days,
            ),
            years=years,
        )
        benchmark = benchmarks[cost_multiplier]
        observations.append({
            "scenario_id": spec.scenario_id,
            "beta": spec.beta,
            "path_id": spec.path_id,
            "signal_mode": resolved_name,
            "stop_variant": "stop_12" if stop_loss_pct == -0.12 else "custom_stop",
            "stop_loss_pct": stop_loss_pct,
            "cost_multiplier": cost_multiplier,
            **{f"strategy_{key}": value for key, value in metrics.items()},
            **{f"benchmark_{key}": value for key, value in benchmark.items()},
            "excess_return": float(metrics["total_return"]) - float(benchmark["total_return"]),
        })
    return {
        "signal_name": resolved_name,
        "path_key": {
            "scenario_id": spec.scenario_id,
            "beta": spec.beta,
            "path_id": spec.path_id,
        },
        "realized": {
            "scenario_id": spec.scenario_id,
            "beta": spec.beta,
            "path_id": spec.path_id,
            "close_sha256": hashlib.sha256(market.close.tobytes()).hexdigest(),
            "equal_weight_annual_return": market.realized_equal_weight_return,
            "equal_weight_annual_volatility": market.realized_equal_weight_volatility,
        },
        "benchmarks": [
            {
                "scenario_id": spec.scenario_id,
                "beta": spec.beta,
                "path_id": spec.path_id,
                "cost_multiplier": cost_multiplier,
                **metrics,
            }
            for cost_multiplier, metrics in benchmarks.items()
        ],
        "observations": observations,
    }


def run_synthetic_path(
    spec: SyntheticPathSpec,
    *,
    asset_count: int = ASSET_COUNT,
    warmup_days: int = WARMUP_DAYS,
    evaluation_days: int = EVALUATION_DAYS,
    max_positions: int = MAX_POSITIONS,
    max_hold_days: int = MAX_HOLD_DAYS,
    top_rank: int = TOP_RANK,
    industry_cap: int = INDUSTRY_CAP,
    signal_modes: tuple[str, ...] = SIGNAL_MODES,
    stop_variants: tuple[tuple[str, float | None], ...] = STOP_VARIANTS,
    cost_multipliers: tuple[int, ...] = COST_MULTIPLIERS,
) -> dict[str, Any]:
    market = generate_synthetic_market(
        spec,
        asset_count=asset_count,
        warmup_days=warmup_days,
        evaluation_days=evaluation_days,
    )
    base = build_project_market(market)
    engine = BacktestEngine(None)
    years = evaluation_days / TRADING_DAYS_PER_YEAR

    benchmark_matrix = build_benchmark_market(market, base_market=base)
    benchmarks: dict[int, dict[str, float | int]] = {}
    for cost_multiplier in cost_multipliers:
        benchmarks[cost_multiplier] = _simulation_metrics(
            engine,
            benchmark_matrix,
            matcher_config(
                cost_multiplier,
                stop_loss_pct=None,
                max_positions=asset_count,
                max_hold_days=None,
            ),
            years=years,
        )

    observations: list[dict[str, Any]] = []
    for signal_mode in signal_modes:
        strategy_matrix = build_strategy_market(
            market,
            signal_mode,
            base_market=base,
            top_rank=top_rank,
            industry_cap=industry_cap,
        )
        for stop_variant, stop_loss_pct in stop_variants:
            for cost_multiplier in cost_multipliers:
                metrics = _simulation_metrics(
                    engine,
                    strategy_matrix,
                    matcher_config(
                        cost_multiplier,
                        stop_loss_pct=stop_loss_pct,
                        max_positions=max_positions,
                        max_hold_days=max_hold_days,
                    ),
                    years=years,
                )
                benchmark = benchmarks[cost_multiplier]
                observations.append({
                    "scenario_id": spec.scenario_id,
                    "beta": spec.beta,
                    "path_id": spec.path_id,
                    "signal_mode": signal_mode,
                    "stop_variant": stop_variant,
                    "stop_loss_pct": stop_loss_pct,
                    "cost_multiplier": cost_multiplier,
                    **{f"strategy_{key}": value for key, value in metrics.items()},
                    **{f"benchmark_{key}": value for key, value in benchmark.items()},
                    "excess_return": (
                        float(metrics["total_return"])
                        - float(benchmark["total_return"])
                    ),
                })
    return {
        "path_key": {
            "scenario_id": spec.scenario_id,
            "beta": spec.beta,
            "path_id": spec.path_id,
        },
        "realized": {
            "scenario_id": spec.scenario_id,
            "beta": spec.beta,
            "path_id": spec.path_id,
            "close_sha256": hashlib.sha256(market.close.tobytes()).hexdigest(),
            "equal_weight_annual_return": market.realized_equal_weight_return,
            "equal_weight_annual_volatility": market.realized_equal_weight_volatility,
        },
        "benchmarks": [
            {
                "scenario_id": spec.scenario_id,
                "beta": spec.beta,
                "path_id": spec.path_id,
                "cost_multiplier": cost_multiplier,
                **metrics,
            }
            for cost_multiplier, metrics in benchmarks.items()
        ],
        "observations": observations,
    }


def _distribution(values: list[float]) -> dict[str, float]:
    if not values:
        raise ValueError("distribution requires at least one value")
    array = np.asarray(values, dtype=np.float64)
    if not np.isfinite(array).all():
        raise ValueError("distribution values must be finite")
    return {
        "mean": round(float(np.mean(array)), 6),
        "p05": round(float(np.quantile(array, 0.05)), 6),
        "p50": round(float(np.quantile(array, 0.50)), 6),
        "p95": round(float(np.quantile(array, 0.95)), 6),
    }


def _sign_test(positive: int, negative: int) -> tuple[float, float]:
    total = positive + negative
    if total == 0:
        return 1.0, 0.0
    tail = min(positive, negative)
    denominator_log = total * math.log(2.0)
    log_terms = [
        math.lgamma(total + 1)
        - math.lgamma(count + 1)
        - math.lgamma(total - count + 1)
        - denominator_log
        for count in range(tail + 1)
    ]
    max_log_term = max(log_terms)
    log_tail = max_log_term + math.log(
        sum(math.exp(value - max_log_term) for value in log_terms)
    )
    log_probability = math.log(2.0) + log_tail
    log10_probability = log_probability / math.log(10.0)
    if log_probability >= 0:
        probability = 1.0
    elif log_probability < -745.0:
        probability = 0.0
    else:
        probability = float(f"{math.exp(log_probability):.3g}")
    return probability, round(log10_probability, 6)


def _paired_summary(differences: list[float]) -> dict[str, float]:
    distribution = _distribution(differences)
    positive = sum(value > 0 for value in differences)
    negative = sum(value < 0 for value in differences)
    sign_test_p, sign_test_log10_p = _sign_test(positive, negative)
    return {
        **distribution,
        "positive_rate": round(positive / len(differences), 6),
        "sign_test_p": sign_test_p,
        "sign_test_log10_p": sign_test_log10_p,
    }


def _strategy_metric_summary(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    return {
        "total_return": _distribution([float(row[f"{prefix}_total_return"]) for row in rows]),
        "annual_return": _distribution([float(row[f"{prefix}_annual_return"]) for row in rows]),
        "sharpe": _distribution([float(row[f"{prefix}_sharpe"]) for row in rows]),
        "max_drawdown": {
            **_distribution([float(row[f"{prefix}_max_drawdown"]) for row in rows]),
            "worst": round(min(float(row[f"{prefix}_max_drawdown"]) for row in rows), 6),
        },
        "turnover": _distribution([float(row[f"{prefix}_turnover"]) for row in rows]),
        "n_trades": _distribution([float(row[f"{prefix}_n_trades"]) for row in rows]),
    }


def _group_rows(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(tuple(row[field] for field in fields), []).append(row)
    return grouped


def _expected_cell_keys() -> list[tuple[str, float, str, str, int]]:
    return [
        (scenario, beta, mode, stop_variant, cost)
        for scenario in SCENARIOS
        for beta in CORRELATIONS
        for mode in SIGNAL_MODES
        for stop_variant, _stop_loss in STOP_VARIANTS
        for cost in COST_MULTIPLIERS
    ]


def aggregate_observations(
    observations: list[dict[str, Any]],
    *,
    expected_paths: int,
) -> dict[str, Any]:
    if expected_paths < 1:
        raise ValueError("expected_paths must be positive")
    fields = ("scenario_id", "beta", "signal_mode", "stop_variant", "cost_multiplier")
    grouped = _group_rows(observations, fields)
    expected_keys = set(_expected_cell_keys())
    extra = set(grouped) - expected_keys
    missing = expected_keys - set(grouped)
    if extra:
        raise ValueError(f"unexpected aggregate cells: {sorted(extra)[:3]}")
    if missing:
        raise ValueError(f"missing aggregate cells: {sorted(missing)[:3]}")

    cells: list[dict[str, Any]] = []
    for key in _expected_cell_keys():
        rows = grouped[key]
        path_ids = [int(row["path_id"]) for row in rows]
        if len(set(path_ids)) != len(path_ids):
            raise ValueError(f"duplicate path ids in cell: {key}")
        if sorted(path_ids) != list(range(expected_paths)):
            raise ValueError(
                f"path count for cell {key} must be exactly {expected_paths}; got {len(path_ids)}"
            )
        rows = sorted(rows, key=lambda row: int(row["path_id"]))
        excess = [float(row["excess_return"]) for row in rows]
        scenario, beta, mode, stop_variant, cost = key
        cells.append({
            "scenario_id": scenario,
            "beta": beta,
            "signal_mode": mode,
            "stop_variant": stop_variant,
            "stop_loss_pct": next(
                row["stop_loss_pct"] for row in rows if row["stop_variant"] == stop_variant
            ),
            "cost_multiplier": cost,
            "paths": len(rows),
            "strategy": _strategy_metric_summary(rows, "strategy"),
            "benchmark": _strategy_metric_summary(rows, "benchmark"),
            "excess_return": {
                **_distribution(excess),
                "positive_rate": round(sum(value > 0 for value in excess) / len(excess), 6),
                "sign_test_p": _sign_test(
                    sum(value > 0 for value in excess),
                    sum(value < 0 for value in excess),
                )[0],
                "sign_test_log10_p": _sign_test(
                    sum(value > 0 for value in excess),
                    sum(value < 0 for value in excess),
                )[1],
            },
        })

    by_signal: list[dict[str, Any]] = []
    signal_groups = _group_rows(observations, ("signal_mode",))
    for mode in SIGNAL_MODES:
        rows = signal_groups[(mode,)]
        by_signal.append({
            "signal_mode": mode,
            "observations": len(rows),
            "excess_return": _paired_summary([float(row["excess_return"]) for row in rows]),
        })

    by_scenario: list[dict[str, Any]] = []
    scenario_groups = _group_rows(observations, ("scenario_id",))
    for scenario in SCENARIOS:
        rows = scenario_groups[(scenario,)]
        by_scenario.append({
            "scenario_id": scenario,
            "observations": len(rows),
            "excess_return": _paired_summary([float(row["excess_return"]) for row in rows]),
        })

    by_beta: list[dict[str, Any]] = []
    beta_groups = _group_rows(observations, ("beta",))
    for beta in CORRELATIONS:
        rows = beta_groups[(beta,)]
        by_beta.append({
            "beta": beta,
            "observations": len(rows),
            "excess_return": _paired_summary([float(row["excess_return"]) for row in rows]),
        })

    by_cost: list[dict[str, Any]] = []
    cost_groups = _group_rows(observations, ("cost_multiplier",))
    for cost in COST_MULTIPLIERS:
        rows = cost_groups[(cost,)]
        by_cost.append({
            "cost_multiplier": cost,
            "observations": len(rows),
            "excess_return": _paired_summary([float(row["excess_return"]) for row in rows]),
        })

    stop_comparisons: list[dict[str, Any]] = []
    stop_groups = _group_rows(observations, ("scenario_id", "beta", "signal_mode", "cost_multiplier", "path_id"))
    for scenario in SCENARIOS:
        for beta in CORRELATIONS:
            for mode in SIGNAL_MODES:
                for cost in COST_MULTIPLIERS:
                    differences = []
                    for path_id in range(expected_paths):
                        no_stop = stop_groups[(scenario, beta, mode, cost, path_id)]
                        by_variant = {row["stop_variant"]: row for row in no_stop}
                        differences.append(
                            float(by_variant["no_stop"]["strategy_total_return"])
                            - float(by_variant["stop_12"]["strategy_total_return"])
                        )
                    stop_comparisons.append({
                        "scenario_id": scenario,
                        "beta": beta,
                        "signal_mode": mode,
                        "cost_multiplier": cost,
                        "no_stop_minus_stop": _paired_summary(differences),
                    })

    correlation_comparisons: list[dict[str, Any]] = []
    correlation_groups = _group_rows(observations, ("scenario_id", "signal_mode", "stop_variant", "cost_multiplier", "path_id"))
    for scenario in SCENARIOS:
        for mode in SIGNAL_MODES:
            for stop_variant, _stop in STOP_VARIANTS:
                for cost in COST_MULTIPLIERS:
                    differences = []
                    for path_id in range(expected_paths):
                        rows = correlation_groups[(scenario, mode, stop_variant, cost, path_id)]
                        by_beta_row = {float(row["beta"]): row for row in rows}
                        differences.append(
                            float(by_beta_row[0.7]["strategy_total_return"])
                            - float(by_beta_row[0.0]["strategy_total_return"])
                        )
                    correlation_comparisons.append({
                        "scenario_id": scenario,
                        "signal_mode": mode,
                        "stop_variant": stop_variant,
                        "cost_multiplier": cost,
                        "common_minus_independent": _paired_summary(differences),
                    })

    cost_comparisons: list[dict[str, Any]] = []
    cost_groups = _group_rows(observations, ("scenario_id", "beta", "signal_mode", "stop_variant", "path_id"))
    monotonic_cells = 0
    for scenario in SCENARIOS:
        for beta in CORRELATIONS:
            for mode in SIGNAL_MODES:
                for stop_variant, _stop in STOP_VARIANTS:
                    differences = []
                    monotonic_paths = 0
                    for path_id in range(expected_paths):
                        rows = cost_groups[(scenario, beta, mode, stop_variant, path_id)]
                        by_cost_row = {int(row["cost_multiplier"]): row for row in rows}
                        returns = [
                            float(by_cost_row[2]["strategy_total_return"]),
                            float(by_cost_row[3]["strategy_total_return"]),
                            float(by_cost_row[5]["strategy_total_return"]),
                        ]
                        monotonic_paths += returns[0] >= returns[1] >= returns[2]
                        differences.append(returns[0] - returns[2])
                    if monotonic_paths == expected_paths:
                        monotonic_cells += 1
                    cost_comparisons.append({
                        "scenario_id": scenario,
                        "beta": beta,
                        "signal_mode": mode,
                        "stop_variant": stop_variant,
                        "two_x_minus_five_x": _paired_summary(differences),
                        "path_monotonic_rate": round(
                            monotonic_paths / expected_paths,
                            6,
                        ),
                    })

    min_paths = min(int(cell["paths"]) for cell in cells)
    return {
        "expected_cell_count": len(expected_keys),
        "observed_cell_count": len(cells),
        "expected_observations": len(expected_keys) * expected_paths,
        "observed_observations": len(observations),
        "min_paths_per_cell": min_paths,
        "paths_per_cell": expected_paths,
        "cells": cells,
        "summary_by_signal": by_signal,
        "summary_by_scenario": by_scenario,
        "summary_by_beta": by_beta,
        "summary_by_cost": by_cost,
        "stop_comparisons": stop_comparisons,
        "correlation_comparisons": correlation_comparisons,
        "cost_comparisons": cost_comparisons,
        "fully_monotonic_cost_cells": monotonic_cells,
        "fully_monotonic_cost_cell_rate": round(
            monotonic_cells / (len(SCENARIOS) * len(CORRELATIONS) * len(SIGNAL_MODES) * len(STOP_VARIANTS)),
            6,
        ),
    }


def realize_path_market_metrics(realized_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = _group_rows(realized_rows, ("scenario_id", "beta"))
    output = []
    for scenario in SCENARIOS:
        for beta in CORRELATIONS:
            rows = grouped.get((scenario, beta), [])
            if not rows:
                continue
            output.append({
                "scenario_id": scenario,
                "beta": beta,
                "paths": len(rows),
                "equal_weight_annual_return": _distribution([
                    float(row["equal_weight_annual_return"]) for row in rows
                ]),
                "equal_weight_annual_volatility": _distribution([
                    float(row["equal_weight_annual_volatility"]) for row in rows
                ]),
            })
    return output
