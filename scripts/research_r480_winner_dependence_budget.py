#!/usr/bin/env python3
"""R480: pre-registered 8-leg subsets targeting lower winner dependence."""
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
PREREG = E / "r480_winner_dependence_budget_preregistration.json"
OUTPUT = E / "r480_winner_dependence_budget_2026-11-22.json"
RESULT = E / "r480_winner_dependence_budget_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R480 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def generate_samples() -> list[dict[str, Any]]:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    execution = payload["execution"]
    directions = payload["base_library"]["members"]
    rows = []
    for label in payload["subsets"]:
        members = list(payload["subsets"][label])
        if len(members) != 8:
            raise ValueError(f"subset must have eight legs: {label}")
        weights = {factor: 1.0 / len(members) for factor in members}
        rows.append(
            {
                "label": label,
                "weights": weights,
                "directions": {factor: directions[factor] for factor in members},
                "max_positions": int(execution["max_positions"]),
                "max_hold_days": int(execution["max_hold_days"]),
                "max_exposure_pct": float(execution["max_exposure_pct"]),
                "entry_score": float(execution["entry_score"]),
                "exit_score": float(execution["exit_score"]),
                "top_rank": int(execution["top_rank"]),
            }
        )
    return rows


def top_positive_share(trades: list[dict[str, Any]], top: int = 5) -> float:
    positives = sorted(
        (float(item.get("pnl_amount") or 0.0) for item in trades if float(item.get("pnl_amount") or 0.0) > 0.0),
        reverse=True,
    )
    total = sum(positives)
    return round(sum(positives[:top]) / total, 6) if total > 0.0 else 0.0


def pnl_concentration(trades: list[dict[str, Any]]) -> dict[str, float | int]:
    pnls = sorted((float(item.get("pnl_amount") or 0.0) for item in trades), reverse=True)
    total = sum(pnls)
    return {
        "trades": len(pnls),
        "positive_trades": sum(value > 0 for value in pnls),
        "negative_trades": sum(value < 0 for value in pnls),
        "total_pnl": round(total, 4),
        "top_5_positive_share": top_positive_share(trades),
        "remaining_after_top_5": round(total - sum(pnls[:5]), 4),
    }


def block_bootstrap(curve: list[dict[str, Any]], *, block: int, paths: int, seed: int) -> dict[str, float]:
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
    def ordered(items: list[float], q: float) -> float:
        return sorted(items)[min(len(items) - 1, int(q * len(items)))]

    return {
        "total_p05": ordered(totals, 0.05),
        "total_p50": ordered(totals, 0.5),
        "sharpe_p50": ordered(sharpes, 0.5),
        "mdd_p05": ordered(mdds, 0.05),
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
    if frame.is_empty():
        raise RuntimeError("HS300 benchmark has no rows")
    return round(float(frame["close"][-1]) / float(frame["close"][0]) - 1.0, 6)


def run_sample(
    p5: Any,
    service: Any,
    panel: pl.DataFrame,
    symbols: list[str],
    row: dict[str, Any],
    *,
    start: str,
    end: str,
    cost_label: str,
) -> dict[str, Any]:
    from app.factor_research.incremental import build_rank_composite
    field = f"_r480_variant_{row['label']}"
    if field not in panel.columns:
        panel, _ = build_rank_composite(
            panel,
            row["weights"],
            row["directions"],
            field,
            minimum_weight_coverage=0.75,
        )
    seed_config = p5.portfolio._backtest_config(
        {field: 1.0},
        {field: "high"},
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=int(row["max_positions"]),
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
        max_hold_days=int(row["max_hold_days"]),
    )
    seed_config.params = {
        **(seed_config.params or {}),
        "entry_score": float(row["entry_score"]),
        "exit_score": float(row["exit_score"]),
        "top_rank": int(row["top_rank"]),
    }
    seed_config.max_exposure_pct = float(row["max_exposure_pct"])
    seed_config.symbols = list(symbols)
    prepared_base = service.prepare_matrix_optimization([seed_config])
    prepared = p5._extend_prepared_with_panel(prepared_base, panel, [field])
    result = service.run_with_evidence(seed_config, prepared=prepared)
    require_formal_evidence(result, label="research_r480_winner_dependence_budget.py")
    if result.error:
        raise RuntimeError(result.error)
    return {
        "stats": p5._select_stats(result.stats),
        "curve": list(result.equity_curve),
        "trades": list(result.trades),
        "trade_count": len(result.trades),
    }


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r468 = load_module(ROOT / "scripts" / "research_r468_operating_efficiency_sleeve.py", "r480_r468")
    r464 = r468.load_module(ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r480_r464")
    p5 = r464.load_p5()
    panel, _ignored = r464.build_panel(p5)
    from app.hs300.current import current_symbols
    price_panel = p5.load_candidate_panel(ROOT / "data", current_symbols(), [])[0]
    price_panel = price_panel.with_columns(
        (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1.0).alias("momentum_60d")
    )
    panel = panel.join(
        price_panel.select(["symbol", "date", "momentum_60d"]),
        on=["symbol", "date"],
        how="left",
    ).sort(["symbol", "date"])
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    snapshot = load_fundamental_snapshot(ROOT / "data")
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    candidate_members = sorted({member for subset in prereg["subsets"].values() for member in subset})
    missing = [factor for factor in candidate_members if factor not in panel.columns]
    if missing:
        panel = attach_fundamental_factors(panel, snapshot, missing)
    missing = [factor for factor in candidate_members if factor not in panel.columns]
    if missing:
        raise RuntimeError(f"subset factors unavailable: {missing}")
    symbols = sorted(panel["symbol"].unique().to_list())
    service = p5.portfolio._service()
    windows = prereg["windows"]
    rows = generate_samples()
    for row in rows:
        for window in ("train", "validation"):
            result = run_sample(
                p5, service, panel, symbols, row,
                start=windows[f"{window}_start"], end=windows[f"{window}_end"], cost_label="2x",
            )
            result["dependence"] = pnl_concentration(result["trades"])
            result.pop("trades", None)
            row[window] = result
            row[f"{window}_bootstrap"] = block_bootstrap(
                result["curve"],
                block=int(prereg["bootstrap"]["block_days"]),
                paths=int(prereg["bootstrap"]["replications"]),
                seed=int(prereg["bootstrap"]["seed"]) + (0 if window == "train" else 100),
            )
        row["training_gate"] = bool(
            row["train"]["stats"]["annual_return"] >= prereg["selection"]["train_annual_min"]
            and row["validation"]["stats"]["annual_return"] >= prereg["selection"]["validation_annual_min"]
            and row["validation"]["stats"]["sharpe"] >= prereg["selection"]["validation_sharpe_min"]
            and row["validation"]["stats"]["max_drawdown"] >= prereg["selection"]["validation_mdd_min"]
            and row["validation_bootstrap"]["total_p05"] >= prereg["selection"]["validation_bootstrap_p05_min_for_platform"]
        )
    eligible = [row for row in rows if row["training_gate"]]
    eligible.sort(
        key=lambda row: (
            min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]),
            row["validation_bootstrap"]["total_p05"],
            -float(row["train"]["dependence"]["top_5_positive_share"]),
        ),
        reverse=True,
    )
    selected = eligible[0] if eligible else max(
        rows,
        key=lambda row: min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]),
    )
    selected["test"] = run_sample(
        p5, service, panel, symbols, selected,
        start=windows["test_start"], end=windows["test_end"], cost_label="2x",
    )
    selected["recent"] = run_sample(
        p5, service, panel, symbols, selected,
        start=windows["recent_start"], end=windows["test_end"], cost_label="2x",
    )
    selected["test_3x"] = run_sample(
        p5, service, panel, symbols, selected,
        start=windows["test_start"], end=windows["test_end"], cost_label="3x",
    )
    selected["test_5x"] = run_sample(
        p5, service, panel, symbols, selected,
        start=windows["test_start"], end=windows["test_end"], cost_label="5x",
    )
    selected["test_bootstrap"] = block_bootstrap(
        selected["test"]["curve"],
        block=int(prereg["bootstrap"]["block_days"]),
        paths=int(prereg["bootstrap"]["replications"]),
        seed=int(prereg["bootstrap"]["seed"]) + 1,
    )
    selected["recent_bootstrap"] = block_bootstrap(
        selected["recent"]["curve"],
        block=int(prereg["bootstrap"]["block_days"]),
        paths=int(prereg["bootstrap"]["replications"]),
        seed=int(prereg["bootstrap"]["seed"]) + 2,
    )
    hs300_test = hs300_return(windows["test_start"], windows["test_end"])
    hs300_recent = hs300_return(windows["recent_start"], windows["test_end"])
    checks = {
        "training_platform_exists": bool(eligible),
        "test_annual_min": selected["test"]["stats"]["annual_return"] >= prereg["confirmation_gates"]["test_annual_min"],
        "test_sharpe_min": selected["test"]["stats"]["sharpe"] >= prereg["confirmation_gates"]["test_sharpe_min"],
        "test_mdd_min": selected["test"]["stats"]["max_drawdown"] >= prereg["confirmation_gates"]["test_mdd_min"],
        "recent_annual_min": selected["recent"]["stats"]["annual_return"] >= prereg["confirmation_gates"]["recent_annual_min"],
        "stress_3x_positive": selected["test_3x"]["stats"]["annual_return"] > 0.0,
        "test_bootstrap_positive": selected["test_bootstrap"]["total_p05"] > 0.0,
        "recent_bootstrap_positive": selected["recent_bootstrap"]["total_p05"] > 0.0,
        "test_excess_hs300_positive": selected["test"]["stats"]["total_return"] > hs300_test,
        "no_leverage": selected["max_exposure_pct"] <= 1.0,
    }
    payload = {
        "round": "R480",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__)),
        "selection_basis": prereg["selection"]["basis"],
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "benchmark": prereg["benchmark"],
        "benchmark_returns": {"test": hs300_test, "recent": hs300_recent},
        "sample_count": len(rows),
        "eligible_count": len(eligible),
        "rows": [
            {
                **row,
                "train": {key: value for key, value in row["train"].items() if key != "curve"},
                "validation": {key: value for key, value in row["validation"].items() if key != "curve"},
            }
            for row in rows
        ],
        "selected": {
            **selected,
            "test": {key: value for key, value in selected["test"].items() if key not in {"curve", "trades"}},
            "recent": {key: value for key, value in selected["recent"].items() if key not in {"curve", "trades"}},
            "test_3x": {key: value for key, value in selected["test_3x"].items() if key not in {"curve", "trades"}},
            "test_5x": {key: value for key, value in selected["test_5x"].items() if key not in {"curve", "trades"}},
            "test_dependence": pnl_concentration(selected["test"]["trades"]),
            "recent_dependence": pnl_concentration(selected["recent"]["trades"]),
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
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    test = selected["test"]["stats"]
    recent = selected["recent"]["stats"]
    lines = [
        "# R480 赢家依赖预算与 8 腿子集",
        "",
        "本轮从 R476 的 9 腿库中预注册 4 个 8 腿子集，用训练+验证选择，检验+近一年只确认；沪深300 使用 `000300.SH`。",
        "",
        f"- 样本数：{len(rows)}",
        f"- 训练+验证合格：{len(eligible)}",
        f"- 选中：`{selected['label']}`",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        f"- 检验年化：{test['annual_return']:.2%}；Sharpe：{test['sharpe']:.3f}；MDD：{test['max_drawdown']:.2%}",
        f"- 近一年年化：{recent['annual_return']:.2%}；Sharpe：{recent['sharpe']:.3f}；MDD：{recent['max_drawdown']:.2%}",
        f"- 检验相对 HS300：{test['total_return'] - hs300_test:+.2%}",
        f"- 近一年相对 HS300：{recent['total_return'] - hs300_recent:+.2%}",
        f"- 检验 bootstrap p05：{selected['test_bootstrap']['total_p05']:.2%}",
        f"- 近一年 bootstrap p05：{selected['recent_bootstrap']['total_p05']:.2%}",
        f"- 训练/验证 top-5 正收益占比：{selected['train']['dependence']['top_5_positive_share']:.2%} / {selected['validation']['dependence']['top_5_positive_share']:.2%}",
        f"- 检验/近一年 top-5 正收益占比：{payload['selected']['test_dependence']['top_5_positive_share']:.2%} / {payload['selected']['recent_dependence']['top_5_positive_share']:.2%}",
        "",
        "## 门槛",
        "",
    ]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
