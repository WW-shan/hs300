#!/usr/bin/env python3
"""R489: deterministic 6-8 leg subset search over the frozen R480 library."""

from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import math
import random
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r489_decorrelated_subset_search_preregistration.json"
R484 = E / "r484_daily_entry_batch_cap_2026-11-22.json"
OUTPUT = E / "r489_decorrelated_subset_search_2026-11-22.json"
RESULT = E / "r489_decorrelated_subset_search_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R489 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def generate_samples() -> list[dict[str, Any]]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    members = sorted(prereg["universe"]["members"])
    rows = []
    for size in prereg["subset_sizes"]:
        for combo in itertools.combinations(members, size):
            weights = {member: 1.0 / size for member in combo}
            rows.append(
                {
                    "label": f"subset_{size}_{'_'.join(combo)}",
                    "weights": weights,
                    "directions": {
                        member: prereg["universe"]["members"][member] for member in combo
                    },
                    "max_positions": 10,
                    "max_hold_days": 40,
                    "max_exposure_pct": 1.0,
                    "entry_score": 70.0,
                    "exit_score": 40.0,
                    "top_rank": 20,
                    "max_new_positions_per_day": 1,
                }
            )
    return rows


def block_bootstrap(
    curve: list[dict[str, Any]], *, block: int, paths: int, seed: int
) -> dict[str, float]:
    values = [
        float(curve[i]["value"]) / float(curve[i - 1]["value"]) - 1.0 for i in range(1, len(curve))
    ]
    rng = random.Random(seed)
    totals, sharpes, mdds = [], [], []
    for _ in range(paths):
        sampled = []
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

    field = f"_r489_{row['label']}"
    if field not in panel.columns:
        panel, _ = build_rank_composite(
            panel, row["weights"], row["directions"], field, minimum_weight_coverage=0.75
        )
    config = p5.portfolio._backtest_config(
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
    config.params = {
        **(config.params or {}),
        "entry_score": float(row["entry_score"]),
        "exit_score": float(row["exit_score"]),
        "top_rank": int(row["top_rank"]),
    }
    config.max_exposure_pct = float(row["max_exposure_pct"])
    config.max_new_positions_per_day = int(row["max_new_positions_per_day"])
    config.symbols = list(symbols)
    prepared_base = service.prepare_matrix_optimization([config])
    prepared = p5._extend_prepared_with_panel(prepared_base, panel, [field])
    result = service.run(config, prepared=prepared)
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
    verify_sidecar(R484)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r468 = load_module(
        ROOT / "scripts" / "research_r468_operating_efficiency_sleeve.py", "r489_r468"
    )
    r464 = r468.load_module(
        ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r489_r464"
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
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot

    snapshot = load_fundamental_snapshot(ROOT / "data")
    members = prereg["universe"]["members"]
    missing = [factor for factor in members if factor not in panel.columns]
    if missing:
        panel = attach_fundamental_factors(panel, snapshot, missing)
    symbols = sorted(panel["symbol"].unique().to_list())
    service = p5.portfolio._service()
    windows = prereg["windows"]
    rows = generate_samples()
    # train+validation pre-screen
    pre = []
    for row in rows:
        row["train"] = run_sample(
            p5,
            service,
            panel,
            symbols,
            row,
            start=windows["train_start"],
            end=windows["train_end"],
            cost_label="2x",
        )
        row["validation"] = run_sample(
            p5,
            service,
            panel,
            symbols,
            row,
            start=windows["validation_start"],
            end=windows["validation_end"],
            cost_label="2x",
        )
        pre.append(row)
    pre.sort(
        key=lambda row: (
            min(
                row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]
            ),
            row["validation"]["stats"]["sharpe"],
        ),
        reverse=True,
    )
    finalists = pre[: int(prereg["selection"]["max_finalists"])]
    for row in finalists:
        row["validation_bootstrap"] = block_bootstrap(
            row["validation"]["curve"], block=20, paths=5000, seed=489
        )
    eligible = [
        row
        for row in finalists
        if row["train"]["stats"]["annual_return"] >= prereg["selection"]["train_annual_min"]
        and row["validation"]["stats"]["annual_return"]
        >= prereg["selection"]["validation_annual_min"]
        and row["validation"]["stats"]["sharpe"] >= prereg["selection"]["validation_sharpe_min"]
        and row["validation"]["stats"]["max_drawdown"] >= prereg["selection"]["validation_mdd_min"]
        and row["validation_bootstrap"]["total_p05"]
        >= prereg["selection"]["validation_bootstrap_p05_min_for_platform"]
    ]
    eligible.sort(
        key=lambda row: (
            min(
                row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]
            ),
            row["validation_bootstrap"]["total_p05"],
            -len(row["weights"]),
            row["label"],
        ),
        reverse=True,
    )
    selected = eligible[0] if eligible else finalists[0]
    selected["test"] = run_sample(
        p5,
        service,
        panel,
        symbols,
        selected,
        start=windows["test_start"],
        end=windows["test_end"],
        cost_label="2x",
    )
    selected["recent"] = run_sample(
        p5,
        service,
        panel,
        symbols,
        selected,
        start=windows["recent_start"],
        end=windows["test_end"],
        cost_label="2x",
    )
    selected["test_3x"] = run_sample(
        p5,
        service,
        panel,
        symbols,
        selected,
        start=windows["test_start"],
        end=windows["test_end"],
        cost_label="3x",
    )
    selected["test_5x"] = run_sample(
        p5,
        service,
        panel,
        symbols,
        selected,
        start=windows["test_start"],
        end=windows["test_end"],
        cost_label="5x",
    )
    selected["test_bootstrap"] = block_bootstrap(
        selected["test"]["curve"], block=20, paths=5000, seed=490
    )
    selected["recent_bootstrap"] = block_bootstrap(
        selected["recent"]["curve"], block=20, paths=5000, seed=491
    )
    hs300_test = hs300_return(windows["test_start"], windows["test_end"])
    hs300_recent = hs300_return(windows["recent_start"], windows["test_end"])
    checks = {
        "training_platform_exists": bool(eligible),
        "test_annual_min": selected["test"]["stats"]["annual_return"]
        >= prereg["confirmation_gates"]["test_annual_min"],
        "test_sharpe_min": selected["test"]["stats"]["sharpe"]
        >= prereg["confirmation_gates"]["test_sharpe_min"],
        "test_mdd_min": selected["test"]["stats"]["max_drawdown"]
        >= prereg["confirmation_gates"]["test_mdd_min"],
        "recent_annual_min": selected["recent"]["stats"]["annual_return"]
        >= prereg["confirmation_gates"]["recent_annual_min"],
        "stress_3x_positive": selected["test_3x"]["stats"]["annual_return"] > 0.0,
        "test_bootstrap_positive": selected["test_bootstrap"]["total_p05"] > 0.0,
        "recent_bootstrap_positive": selected["recent_bootstrap"]["total_p05"] > 0.0,
        "test_excess_hs300_positive": selected["test"]["stats"]["total_return"] > hs300_test,
        "no_leverage": selected["max_exposure_pct"] <= 1.0,
    }
    payload = {
        "round": "R489",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__)),
        "selection_basis": prereg["selection"]["basis"],
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "benchmark": prereg["benchmark"],
        "benchmark_returns": {"test": hs300_test, "recent": hs300_recent},
        "subset_count": len(rows),
        "finalist_count": len(finalists),
        "eligible_count": len(eligible),
        "rows": [
            {
                **row,
                "train": {k: v for k, v in row["train"].items() if k != "curve"},
                "validation": {k: v for k, v in row["validation"].items() if k != "curve"},
            }
            for row in rows
        ],
        "selected": {
            **selected,
            "test": {k: v for k, v in selected["test"].items() if k != "curve"},
            "recent": {k: v for k, v in selected["recent"].items() if k != "curve"},
            "test_3x": {k: v for k, v in selected["test_3x"].items() if k != "curve"},
            "test_5x": {k: v for k, v in selected["test_5x"].items() if k != "curve"},
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
    st = selected["test"]["stats"]
    rc = selected["recent"]["stats"]
    lines = [
        "# R489 去相关 6–8 腿子集搜索",
        "",
        "本轮从 R480 的 8 腿库中枚举 6–8 腿子集，训练+验证选样，检验+近一年确认。",
        "",
        f"- 子集总数：{len(rows)}",
        f"- 进入 bootstrap 的 finalist：{len(finalists)}",
        f"- 训练+验证合格：{len(eligible)}",
        f"- 选中：`{selected['label']}`",
        f"- 腿数：{len(selected['weights'])}",
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
        f"- 检验 p05：{selected['test_bootstrap']['total_p05']:.2%}",
        f"- 近一年 p05：{selected['recent_bootstrap']['total_p05']:.2%}",
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
