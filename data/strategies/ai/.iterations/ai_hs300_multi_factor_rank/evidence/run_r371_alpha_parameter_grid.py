"""Run the preregistered 27-cell alpha-parameter neighbourhood around R360."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import shutil
import statistics as st
import sys
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
RESEARCH = DATA / "research"
R40 = RESEARCH / "r40_engine_data"
PREREGISTRATION = EVIDENCE / "r371_alpha_grid_preregistration.json"
R360_RESULT = EVIDENCE / "r360_industry_fade_backtest.json"
BASE_STRATEGY = EVIDENCE / "r360_strategies/ai_hs300_industry_fade_quality_h20_sl20.py"
VARIANTS = EVIDENCE / "r371_variants"
CACHE = RESEARCH / "r371_cache"
OUTPUT = EVIDENCE / "r371_alpha_parameter_grid.json"
RESULT_MD = EVIDENCE / "r371_result.md"
START = date(2022, 5, 5)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
FADE_DAYS = (3, 5, 10)
HOLD_DAYS = (10, 20, 30)
STOP_LOSSES = (-0.15, -0.20, -0.25)
A_STRATEGY = "ai_hs300_macro_gate_quality"
C_STRATEGY = "ai_hs300_quality_momentum_broad_h60"

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


def parameter_grid() -> list[tuple[int, int, float]]:
    return [(fade, hold, stop) for fade in FADE_DAYS for hold in HOLD_DAYS for stop in STOP_LOSSES]


def stop_slug(stop_loss: float) -> str:
    return f"sl{round(abs(stop_loss) * 100):02d}"


def cell_slug(fade: int, hold: int, stop: float) -> str:
    return f"fade{fade:02d}_h{hold:02d}_{stop_slug(stop)}"


def factor_id(fade: int) -> str:
    return f"uf_r371_fade_change_{fade}d"


def _marginal(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    grouped: dict[str, list[bool]] = {}
    for row in rows:
        value = str(row[key])
        grouped.setdefault(value, []).append(bool(row["joint_pass"]))
    return {value: sum(passed) / len(passed) for value, passed in sorted(grouped.items())}


def summarise_grid(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("parameter grid cannot be empty")
    pass_rate = sum(bool(row["joint_pass"]) for row in rows) / len(rows)
    marginals = {
        "marginal_fade_days": _marginal(rows, "fade_days"),
        "marginal_hold_days": _marginal(rows, "hold_days"),
        "marginal_stop_loss": _marginal(rows, "stop_loss"),
    }
    minimum_marginal = min(value for rates in marginals.values() for value in rates.values())
    annuals = [float(row["stats"]["annual"]) for row in rows if "stats" in row]
    sharpes = [float(row["stats"]["sharpe"]) for row in rows if "stats" in row]
    mdds = [float(row["stats"]["mdd"]) for row in rows if "stats" in row]
    return {
        "cell_count": len(rows),
        "pass_count": sum(bool(row["joint_pass"]) for row in rows),
        "pass_rate": pass_rate,
        **marginals,
        "minimum_marginal_pass_rate": minimum_marginal,
        "plateau_pass": pass_rate >= 0.75 and minimum_marginal >= 2.0 / 3.0,
        "annual_range": [min(annuals), max(annuals)] if annuals else None,
        "sharpe_range": [min(sharpes), max(sharpes)] if sharpes else None,
        "mdd_range": [min(mdds), max(mdds)] if mdds else None,
    }


def _curve_frame(curve: list[dict[str, Any]]) -> pl.DataFrame:
    rows = []
    for item in curve or []:
        raw = item.get("date")
        day = raw if isinstance(raw, date) else date.fromisoformat(str(raw)[:10])
        rows.append({"date": day, "value": float(item.get("value", 0.0))})
    if not rows:
        raise RuntimeError("empty equity curve")
    return pl.DataFrame(rows).sort("date").unique(subset=["date"], keep="last")


def _stats(curve: pl.DataFrame) -> dict[str, float | int]:
    values = [float(value) for value in curve["value"].to_list()]
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    volatility = st.pstdev(returns)
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {
        "total_return": total,
        "annual": (1.0 + total) ** (252.0 / len(returns)) - 1.0,
        "sharpe": st.mean(returns) / volatility * math.sqrt(252.0) if volatility else 0.0,
        "mdd": mdd,
        "days": len(values),
    }


def _year_returns(curve: pl.DataFrame) -> dict[str, float]:
    grouped: dict[int, list[float]] = {}
    for row in curve.iter_rows(named=True):
        grouped.setdefault(row["date"].year, []).append(float(row["value"]))
    output: dict[str, float] = {}
    previous_end: float | None = None
    for year in sorted(grouped):
        values = grouped[year]
        start_value = previous_end if previous_end is not None else values[0]
        output[str(year)] = values[-1] / start_value - 1.0
        previous_end = values[-1]
    return output


def _segment_return(curve: pl.DataFrame, start: date, end: date = END) -> float | None:
    part = curve.filter(pl.col("date").is_between(start, end)).sort("date")
    if part.height < 2:
        return None
    return float(part["value"][-1] / part["value"][0] - 1.0)


def _membership() -> pl.DataFrame:
    return (
        pl.read_parquet(
            RESEARCH / "r78_daily_membership/daily_snapshots.parquet",
            columns=["query_date", "symbol"],
        )
        .select(pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String))
        .unique()
        .sort(["effective_date", "symbol"])
    )


@contextmanager
def _patched_membership(frame: pl.DataFrame):
    from app.backtest import strategy as strategy_module

    original = strategy_module._hs300_membership_frame
    strategy_module._hs300_membership_frame = lambda _start, _end: frame
    try:
        yield
    finally:
        strategy_module._hs300_membership_frame = original


def _write_variant_definitions() -> dict[str, Any]:
    strategy_dir = VARIANTS / "strategies"
    factor_dir = VARIANTS / "custom_factors"
    signal_dir = VARIANTS / "custom_signals"
    for directory in (strategy_dir, factor_dir, signal_dir):
        directory.mkdir(parents=True, exist_ok=True)
        for path in directory.iterdir():
            if path.is_file():
                path.unlink()
    base_text = BASE_STRATEGY.read_text(encoding="utf-8")
    definitions: dict[str, Any] = {"strategies": {}, "factors": {}, "signals": {}}
    for fade in FADE_DAYS:
        current_factor_id = factor_id(fade)
        signal_id = f"r371_rel_strength_fade_{fade}"
        signal_column = f"csg_{signal_id}"
        strategy_id = f"r371_industry_fade_{fade}d_quality_h20_sl20"
        factor = {
            "id": current_factor_id,
            "kind": "custom",
            "version": 1,
            "label": f"R371 行业相对强弱{fade}日变化",
            "group": "行业动量",
            "formula": f"ts_delta(ext_alt_hs300_stock_rel_industry_20d, {fade})",
            "description": f"当前行业相对强弱减去{fade}个交易日前的值；R371参数邻域研究。",
            "direction": "none",
            "status": "draft",
        }
        signal = {
            "id": signal_id,
            "name": f"R371 行业内相对强弱转负且{fade}日走弱",
            "kind": "exit",
            "timeframe": "daily",
            "enabled": True,
            "conditions": [
                {
                    "left": "ext_alt_hs300_stock_rel_industry_20d",
                    "op": "<",
                    "right": "0",
                    "leftDays": 0,
                    "rightDays": 0,
                },
                {
                    "left": current_factor_id,
                    "op": "<",
                    "right": "0",
                    "leftDays": 0,
                    "rightDays": 0,
                },
            ],
            "column": signal_column,
        }
        strategy_text = base_text.replace(
            "ai_hs300_industry_fade_quality_h20_sl20", strategy_id
        ).replace(
            "uf_industry_relative_strength_change_5d", current_factor_id
        ).replace(
            "csg_r360_rel_strength_fade", signal_column
        ).replace(
            "行业相对强弱转负且五日继续走弱时退出", f"行业相对强弱转负且{fade}日继续走弱时退出"
        )
        factor_path = factor_dir / f"{current_factor_id}.json"
        signal_path = signal_dir / f"{signal_id}.json"
        strategy_path = strategy_dir / f"{strategy_id}.py"
        factor_path.write_text(json.dumps(factor, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        signal_path.write_text(json.dumps(signal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        strategy_path.write_text(strategy_text, encoding="utf-8")
        definitions["strategies"][str(fade)] = {
            "id": strategy_id,
            "path": strategy_path.relative_to(ROOT).as_posix(),
            "sha256": sha256(strategy_path),
        }
        definitions["factors"][str(fade)] = {
            "id": current_factor_id,
            "path": factor_path.relative_to(ROOT).as_posix(),
            "sha256": sha256(factor_path),
        }
        definitions["signals"][str(fade)] = {
            "id": signal_id,
            "column": signal_column,
            "path": signal_path.relative_to(ROOT).as_posix(),
            "sha256": sha256(signal_path),
        }
    return definitions


def _cache_metadata(
    *,
    strategy_id: str,
    fade: int,
    hold: int,
    stop: float,
    membership_sha256: str,
    strategy_sha256: str,
) -> dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "fade_days": fade,
        "hold_days": hold,
        "stop_loss": stop,
        "start": str(START),
        "end": str(END),
        "fees_pct": 0.0004,
        "commission_pct": 0.0004,
        "stamp_tax_pct": 0.0005,
        "slippage_bps": 10.0,
        "matching": "open_t+1",
        "max_positions": 10,
        "membership_sha256": membership_sha256,
        "strategy_sha256": strategy_sha256,
    }


def _run_grid() -> dict[str, Any]:
    prereg = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    definitions = _write_variant_definitions()
    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
    from app.config import settings
    from app.factors.store import load_into_registry
    from app.indicators import pipeline
    from app.services.industry_map import industry_loader_for
    from app.strategy.engine import StrategyEngine
    from app.tickflow.repository import DataStore, KlineRepository

    r331 = module(EVIDENCE / "run_r331_three_asset_bucket.py", "r371_r331")
    r344 = module(EVIDENCE / "run_r344_extended_window.py", "r371_r344")
    r359 = module(EVIDENCE / "run_r359_cohort_ensemble.py", "r371_r359")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r371_r112")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r371_r114")
    r360_isolation = module(EVIDENCE / "r360_isolation.py", "r371_isolation")
    r370 = module(EVIDENCE / "run_r370_monte_carlo_parameter_audit.py", "r371_r370")
    work = Path(tempfile.mkdtemp(prefix="r371_engine_data_"))
    try:
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
        isolation = r360_isolation.prepare_isolated_data_root(engine_root, DATA)
        for name in ("custom_factors", "custom_signals"):
            for source in sorted((VARIANTS / name).glob("*")):
                shutil.copy2(source, engine_root / "user_data" / name / source.name)
        settings.data_dir = engine_root
        load_into_registry(engine_root)
        pipeline.invalidate_custom_signals()
        engine = StrategyEngine(
            strategy_dirs=[DATA / "strategies/ai", VARIANTS / "strategies"],
            override_loader=lambda _sid: {},
            industry_loader=industry_loader_for(engine_root),
        )
        service = StrategyBacktestService(
            BacktestEngine(KlineRepository(DataStore(engine_root))), engine
        )
        membership_frame = _membership()
        membership_hash = hashlib.sha256(
            membership_frame.write_csv().encode("utf-8")
        ).hexdigest()
        a = (
            pl.read_parquet(RESEARCH / "r344_cache/ai_hs300_macro_gate_quality_2022-05-05_2x_p10.parquet")
            .with_columns(pl.col("date").cast(pl.Date))
            .sort("date")
        )
        sessions = (
            pl.read_parquet(
                RESEARCH / "r344_cache/ai_hs300_quality_momentum_broad_h60_2022-05-05_2x_p50.parquet",
                columns=["date"],
            )["date"]
            .cast(pl.Date)
            .to_list()
        )
        cohort_starts = r359.cohort_start_dates(sessions, START)
        cohort_curves = [
            pl.read_parquet(
                RESEARCH
                / f"r359_v2_cache/ai_hs300_quality_momentum_broad_h60_{cohort_start}_2x_p50.parquet"
            )
            .with_columns(pl.col("date").cast(pl.Date))
            .sort("date")
            for cohort_start in cohort_starts
        ]
        c = r359.combine_cohort_curves(cohort_curves, cohort_starts)
        gold = r331.asset_frame("gold", "518880.SH")
        treasury = r331.asset_frame("treasury", "511260.SH")
        dividend = r331.asset_frame("dividend", "510880.SH")
        gold_proxy = r344.gold_trend_proxy(gold, treasury, dividend, 0.001)
        CACHE.mkdir(parents=True, exist_ok=True)
        rows: list[dict[str, Any]] = []
        cache_manifest: dict[str, Any] = {}
        old_114 = (r114.TARGET_VOL, r114.ROUND_TRIP_COST)
        old_331 = (r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX)
        try:
            r114.TARGET_VOL = 0.15
            r114.ROUND_TRIP_COST = 0.0052
            r331.LOOKBACK = 60
            r331.TARGET_VOL = 0.15
            r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
            for fade, hold, stop in parameter_grid():
                slug = cell_slug(fade, hold, stop)
                strategy_id = definitions["strategies"][str(fade)]["id"]
                strategy_hash = definitions["strategies"][str(fade)]["sha256"]
                expected_meta = _cache_metadata(
                    strategy_id=strategy_id,
                    fade=fade,
                    hold=hold,
                    stop=stop,
                    membership_sha256=membership_hash,
                    strategy_sha256=strategy_hash,
                )
                curve_path = CACHE / f"{slug}.parquet"
                meta_path = CACHE / f"{slug}.meta.json"
                if curve_path.exists() or meta_path.exists():
                    if not curve_path.exists() or not meta_path.exists():
                        raise RuntimeError(f"R371 incomplete cache entry: {slug}")
                    if json.loads(meta_path.read_text(encoding="utf-8")) != expected_meta:
                        raise RuntimeError(f"R371 cache metadata mismatch: {slug}")
                    curve = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
                else:
                    config = StrategyBacktestConfig(
                        strategy_id=strategy_id,
                        symbols=None,
                        start=START,
                        end=END,
                        params={},
                        overrides={"max_hold_days": hold, "stop_loss": stop},
                        matching="open_t+1",
                        fees_pct=0.0004,
                        commission_pct=0.0004,
                        stamp_tax_pct=0.0005,
                        slippage_bps=10.0,
                        holding_days=hold,
                        max_positions=10,
                        max_exposure_pct=1.0,
                        initial_capital=1_000_000.0,
                        position_sizing="equal",
                        mode="position",
                        asset_type="stock",
                        hs300=True,
                    )
                    with _patched_membership(membership_frame):
                        result = service.run(config)
                    if result.error:
                        raise RuntimeError(f"R371 {slug}: {result.error}")
                    curve = _curve_frame(result.equity_curve)
                    if curve["date"].min() != START or curve["date"].max() != END:
                        raise RuntimeError(f"R371 date coverage mismatch: {slug}")
                    curve.write_parquet(curve_path, compression="zstd")
                    meta_path.write_text(
                        json.dumps(expected_meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                cache_manifest[slug] = {
                    "curve_sha256": sha256(curve_path),
                    "meta_sha256": sha256(meta_path),
                }
                risk, _ = r344._combine_three((a, curve, c), r112.ROUND_TRIP_COST, 0.10)
                equity = r114._vol_target(risk)["curve"]
                final = r331.build_three_asset_bucket(
                    equity,
                    gold_proxy,
                    treasury,
                    dividend,
                    round_trip_cost=0.001,
                )["curve"]
                stats = _stats(final)
                yearly = _year_returns(final)
                recent = _segment_return(final, RECENT_START)
                final_values = [float(value) for value in final["value"].to_list()]
                bootstrap = r370.circular_block_bootstrap(
                    [
                        final_values[index] / final_values[index - 1] - 1.0
                        for index in range(1, len(final_values))
                    ],
                    block_days=20,
                    horizon=252,
                    paths=1000,
                    seed=20261110,
                )
                evaluation = r370.evaluate_parameter_cell(
                    {
                        "metrics": {
                            "stats": stats,
                            "recent": {"total_return": recent},
                            "yearly": yearly,
                            "bootstrap": bootstrap,
                        }
                    }
                )
                rows.append(
                    {
                        "cell": slug,
                        "fade_days": fade,
                        "hold_days": hold,
                        "stop_loss": stop,
                        "standalone_b_stats": _stats(curve),
                        "stats": stats,
                        "yearly": yearly,
                        "recent": recent,
                        "bootstrap": bootstrap,
                        "evaluation": evaluation,
                        "joint_pass": evaluation["joint_pass"],
                    }
                )
                print(
                    f"R371 {slug}: annual={stats['annual']:.4f} sharpe={stats['sharpe']:.3f} "
                    f"mdd={stats['mdd']:.4f} pass={evaluation['joint_pass']}",
                    flush=True,
                )
        finally:
            r114.TARGET_VOL, r114.ROUND_TRIP_COST = old_114
            r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX = old_331
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary = summarise_grid(rows)
    center = next(row for row in rows if (row["fade_days"], row["hold_days"], row["stop_loss"]) == (5, 20, -0.20))
    frozen = json.loads(R360_RESULT.read_text(encoding="utf-8"))["performance"]["candidate"]["2x"]
    center_matches = all(
        math.isclose(float(center["stats"][key]), float(frozen[key]), rel_tol=0.0, abs_tol=1e-9)
        for key in ("annual", "sharpe", "mdd", "total_return")
    ) and int(center["stats"]["days"]) == int(frozen["days"])
    if not center_matches:
        raise RuntimeError("R371 centre cell does not reconstruct frozen R360")
    return {
        "round": "R371",
        "classification": prereg["classification"],
        "preregistration": PREREGISTRATION.relative_to(ROOT).as_posix(),
        "preregistration_sha256": sha256(PREREGISTRATION),
        "variant_definitions": definitions,
        "cache_manifest": cache_manifest,
        "input_isolation": isolation,
        "grid": summary,
        "center_reconstruction_matches_r360": center_matches,
        "rows": rows,
        "decision": {
            "selected_cell": None,
            "no_best_cell_selected": True,
            "no_leverage": True,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }


def _markdown(report: dict[str, Any]) -> str:
    grid = report["grid"]
    lines = [
        "# R371 · R360 核心 alpha 参数 27 格邻域回测",
        "",
        f"结论：27 格中通过 {grid['pass_count']}/{grid['cell_count']}，整体通过率 {grid['pass_rate']:.2%}；最低单参数层通过率 {grid['minimum_marginal_pass_rate']:.2%}。平台门槛：**{grid['plateau_pass']}**。",
        "",
        f"- 年化范围：{grid['annual_range'][0]:.2%} 至 {grid['annual_range'][1]:.2%}。",
        f"- Sharpe 范围：{grid['sharpe_range'][0]:.3f} 至 {grid['sharpe_range'][1]:.3f}。",
        f"- MDD 范围：{grid['mdd_range'][0]:.2%} 至 {grid['mdd_range'][1]:.2%}。",
        f"- 中心格 (5日/20日/-20%) 精确重建冻结 R360：{report['center_reconstruction_matches_r360']}。",
        "",
        "## 边际通过率",
        "",
        "| 参数 | 各档通过率 |",
        "| --- | --- |",
        f"| 行业衰减窗口 | {json.dumps(grid['marginal_fade_days'], ensure_ascii=False)} |",
        f"| 最大持有期 | {json.dumps(grid['marginal_hold_days'], ensure_ascii=False)} |",
        f"| 止损 | {json.dumps(grid['marginal_stop_loss'], ensure_ascii=False)} |",
        "",
        "## 全部 27 格",
        "",
        "| 衰减日 | 持有日 | 止损 | 年化 | Sharpe | MDD | 近一年 | Bootstrap正收益率 | 通过 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['fade_days']} | {row['hold_days']} | {row['stop_loss']:.0%} | "
            f"{row['stats']['annual']:.2%} | {row['stats']['sharpe']:.3f} | {row['stats']['mdd']:.2%} | "
            f"{row['recent']:.2%} | {row['bootstrap']['positive_rate']:.2%} | {row['joint_pass']} |"
        )
    lines.extend(
        [
            "",
            "## 判定边界",
            "",
            "- 这是 R360 周边的邻域测试，不是全参数空间穷举，也不是新的独立样本外证据。",
            "- 27 个单元格高度相关，不能当作 27 次独立试验；本轮不选最佳格。",
            "- 只对所有格重跑 2x 成本；5x 仍只作为中心策略的诊断，不是硬否决。",
            "- 历史行业分类仍是当前快照回填，历史成分与 ETF 复权限制不变。",
            "- 不加杠杆、不生成订单、不启用生产。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    for path in (OUTPUT, OUTPUT.with_name(OUTPUT.name + ".sha256"), RESULT_MD, RESULT_MD.with_name(RESULT_MD.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"R371 output exists; refusing overwrite: {path}")
    report = _run_grid()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha256(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    RESULT_MD.write_text(_markdown(report), encoding="utf-8")
    RESULT_MD.with_name(RESULT_MD.name + ".sha256").write_text(f"{sha256(RESULT_MD)}  {RESULT_MD.name}\n", encoding="ascii")
    print(json.dumps(report["grid"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
