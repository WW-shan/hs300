#!/usr/bin/env python3
"""R414: confirm an 8-leg subset of R391 statistical-pass factors."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r414_statistical_pass_portfolio_preregistration.json"
OUTPUT = E / "r414_statistical_pass_portfolio_2026-11-22.json"
RESULT = E / "r414_statistical_pass_portfolio_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r414_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_prereg() -> dict[str, Any]:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R414":
        raise RuntimeError("R414 round mismatch")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R414 preregistration checksum mismatch")
    if len(payload["candidates"]) != 11:
        raise RuntimeError("R414 requires the frozen 11 R391 passes")
    return payload


def load_panel(p5: Any, prereg: dict[str, Any]) -> pl.DataFrame:
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.hs300.current import current_symbols

    symbols = current_symbols()
    # Start from the project's canonical research panel (loads the current 8-leg
    # members and their PIT-aware columns), then attach only the extra R391 pass
    # columns that are present in ext_data.
    panel, _ = p5.load_candidate_panel(DATA, symbols, [])
    ext_fields = {
        "ext_alt_hs300_short_ratio_low": "short_ratio_low",
        "ext_alt_hs300_vol_of_vol_60d_low": "vol_of_vol_60d_low",
        "ext_alt_hs300_lhb_event_count_20d_low": "lhb_event_count_20d_low",
        "ext_alt_hs300_lhb_inst_net_buy_ratio_20d": "lhb_inst_net_buy_ratio_20d",
        "ext_alt_hs300_holder_top10_share_chg_qoq": "holder_top10_share_chg_qoq",
        "ext_alt_hs300_holder_top10_share_chg_yoy": "holder_top10_share_chg_yoy",
    }
    panel = panel.join(
        load_existing_ext_factors(DATA, ext_fields),
        on=["symbol", "date"],
        how="left",
    )
    # inventory improvement is a PIT financial column.
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    panel = attach_fundamental_factors(
        panel,
        snapshot,
        ["inventory_turnover_improvement_yoy"],
    )
    # amihud_20d is not in enriched storage and is explicitly unavailable.
    if "amihud_20d" not in panel.columns:
        panel = panel.with_columns(pl.lit(None, dtype=pl.Float64).alias("amihud_20d"))
    missing = [
        factor for factor in prereg["candidates"]
        if factor not in panel.columns and factor != "amihud_20d"
    ]
    if missing:
        raise RuntimeError(f"R414 panel missing candidates: {missing}")
    return panel

def select_subset(prereg: dict[str, Any], panel: pl.DataFrame) -> list[str]:
    from app.factor_research.signal_scan import calculate_rank_ic

    ranks = []
    for factor in prereg["candidates"]:
        if factor == "amihud_20d":
            continue
        ic = calculate_rank_ic(
            panel,
            factor,
            "forward_return_5d",
            minimum=30,
        )
        ranks.append((factor, float(ic["ic"].mean()) if ic.height else -999.0))
    ranks.sort(key=lambda row: (-row[1], row[0]))
    chosen: list[str] = []
    for factor, _ in ranks:
        chosen.append(factor)
        if len(chosen) == prereg["selection_rule"]["max_members"]:
            break
    if len(chosen) != 8:
        raise RuntimeError("R414 subset selection did not produce eight members")
    return chosen


def run_variant(
    p5: Any,
    service: Any,
    prepared: Any,
    members: list[str],
    directions: dict[str, str],
    *,
    start: str,
    end: str,
    cost: str,
    label: str,
) -> dict[str, Any]:
    scoring, selected = p5._equal_inputs(members, directions)
    record = p5._run_prepared(
        service,
        prepared,
        scoring,
        selected,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        cost_label=cost,
        label=label,
    )
    stats = record["stats"]
    return {
        "total_return": stats["total_return"],
        "annual_return": stats["annual_return"],
        "sharpe": stats["sharpe"],
        "max_drawdown": stats["max_drawdown"],
        "n_trades": stats["n_trades"],
        "benchmark_return": record["benchmark_return"],
    }


def baseline_members(prereg: dict[str, Any]) -> list[str]:
    return [
        "ext_alt_hs300_idio_vol_40d_low",
        "ext_alt_hs300_dividend_yield",
        "ext_alt_hs300_vol_of_vol_20d_low",
        "ext_alt_hs300_cash_flow_stability_4q_low",
        "ext_alt_hs300_fund_holding_ratio_low",
        "ext_alt_hs300_ocf_margin_4q",
        "ext_alt_hs300_margin_chg_1m_low",
        "ext_alt_hs300_holder_top10_share_chg_yoy",
    ]


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R414 output exists: {path.name}")
    prereg = load_prereg()
    p5 = load_p5()
    panel = load_panel(p5, prereg)
    selected = select_subset(prereg, panel)
    baseline = baseline_members(prereg)
    from app.factor_research.sets import FactorSetStore
    sets = FactorSetStore(DATA)
    head = sets.head("multi_dimension_v1")
    if head is None or head.watch_version is None:
        raise RuntimeError("multi_dimension_v1 watch version unavailable")
    version = sets.load_version("multi_dimension_v1", head.watch_version)
    baseline_directions = {
        member.factor_id: member.direction
        for member in version.members
        if member.role == "alpha" and member.membership_status in {"watch", "active"}
    }
    directions = {**baseline_directions, **prereg["directions"]}
    service = p5.portfolio._service()
    all_members = sorted(set(baseline) | set(selected))
    missing = [factor for factor in all_members if factor not in panel.columns]
    if missing:
        raise RuntimeError(f"R414 prepared panel missing: {missing}")
    symbols = sorted(panel["symbol"].unique().to_list())
    windows = prereg["windows"]
    prepared_train = p5._prepare_shared_window(
        service, symbols, panel, all_members,
        start=date.fromisoformat(windows["train_start"]),
        end=date.fromisoformat(windows["train_end"]),
    )
    prepared_test = p5._prepare_shared_window(
        service, symbols, panel, all_members,
        start=date.fromisoformat(windows["test_start"]),
        end=date.fromisoformat(windows["test_end"]),
    )
    baseline_train = run_variant(p5, service, prepared_train, baseline, directions, start=windows["train_start"], end=windows["train_end"], cost="2x", label="r414_baseline_train")
    variant_train = run_variant(p5, service, prepared_train, selected, directions, start=windows["train_start"], end=windows["train_end"], cost="2x", label="r414_variant_train")
    baseline_test = run_variant(p5, service, prepared_test, baseline, directions, start=windows["test_start"], end=windows["test_end"], cost="2x", label="r414_baseline_test")
    variant_test = run_variant(p5, service, prepared_test, selected, directions, start=windows["test_start"], end=windows["test_end"], cost="2x", label="r414_variant_test")
    variant_stress = run_variant(p5, service, prepared_test, selected, directions, start=windows["test_start"], end=windows["test_end"], cost="3x", label="r414_variant_test_3x")
    gates = prereg["gates"]
    checks = {
        "selected_has_8": len(selected) == 8,
        "train_annual_min": variant_train["annual_return"] >= gates["train_annual_min"],
        "test_annual_min": variant_test["annual_return"] >= gates["test_annual_min"],
        "test_sharpe_min": variant_test["sharpe"] >= gates["test_sharpe_min"],
        "test_mdd_min": variant_test["max_drawdown"] >= gates["test_mdd_min"],
        "stress_3x_positive": variant_stress["annual_return"] > 0,
        "beat_baseline_train": variant_train["annual_return"] > baseline_train["annual_return"],
        "beat_baseline_test": variant_test["annual_return"] > baseline_test["annual_return"],
    }
    payload = {
        "round": "R414",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "selected_members": selected,
        "data_unavailable": ["amihud_20d"],
        "windows": windows,
        "baseline_train_2x": baseline_train,
        "variant_train_2x": variant_train,
        "baseline_test_2x": baseline_test,
        "variant_test_2x": variant_test,
        "variant_test_3x": variant_stress,
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
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R414 R391统计通过因子组合确认",
        "",
        f"- 选中 8 腿：{', '.join(selected)}",
        "| 版本 | 训练年化 | 训练Sharpe | 检验年化 | 检验Sharpe | 检验MDD | 3×检验年化 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| 当前基线 | {baseline_train['annual_return']:.2%} | {baseline_train['sharpe']:.3f} | {baseline_test['annual_return']:.2%} | {baseline_test['sharpe']:.3f} | {baseline_test['max_drawdown']:.2%} | - |",
        f"| R391通过者子集 | {variant_train['annual_return']:.2%} | {variant_train['sharpe']:.3f} | {variant_test['annual_return']:.2%} | {variant_test['sharpe']:.3f} | {variant_test['max_drawdown']:.2%} | {variant_stress['annual_return']:.2%} |",
        "",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        "",
        "## 门槛",
        "",
        *[f"- {name}: {'通过' if passed else '未过'}" for name, passed in checks.items()],
        "",
        "## 边界",
        "",
        *[f"- {item}" for item in prereg["known_limits"]],
        "",
    ]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
