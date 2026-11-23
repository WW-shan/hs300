#!/usr/bin/env python3
"""R495: strict R418 admitted five-factor tail confirmation."""

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
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r495_admitted_five_factor_tail_confirmation_preregistration.json"
OUTPUT = E / "r495_admitted_five_factor_tail_confirmation_2026-11-22.json"
RESULT = E / "r495_admitted_five_factor_tail_confirmation_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R495 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def member_weights() -> tuple[dict[str, float], dict[str, str]]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    members = prereg["members"]
    return {member: 1.0 / len(members) for member in members}, dict(members)


def block_bootstrap(curve: list[dict[str, Any]], *, seed: int) -> dict[str, float]:
    values = [
        float(curve[i]["value"]) / float(curve[i - 1]["value"]) - 1.0 for i in range(1, len(curve))
    ]
    rng = random.Random(seed)
    totals, sharpes, mdds = [], [], []
    for _ in range(5000):
        sampled = []
        while len(sampled) < len(values):
            start = rng.randrange(len(values))
            sampled.extend(values[(start + offset) % len(values)] for offset in range(20))
        selected = sampled[: len(values)]
        product = math.prod(1.0 + item for item in selected)
        mean = sum(selected) / len(selected)
        vol = (sum((item - mean) ** 2 for item in selected) / len(selected)) ** 0.5
        path = [1.0]
        for item in selected:
            path.append(path[-1] * (1.0 + item))
        peak = path[0]
        mdd = 0.0
        for item in path:
            peak = max(peak, item)
            mdd = min(mdd, item / peak - 1.0)
        totals.append(product - 1.0)
        sharpes.append(mean / vol * math.sqrt(252.0) if vol else 0.0)
        mdds.append(mdd)

    def q(items: list[float], fraction: float) -> float:
        return sorted(items)[min(len(items) - 1, int(fraction * len(items)))]

    return {
        "total_p05": q(totals, 0.05),
        "total_p50": q(totals, 0.5),
        "sharpe_p50": q(sharpes, 0.5),
        "mdd_p05": q(mdds, 0.05),
    }


def hs300_return(start: str, end: str) -> float:
    paths = sorted((ROOT / "data" / "kline_index_daily").glob("date=*/part.parquet"))
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


def run_sample(
    p5: Any,
    service: Any,
    panel: pl.DataFrame,
    symbols: list[str],
    weights: dict[str, float],
    directions: dict[str, str],
    *,
    start: str,
    end: str,
    cost_label: str,
) -> dict[str, Any]:
    from app.factor_research.incremental import build_rank_composite

    field = "_r495_admitted_five"
    if field not in panel.columns:
        panel, _ = build_rank_composite(
            panel, weights, directions, field, minimum_weight_coverage=0.75
        )
    config = p5.portfolio._backtest_config(
        {field: 1.0},
        {field: "high"},
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=10,
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
        max_hold_days=40,
    )
    config.params = {
        **(config.params or {}),
        "entry_score": 70.0,
        "exit_score": 40.0,
        "top_rank": 20,
    }
    config.max_exposure_pct = 1.0
    config.max_new_positions_per_day = 1
    config.symbols = list(symbols)
    prepared_base = service.prepare_matrix_optimization([config])
    prepared = p5._extend_prepared_with_panel(prepared_base, panel, [field])
    result = service.run_with_evidence(config, prepared=prepared)
    require_formal_evidence(result, label="research_r495_admitted_five_factor_tail_confirmation.py")
    if result.error:
        raise RuntimeError(result.error)
    return {
        "stats": p5._select_stats(result.stats),
        "curve": list(result.equity_curve),
        "trade_count": len(result.trades),
    }


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r468 = load_module(
        ROOT / "scripts" / "research_r468_operating_efficiency_sleeve.py", "r495_r468"
    )
    r464 = r468.load_module(
        ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r495_r464"
    )
    p5 = r464.load_p5()
    panel, _ = r464.build_panel(p5)
    from app.hs300.current import current_symbols

    price = p5.load_candidate_panel(ROOT / "data", current_symbols(), [])[0].with_columns(
        (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1.0).alias("momentum_60d")
    )
    panel = panel.join(
        price.select(["symbol", "date", "momentum_60d"]), on=["symbol", "date"], how="left"
    ).sort(["symbol", "date"])
    from app.factor_research.extended_parameter_data import load_existing_ext_factors

    ext = load_existing_ext_factors(
        ROOT / "data", {"ext_alt_hs300_vol_of_vol_60d_low": "vol_of_vol_60d_low"}
    )
    panel = panel.join(ext, on=["symbol", "date"], how="left").sort(["symbol", "date"])
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot

    snapshot = load_fundamental_snapshot(ROOT / "data")
    missing = [member for member in prereg["members"] if member not in panel.columns]
    if missing:
        panel = attach_fundamental_factors(panel, snapshot, missing)
    weights, directions = member_weights()
    windows = prereg["windows"]
    service = p5.portfolio._service()
    symbols = sorted(panel["symbol"].unique().to_list())
    result = {}
    for window in ("train", "validation", "test", "recent"):
        result[window] = run_sample(
            p5,
            service,
            panel,
            symbols,
            weights,
            directions,
            start=windows[f"{window}_start"],
            end=windows["test_end"] if window == "recent" else windows[f"{window}_end"],
            cost_label="2x",
        )
    result["test_3x"] = run_sample(
        p5,
        service,
        panel,
        symbols,
        weights,
        directions,
        start=windows["test_start"],
        end=windows["test_end"],
        cost_label="3x",
    )
    result["test_5x"] = run_sample(
        p5,
        service,
        panel,
        symbols,
        weights,
        directions,
        start=windows["test_start"],
        end=windows["test_end"],
        cost_label="5x",
    )
    result["validation_bootstrap"] = block_bootstrap(result["validation"]["curve"], seed=495)
    result["test_bootstrap"] = block_bootstrap(result["test"]["curve"], seed=496)
    result["recent_bootstrap"] = block_bootstrap(result["recent"]["curve"], seed=497)
    hs300_test = hs300_return(windows["test_start"], windows["test_end"])
    hs300_recent = hs300_return(windows["recent_start"], windows["test_end"])
    checks = {
        "train_annual_min": result["train"]["stats"]["annual_return"]
        >= prereg["confirmation_gates"]["train_annual_min"],
        "validation_annual_min": result["validation"]["stats"]["annual_return"]
        >= prereg["confirmation_gates"]["validation_annual_min"],
        "validation_sharpe_min": result["validation"]["stats"]["sharpe"]
        >= prereg["confirmation_gates"]["validation_sharpe_min"],
        "validation_mdd_min": result["validation"]["stats"]["max_drawdown"]
        >= prereg["confirmation_gates"]["validation_mdd_min"],
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
        "round": "R495",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "selection_basis": prereg["selection"]["basis"],
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "benchmark": prereg["benchmark"],
        "benchmark_returns": {"test": hs300_test, "recent": hs300_recent},
        "weights": weights,
        "directions": directions,
        "result": {
            k: (
                {kk: vv for kk, vv in v.items() if kk != "curve"}
                if isinstance(v, dict) and "curve" in v
                else v
            )
            for k, v in result.items()
        },
        "gate": {"pass": all(checks.values()), "checks": checks},
        "diagnostics": {"five_x_warning_only": True, "no_leverage": True},
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
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    st = result["test"]["stats"]
    rc = result["recent"]["stats"]
    lines = [
        "# R495 严格准入五因子尾部确认",
        "",
        (
            "本轮只使用 R418 严格准入的五个因子，执行参数沿用 R419；"
            "训练+验证、检验、近一年和 bootstrap 全部确认。"
        ),
        "",
        f"- 组合腿数：{len(weights)}",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        (
            f"- 检验年化：{st['annual_return']:.2%}；"
            f"Sharpe：{st['sharpe']:.3f}；MDD：{st['max_drawdown']:.2%}"
        ),
        (
            f"- 近一年年化：{rc['annual_return']:.2%}；"
            f"Sharpe：{rc['sharpe']:.3f}；MDD：{rc['max_drawdown']:.2%}"
        ),
        f"- 检验相对 HS300：{st['total_return'] - hs300_test:+.2%}",
        f"- 近一年相对 HS300：{rc['total_return'] - hs300_recent:+.2%}",
        f"- 检验 p05：{result['test_bootstrap']['total_p05']:.2%}",
        f"- 近一年 p05：{result['recent_bootstrap']['total_p05']:.2%}",
        "",
        "## 门槛",
        "",
    ]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii"
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
