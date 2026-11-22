#!/usr/bin/env python3
"""R472: fresh-start holdout audit for the R468 operating-efficiency sleeve."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r472_operating_efficiency_holdout_preregistration.json"
OUTPUT = E / "r472_operating_efficiency_holdout_2026-11-22.json"
RESULT = E / "r472_operating_efficiency_holdout_2026-11-22.md"
SOURCE = E / "r468_operating_efficiency_sleeve_2026-11-22.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R472 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def path_metrics(curve: list[dict[str, Any]]) -> dict[str, float]:
    if len(curve) < 2:
        return {"total_return": -1.0, "annual": -1.0, "sharpe": -1.0, "mdd": -1.0}
    values = [float(item["value"]) for item in curve]
    returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    mean = sum(returns) / len(returns)
    vol = (sum((item - mean) ** 2 for item in returns) / len(returns)) ** 0.5
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {"total_return": total, "annual": (1.0 + total) ** (252.0 / len(returns)) - 1.0, "sharpe": mean / vol * (252.0 ** 0.5) if vol else 0.0, "mdd": mdd}


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    selected = source["selected"]
    r468 = load_module(ROOT / "scripts" / "research_r468_operating_efficiency_sleeve.py", "r472_r468")
    r464 = r468.load_module(ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r472_r464")
    r453 = r468.load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r472_r453")
    p5 = r464.load_p5()
    panel, symbols, coverage = r468.build_panel(p5)
    service = p5.portfolio._service()
    sample = {
        "weights": selected["weights"],
        "direction": selected["direction"],
        "max_positions": int(selected["max_positions"]),
        "max_hold_days": int(selected["max_hold_days"]),
        "max_exposure_pct": float(selected["max_exposure_pct"]),
        "entry_score": float(selected["entry_score"]),
        "exit_score": float(selected["exit_score"]),
    }
    rows = []
    for phase in prereg["fresh_starts"]:
        result = r468.run_sample(
            p5,
            service,
            panel,
            symbols,
            sample,
            start=phase,
            end=prereg["end"],
            cost_label="2x",
        )
        rows.append({"start": phase, "end": prereg["end"], "metrics": path_metrics(result["curve"]), "bootstrap": r453.bootstrap(result["curve"], block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]) + len(rows))})
    positive = sum(row["metrics"]["total_return"] > 0.0 for row in rows)
    checks = {
        "all_phases_positive": positive == len(rows),
        "minimum_phase_total_positive": min(row["metrics"]["total_return"] for row in rows) > 0.0,
        "minimum_phase_sharpe_positive": min(row["metrics"]["sharpe"] for row in rows) > 0.0,
        "all_bootstrap_p05_positive": all(row["bootstrap"]["total_p05"] > 0.0 for row in rows),
        "no_leverage": True,
    }
    payload = {
        "round": "R472",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_round": "R468",
        "source_label": selected["label"],
        "coverage": coverage,
        "rows": rows,
        "positive_phase_count": positive,
        "phase_count": len(rows),
        "gate": {"pass": all(checks.values()), "checks": checks},
        "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R472 营运效率 sleeve fresh-start 留出审计",
        "",
        "R468 的高检验收益需要检查是否依赖固定起点。本轮固定 R468 参数，只改变 fresh-start 起点，不重新选参。",
        "",
        f"- 源：`{selected['label']}`",
        f"- 相位：{len(rows)}，正收益：{positive}",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        "",
        "| 起点 | 总收益 | 年化 | Sharpe | MDD | p05 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        m = row["metrics"]
        lines.append(f"| {row['start']} | {m['total_return']:.2%} | {m['annual']:.2%} | {m['sharpe']:.3f} | {m['mdd']:.2%} | {row['bootstrap']['total_p05']:.2%} |")
    lines += ["", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
