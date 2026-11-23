#!/usr/bin/env python3
"""R506: frozen cap-1 confirmation for the R499 sleeve."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.factor_research.evidence_guard import require_formal_evidence

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r506_r499_cap1_confirmation_preregistration.json"
R499 = E / "r499_weak_state_value_quality_tilt_2026-11-22.json"
OUTPUT = E / "r506_r499_cap1_confirmation_2026-11-22.json"
RESULT = E / "r506_r499_cap1_confirmation_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R506 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def weights() -> dict[str, float]:
    return {
        "ext_alt_hs300_cash_flow_stability_4q_low": 0.2375,
        "ext_alt_hs300_dividend_yield": 0.2375,
        "ext_alt_hs300_fund_holding_ratio_low": 0.2375,
        "ext_alt_hs300_idio_vol_40d_low": 0.2375,
        "value_quality": 0.05,
    }


def fixed_cap() -> int:
    return 1


def block_bootstrap(
    curve: list[dict[str, Any]], *, seed: int, block: int = 20, paths: int = 5000
) -> dict[str, float]:
    values = [
        float(curve[index]["value"]) / float(curve[index - 1]["value"]) - 1.0
        for index in range(1, len(curve))
        if float(curve[index - 1].get("value") or 0.0) > 0.0
    ]
    if not values:
        return {"total_p05": 0.0, "total_p50": 0.0, "sharpe_p50": 0.0, "mdd_p05": 0.0}
    rng = random.Random(seed)
    totals, sharpes, mdds = [], [], []
    for _ in range(paths):
        sampled: list[float] = []
        while len(sampled) < len(values):
            start = rng.randrange(len(values))
            sampled.extend(values[(start + offset) % len(values)] for offset in range(block))
        selected = sampled[: len(values)]
        product = math.prod(1.0 + value for value in selected)
        mean = sum(selected) / len(selected)
        vol = (sum((value - mean) ** 2 for value in selected) / len(selected)) ** 0.5
        path = [1.0]
        for value in selected:
            path.append(path[-1] * (1.0 + value))
        peak = path[0]
        mdd = 0.0
        for value in path:
            peak = max(peak, value)
            mdd = min(mdd, value / peak - 1.0)
        totals.append(product - 1.0)
        sharpes.append(mean / vol * math.sqrt(252.0) if vol else 0.0)
        mdds.append(mdd)

    def ordered(items: list[float], q: float) -> float:
        return sorted(items)[min(len(items) - 1, int(q * len(items)))]

    return {
        "total_p05": ordered(totals, 0.05),
        "total_p50": ordered(totals, 0.50),
        "sharpe_p50": ordered(sharpes, 0.50),
        "mdd_p05": ordered(mdds, 0.05),
    }


def hs300_return(start: str, end: str) -> float:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    frame = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(
            (pl.col("symbol") == "000300.SH")
            & (pl.col("date") >= date.fromisoformat(start))
            & (pl.col("date") <= date.fromisoformat(end))
            & pl.col("close").is_not_null()
            & (pl.col("close") > 0)
        )
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    return round(float(frame["close"][-1]) / float(frame["close"][0]) - 1.0, 6)


def build_panel(p5: Any) -> tuple[pl.DataFrame, list[str]]:
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.factor_research.incremental import rank_score_frame
    from app.hs300.current import current_symbols

    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    panel = attach_fundamental_factors(
        panel, snapshot, ["pb_latest", "ocf_debt", "debt_ratio_latest"]
    )
    ext = load_existing_ext_factors(
        DATA,
        {
            "ext_alt_hs300_cash_flow_stability_4q_low": "cash_flow_stability_4q_low",
            "ext_alt_hs300_dividend_yield": "dividend_yield",
            "ext_alt_hs300_fund_holding_ratio_low": "fund_holding_ratio_low",
            "ext_alt_hs300_idio_vol_40d_low": "idio_vol_40d_low",
        },
    )
    panel = panel.join(ext, on=["symbol", "date"], how="left").sort(["symbol", "date"])
    for member, direction in (
        ("pb_latest", "low"),
        ("ocf_debt", "high"),
        ("debt_ratio_latest", "low"),
    ):
        panel = rank_score_frame(panel, member, direction, output_column=f"_r506_vq_{member}")
    panel = panel.with_columns(
        (
            pl.col("_r506_vq_pb_latest").fill_null(0.0)
            + pl.col("_r506_vq_ocf_debt").fill_null(0.0)
            + pl.col("_r506_vq_debt_ratio_latest").fill_null(0.0)
        ).alias("value_quality"),
        (
            pl.col("_r506_vq_pb_latest").is_not_null()
            & pl.col("_r506_vq_ocf_debt").is_not_null()
            & pl.col("_r506_vq_debt_ratio_latest").is_not_null()
        ).alias("_r506_vq_valid"),
    ).filter(pl.col("_r506_vq_valid"))
    return panel, sorted(panel["symbol"].unique().to_list())


def run_sample(
    p5: Any,
    service: Any,
    panel: pl.DataFrame,
    symbols: list[str],
    prereg: dict[str, Any],
    *,
    start: str,
    end: str,
    cost_label: str,
) -> dict[str, Any]:
    from app.factor_research.incremental import build_rank_composite

    field = "_r506_cap1_composite"
    work, _ = build_rank_composite(
        panel,
        weights(),
        {member: "high" for member in weights()},
        field,
        minimum_weight_coverage=0.75,
    )
    fixed = prereg["fixed_execution"]
    config = p5.portfolio._backtest_config(
        {field: 1.0},
        {field: "high"},
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=int(fixed["max_positions"]),
        cost=(
            p5.portfolio._cost_parameters(cost_label)
            if cost_label != "5x"
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
    config.max_new_positions_per_day = fixed_cap()
    config.symbols = list(symbols)
    prepared_base = service.prepare_matrix_optimization([config])
    prepared = p5._extend_prepared_with_panel(prepared_base, work, [field])
    result = service.run_with_evidence(config, prepared=prepared)
    require_formal_evidence(result, label="research_r506_r499_cap1_confirmation.py")
    if result.error:
        raise RuntimeError(result.error)
    return {
        "stats": p5._select_stats(result.stats),
        "curve": list(result.equity_curve),
        "trades": len(result.trades),
        "execution": result.stats.get("execution") or {},
    }


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    verify_sidecar(R499)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    source = json.loads(R499.read_text(encoding="utf-8"))
    if float(source["selected"]["weak_value_quality"]) != 0.05:
        raise RuntimeError("R499 selected sample changed")
    r453 = load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r506_r453")
    p5 = r453.load_p5()
    panel, symbols = build_panel(p5)
    service = p5.portfolio._service()
    windows = prereg["windows"]
    result = {}
    for window in ("train", "validation"):
        result[window] = run_sample(
            p5,
            service,
            panel,
            symbols,
            prereg,
            start=windows[f"{window}_start"],
            end=windows[f"{window}_end"],
            cost_label="2x",
        )
    result["validation_bootstrap"] = block_bootstrap(result["validation"]["curve"], seed=506)
    for key, start, cost in (
        ("test", windows["test_start"], "2x"),
        ("recent", windows["recent_start"], "2x"),
        ("test_3x", windows["test_start"], "3x"),
        ("test_5x", windows["test_start"], "5x"),
    ):
        result[key] = run_sample(
            p5,
            service,
            panel,
            symbols,
            prereg,
            start=start,
            end=windows["test_end"],
            cost_label=cost,
        )
    result["test_bootstrap"] = block_bootstrap(result["test"]["curve"], seed=507)
    result["recent_bootstrap"] = block_bootstrap(result["recent"]["curve"], seed=508)
    hs300_test = hs300_return(windows["test_start"], windows["test_end"])
    hs300_recent = hs300_return(windows["recent_start"], windows["test_end"])
    checks = {
        "validation_bootstrap_positive": result["validation_bootstrap"]["total_p05"] > 0.0,
        "test_annual_min": result["test"]["stats"]["annual_return"]
        >= prereg["confirmation_gates"]["test_annual_min"],
        "test_sharpe_min": result["test"]["stats"]["sharpe"]
        >= prereg["confirmation_gates"]["test_sharpe_min"],
        "test_mdd_min": result["test"]["stats"]["max_drawdown"]
        >= prereg["confirmation_gates"]["test_mdd_min"],
        "recent_annual_min": result["recent"]["stats"]["annual_return"]
        >= prereg["confirmation_gates"]["recent_annual_min"],
        "stress_3x_positive": result["test_3x"]["stats"]["annual_return"] > 0.0,
        "test_bootstrap_positive": result["test_bootstrap"]["total_p05"] > 0.0,
        "recent_bootstrap_positive": result["recent_bootstrap"]["total_p05"] > 0.0,
        "test_excess_hs300_positive": result["test"]["stats"]["total_return"] > hs300_test,
        "no_leverage": True,
    }
    payload = {
        "round": "R506",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_r499_sha256": sha(R499),
        "selected_variant": prereg["selected_variant"],
        "selection_performed": False,
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "result": {
            key: (
                {k: v for k, v in value.items() if k != "curve"}
                if isinstance(value, dict)
                else value
            )
            for key, value in result.items()
        },
        "benchmark": prereg["benchmark"],
        "benchmark_returns": {"test": hs300_test, "recent": hs300_recent},
        "gate": {"pass": all(checks.values()), "checks": checks},
        "decision": {
            "status": "watch_candidate" if all(checks.values()) else "rejected",
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    st = result["test"]["stats"]
    rc = result["recent"]["stats"]
    lines = (
        [
            "# R506 R499 Cap-1 独立确认",
            "",
            "- 本轮不重新选样，只确认 `cap_1`。",
            f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
            f"- 验证 p05：{result['validation_bootstrap']['total_p05']:.2%}",
            f"- 检验年化：{st['annual_return']:.2%}；Sharpe：{st['sharpe']:.3f}；MDD：{st['max_drawdown']:.2%}",
            f"- 近一年年化：{rc['annual_return']:.2%}；Sharpe：{rc['sharpe']:.3f}；MDD：{rc['max_drawdown']:.2%}",
            f"- 检验 p05：{result['test_bootstrap']['total_p05']:.2%}",
            f"- 近一年 p05：{result['recent_bootstrap']['total_p05']:.2%}",
            "",
            "## 门槛",
            "",
        ]
        + [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
        + ["", "## 边界", ""]
        + [f"- {item}" for item in prereg["known_limits"]]
        + [""]
    )
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii"
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
