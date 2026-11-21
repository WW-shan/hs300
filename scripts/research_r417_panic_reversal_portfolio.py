#!/usr/bin/env python3
"""R417: confirm R416 panic-reversal leg inside the current 8-leg portfolio."""
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
PREREG = E / "r417_panic_reversal_portfolio_preregistration.json"
OUTPUT = E / "r417_panic_reversal_portfolio_2026-11-22.json"
RESULT = E / "r417_panic_reversal_portfolio_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(name: str, filename: str) -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_prereg() -> dict[str, Any]:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R417":
        raise RuntimeError("R417 round mismatch")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R417 preregistration checksum mismatch")
    return payload


def replacement_variants(prereg: dict[str, Any]) -> dict[str, list[str]]:
    base = list(prereg["baseline_members"])
    output = {}
    for item in prereg["variant_replacements"]:
        drop = item["drop"]
        output[item["name"]] = [*[member for member in base if member != drop], prereg["source_factor"]]
    return output


def run(p5: Any, service: Any, prepared: Any, members: list[str], directions: dict[str, str], *, start: str, end: str, cost: str, label: str) -> dict[str, Any]:
    scoring, selected = p5._equal_inputs(members, directions)
    record = p5._run_prepared(
        service, prepared, scoring, selected,
        start=date.fromisoformat(start), end=date.fromisoformat(end),
        cost_label=cost, label=label,
    )
    stats = record["stats"]
    return {
        "total_return": stats["total_return"],
        "annual_return": stats["annual_return"],
        "sharpe": stats["sharpe"],
        "max_drawdown": stats["max_drawdown"],
        "n_trades": stats["n_trades"],
    }


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R417 output exists: {path.name}")
    prereg = load_prereg()
    p5 = load_module("r417_p5", "research_factor_increment_p5.py")
    r416 = load_module("r417_r416", "research_r416_sentiment_interaction.py")
    from app.hs300.current import current_symbols
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    frame = r416.build_factors(panel)
    source = prereg["source_factor"]
    if source not in frame.columns:
        raise RuntimeError(f"R417 missing source factor: {source}")
    # Non-regime dates carry a neutral score, not a missing score. This keeps
    # the eight-leg composite defined without turning the conditional factor
    # into an always-on factor in statistical interpretation.
    frame = frame.with_columns(pl.col(source).fill_null(0.0).alias(source))
    missing = [member for member in prereg["baseline_members"] if member not in frame.columns]
    if missing:
        raise RuntimeError(f"R417 panel missing baseline members: {missing}")
    service = p5.portfolio._service()
    symbols = sorted(frame["symbol"].unique().to_list())
    directions = {member: "high" for member in prereg["baseline_members"]}
    directions[source] = prereg["new_factor_direction"]
    windows = prereg["windows"]
    all_members = sorted(set(prereg["baseline_members"]) | {source})
    prepared_train = p5._prepare_shared_window(service, symbols, frame, all_members, start=date.fromisoformat(windows["train_start"]), end=date.fromisoformat(windows["train_end"]))
    prepared_validation = p5._prepare_shared_window(service, symbols, frame, all_members, start=date.fromisoformat(windows["validation_start"]), end=date.fromisoformat(windows["validation_end"]))
    prepared_test = p5._prepare_shared_window(service, symbols, frame, all_members, start=date.fromisoformat(windows["test_start"]), end=date.fromisoformat(windows["test_end"]))
    baseline = {member: "high" for member in prereg["baseline_members"]}
    rows: dict[str, dict[str, Any]] = {
        "baseline": {
            "members": prereg["baseline_members"],
            "train_2x": run(p5, service, prepared_train, prereg["baseline_members"], baseline, start=windows["train_start"], end=windows["train_end"], cost="2x", label="r417_baseline_train"),
            "validation_2x": run(p5, service, prepared_validation, prereg["baseline_members"], baseline, start=windows["validation_start"], end=windows["validation_end"], cost="2x", label="r417_baseline_validation"),
        }
    }
    for name, members in replacement_variants(prereg).items():
        rows[name] = {
            "members": members,
            "train_2x": run(p5, service, prepared_train, members, directions, start=windows["train_start"], end=windows["train_end"], cost="2x", label=f"r417_{name}_train"),
            "validation_2x": run(p5, service, prepared_validation, members, directions, start=windows["validation_start"], end=windows["validation_end"], cost="2x", label=f"r417_{name}_validation"),
        }
    base = rows["baseline"]
    eligible = [
        (name, row)
        for name, row in rows.items()
        if name != "baseline"
        and row["train_2x"]["annual_return"] > base["train_2x"]["annual_return"]
        and row["validation_2x"]["annual_return"] > base["validation_2x"]["annual_return"]
    ]
    eligible.sort(key=lambda item: (min(item[1]["train_2x"]["annual_return"], item[1]["validation_2x"]["annual_return"]), item[0]), reverse=True)
    selected_name = eligible[0][0] if eligible else "baseline"
    selected = rows[selected_name]
    selected["test_2x"] = run(p5, service, prepared_test, selected["members"], directions, start=windows["test_start"], end=windows["test_end"], cost="2x", label=f"r417_{selected_name}_test")
    selected["test_3x"] = run(p5, service, prepared_test, selected["members"], directions, start=windows["test_start"], end=windows["test_end"], cost="3x", label=f"r417_{selected_name}_test_3x")
    gates = prereg["gates"]
    checks = {
        "selected_not_baseline": selected_name != "baseline",
        "train_annual_min": selected["train_2x"]["annual_return"] >= gates["train_annual_min"],
        "validation_annual_min": selected["validation_2x"]["annual_return"] >= gates["validation_annual_min"],
        "test_annual_min": selected["test_2x"]["annual_return"] >= gates["test_annual_min"],
        "test_sharpe_min": selected["test_2x"]["sharpe"] >= gates["test_sharpe_min"],
        "test_mdd_min": selected["test_2x"]["max_drawdown"] >= gates["test_mdd_min"],
        "beat_baseline_train": selected["train_2x"]["annual_return"] > base["train_2x"]["annual_return"],
        "beat_baseline_validation": selected["validation_2x"]["annual_return"] > base["validation_2x"]["annual_return"],
        "stress_3x_positive": selected["test_3x"]["annual_return"] > 0,
        "recent_positive": selected["test_2x"]["annual_return"] > 0,
    }
    payload = {
        "round": "R417",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_factor": source,
        "selected_variant": selected_name,
        "variants": rows,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R417 恐慌反转新因子组合确认",
        "",
        "| 变体 | 腿数 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 3×检验年化 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, row in rows.items():
        test = row.get("test_2x")
        if test is None:
            lines.append(f"| {name} | {len(row['members'])} | {row['train_2x']['annual_return']:.2%} | {row['validation_2x']['annual_return']:.2%} | - | - | - | - |")
        else:
            lines.append(f"| {name} | {len(row['members'])} | {row['train_2x']['annual_return']:.2%} | {row['validation_2x']['annual_return']:.2%} | {test['annual_return']:.2%} | {test['sharpe']:.3f} | {test['max_drawdown']:.2%} | {row['test_3x']['annual_return']:.2%} |")
    lines += ["", f"- 选中：`{selected_name}`", f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**", "", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
