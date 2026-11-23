#!/usr/bin/env python3
"""R476: expand the accepted factor set beyond the 8-factor strategy cap."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import polars as pl

from app.factor_research.evidence_guard import require_formal_evidence

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r476_factor_set_expansion_preregistration.json"
OUTPUT = E / "r476_factor_set_expansion_mc_2026-11-22.json"
RESULT = E / "r476_factor_set_expansion_mc_2026-11-22.md"
COMPOSITE = "_r476_library_composite"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R476 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sample_weights(prereg: dict[str, Any], added_factor: str, added_weight: float) -> dict[str, float]:
    base_members = list(prereg["base_members"])
    if added_factor in base_members:
        raise ValueError(f"candidate duplicates a base member: {added_factor}")
    if not 0.0 < float(added_weight) < 1.0:
        raise ValueError("candidate weight must be between zero and one")
    core_weight = 1.0 - float(added_weight)
    base_weight = core_weight / len(base_members)
    return {
        **{factor: base_weight for factor in base_members},
        added_factor: float(added_weight),
    }


def sample_directions(prereg: dict[str, Any], weights: dict[str, float]) -> dict[str, str]:
    return {factor: str(prereg["directions"][factor]) for factor in weights}


def generate_samples() -> list[dict[str, Any]]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    execution = prereg["execution"]
    for added in prereg["candidates"]:
        for weight in prereg["added_weight_grid"]:
            weights = sample_weights(prereg, added, float(weight))
            rows.append(
                {
                    "label": f"base_plus_{added}_at_{round(float(weight) * 100)}",
                    "added_factor": added,
                    "added_weight": float(weight),
                    "weights": weights,
                    "directions": sample_directions(prereg, weights),
                    "max_positions": int(execution["max_positions"]),
                    "max_hold_days": int(execution["max_hold_days"]),
                    "max_exposure_pct": float(execution["max_exposure_pct"]),
                    "entry_score": float(execution["entry_score"]),
                    "exit_score": float(execution["exit_score"]),
                    "top_rank": max(20, int(execution["max_positions"])),
                }
            )
    return rows


def build_library_panel(
    panel: pl.DataFrame,
    prereg: dict[str, Any],
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Materialize each candidate composite, then combine them at explicit weights."""
    from app.factor_research.incremental import build_rank_composite

    rows = generate_samples()
    coverage: dict[str, Any] = {}
    for added in prereg["candidates"]:
        variants = [row for row in rows if row["added_factor"] == added]
        for row in variants:
            output = f"_r476_variant_{row['label']}"
            panel, _ = build_rank_composite(
                panel,
                row["weights"],
                row["directions"],
                output,
                minimum_weight_coverage=0.75,
            )
            nonnull = panel.filter(pl.col(output).is_not_null())
            coverage[row["label"]] = {
                "non_null_rows": nonnull.height,
                "non_null_ratio": round(nonnull.height / panel.height, 6),
                "first_value_date": str(nonnull["date"].min()) if nonnull.height else None,
                "members": len(row["weights"]),
                "candidate": added,
                "candidate_weight": row["added_weight"],
            }
    return panel, coverage


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
    from datetime import date

    from app.factor_research.incremental import build_rank_composite
    field = f"_r476_variant_{row['label']}"
    if field not in panel.columns:
        panel, _ = build_rank_composite(
            panel,
            row["weights"],
            row["directions"],
            field,
            minimum_weight_coverage=0.75,
        )
    # The strategy accepts at most eight scoring fields. The expanded library is
    # represented by one already-direction-adjusted daily rank composite.
    scoring = {field: 1.0}
    directions = {field: "high"}
    seed_config = p5.portfolio._backtest_config(
        scoring,
        directions,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=int(row["max_positions"]),
        cost=_cost(p5, cost_label),
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
    require_formal_evidence(result, label="research_r476_factor_set_expansion_mc.py")
    if result.error:
        if "未产生买入信号" in str(result.error):
            return failed_metrics()
        raise RuntimeError(result.error)
    return {
        "stats": p5._select_stats(result.stats),
        "curve": list(result.equity_curve),
        "trades": len(result.trades),
    }


def _cost(p5: Any, label: str) -> dict[str, float]:
    if label == "5x":
        return {
            "fees_pct": 0.001,
            "commission_pct": 0.001,
            "stamp_tax_pct": 0.0005,
            "slippage_bps": 25.0,
        }
    return p5.portfolio._cost_parameters(label)


def failed_metrics() -> dict[str, Any]:
    return {
        "stats": {
            "annual_return": -1.0,
            "total_return": -1.0,
            "max_drawdown": -1.0,
            "sharpe": -1.0,
            "sortino": -1.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "n_trades": 0.0,
            "avg_exposure": 0.0,
            "worst": -1.0,
        },
        "curve": [],
        "trades": 0,
        "error": "no_buy_signal",
    }


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r468 = load_module(ROOT / "scripts" / "research_r468_operating_efficiency_sleeve.py", "r476_r468")
    r464 = r468.load_module(ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r476_r464")
    r453 = r468.load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r476_r453")
    p5 = r464.load_p5()

    from app.hs300.current import current_symbols

    panel, _ignored = r464.build_panel(p5)
    # `load_candidate_panel` intentionally returns raw price columns only.
    # Momentum is a matrix-native feature (close / close.shift(60) - 1), not
    # an enriched storage column, so materialize it with that exact definition here.
    price_panel = p5.load_candidate_panel(ROOT / "data", current_symbols(), [])[0]
    price_panel = price_panel.with_columns(
        (
            pl.col("close") / pl.col("close").shift(60).over("symbol") - 1.0
        ).alias("momentum_60d")
    )
    panel = panel.join(
        price_panel.select(["symbol", "date", "momentum_60d"]),
        on=["symbol", "date"],
        how="left",
    ).sort(["symbol", "date"])
    # value_quality_safety is already materialized by R464. The operating
    # efficiency field needs the production announcement-gated attachment.
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot

    snapshot = load_fundamental_snapshot(ROOT / "data")
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    missing = [factor for factor in prereg["candidates"] if factor not in panel.columns]
    if missing:
        panel = attach_fundamental_factors(panel, snapshot, missing)
    missing = [factor for factor in prereg["candidates"] if factor not in panel.columns]
    if missing:
        raise RuntimeError(f"candidate factors unavailable: {missing}")

    panel, coverage = build_library_panel(panel, prereg)
    symbols = sorted(panel["symbol"].unique().to_list())
    service = p5.portfolio._service()
    windows = prereg["windows"]
    rows = generate_samples()
    for row in rows:
        row["train"] = run_sample(
            p5, service, panel, symbols, row,
            start=windows["train_start"], end=windows["train_end"], cost_label="2x",
        )
        row["validation"] = run_sample(
            p5, service, panel, symbols, row,
            start=windows["validation_start"], end=windows["validation_end"], cost_label="2x",
        )
        row["training_gate"] = bool(
            row["train"]["stats"]["annual_return"] >= prereg["selection"]["train_annual_min"]
            and row["validation"]["stats"]["annual_return"] >= prereg["selection"]["validation_annual_min"]
            and row["validation"]["stats"]["sharpe"] >= prereg["selection"]["validation_sharpe_min"]
            and row["validation"]["stats"]["max_drawdown"] >= prereg["selection"]["validation_mdd_min"]
        )

    eligible = [row for row in rows if row["training_gate"]]
    eligible.sort(
        key=lambda row: (
            min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]),
            row["validation"]["stats"]["sharpe"],
        ),
        reverse=True,
    )
    finalists = eligible[: int(prereg["selection"]["bootstrap_finalists"])]
    for row in finalists:
        row["validation_bootstrap"] = r453.bootstrap(
            row["validation"]["curve"],
            block=int(prereg["bootstrap"]["block_days"]),
            paths=int(prereg["bootstrap"]["replications"]),
            seed=int(prereg["bootstrap"]["seed"]) + 100,
        )
    bootstrap_positive = [row for row in finalists if row["validation_bootstrap"]["total_p05"] > 0.0]
    selected = bootstrap_positive[0] if bootstrap_positive else (eligible[0] if eligible else rows[0])

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
    selected["test_bootstrap"] = r453.bootstrap(
        selected["test"]["curve"],
        block=int(prereg["bootstrap"]["block_days"]),
        paths=int(prereg["bootstrap"]["replications"]),
        seed=int(prereg["bootstrap"]["seed"]) + 1,
    )
    selected["recent_bootstrap"] = r453.bootstrap(
        selected["recent"]["curve"],
        block=int(prereg["bootstrap"]["block_days"]),
        paths=int(prereg["bootstrap"]["replications"]),
        seed=int(prereg["bootstrap"]["seed"]) + 2,
    )
    checks = {
        "training_platform_exists": bool(eligible),
        "validation_bootstrap_positive": selected.get("validation_bootstrap", {}).get("total_p05", -1.0) > 0.0,
        "test_annual_min": selected["test"]["stats"]["annual_return"] >= prereg["confirmation_gates"]["test_annual_min"],
        "test_sharpe_min": selected["test"]["stats"]["sharpe"] >= prereg["confirmation_gates"]["test_sharpe_min"],
        "test_mdd_min": selected["test"]["stats"]["max_drawdown"] >= prereg["confirmation_gates"]["test_mdd_min"],
        "recent_annual_min": selected["recent"]["stats"]["annual_return"] >= prereg["confirmation_gates"]["recent_annual_min"],
        "stress_3x_positive": selected["test_3x"]["stats"]["annual_return"] > 0.0,
        "test_total_p05_positive": selected["test_bootstrap"]["total_p05"] > 0.0,
        "recent_total_p05_positive": selected["recent_bootstrap"]["total_p05"] > 0.0,
        "expanded_leg_count": len(selected["weights"]) >= 9,
        "no_leverage": selected["max_exposure_pct"] <= 1.0,
    }
    payload = {
        "round": "R476",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__)),
        "selection_basis": "train_and_validation_only",
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "coverage": coverage,
        "sample_count": len(rows),
        "eligible_count": len(eligible),
        "bootstrap_positive_count": len(bootstrap_positive),
        "samples": [
            {
                **row,
                "train": {key: value for key, value in row["train"].items() if key != "curve"},
                "validation": {key: value for key, value in row["validation"].items() if key != "curve"},
            }
            for row in rows
        ],
        "selected": {
            **selected,
            "test": {key: value for key, value in selected["test"].items() if key != "curve"},
            "recent": {key: value for key, value in selected["recent"].items() if key != "curve"},
            "test_3x": {key: value for key, value in selected["test_3x"].items() if key != "curve"},
            "test_5x": {key: value for key, value in selected["test_5x"].items() if key != "curve"},
        },
        "gate": {"pass": all(checks.values()), "checks": checks},
        "diagnostics": {
            "five_x_warning_only": True,
            "no_leverage": True,
            "cost_scenarios": {"2x": "main", "3x": "stress", "5x": "warning_only"},
        },
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
    lines = [
        "# R476 因子集扩容与复合层验证",
        "",
        "本轮不再让 8 参数策略直接承载扩容：先把 9 腿按完整方向表物化成日频 rank composite，再交给单评分腿策略回测。",
        "",
        f"- 样本数：{len(rows)}",
        f"- 训练+验证合格：{len(eligible)}",
        f"- 验证 bootstrap 正尾：{len(bootstrap_positive)}",
        f"- 选中：`{selected['label']}`",
        f"- 组合腿数：{len(selected['weights'])}",
        f"- 候选权重：{selected['added_weight']:.0%}",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        f"- 检验年化：{selected['test']['stats']['annual_return']:.2%}",
        f"- 检验 Sharpe：{selected['test']['stats']['sharpe']:.3f}",
        f"- 检验 MDD：{selected['test']['stats']['max_drawdown']:.2%}",
        f"- 近一年年化：{selected['recent']['stats']['annual_return']:.2%}",
        f"- 检验 p05：{selected['test_bootstrap']['total_p05']:.2%}",
        f"- 近一年 p05：{selected['recent_bootstrap']['total_p05']:.2%}",
        f"- 3× 检验年化：{selected['test_3x']['stats']['annual_return']:.2%}",
        f"- 5× 检验年化：{selected['test_5x']['stats']['annual_return']:.2%}（仅诊断）",
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
