#!/usr/bin/env python3
"""R478: classify R476 bootstrap tails without relaxing the admission gate."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r478_library_bootstrap_tolerance_preregistration.json"
R476 = E / "r476_factor_set_expansion_mc_2026-11-22.json"
OUTPUT = E / "r478_library_bootstrap_tolerance_2026-11-22.json"
RESULT = E / "r478_library_bootstrap_tolerance_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R478 output exists; refusing overwrite: {path.name}")


def classify_p05(value: float | None, tolerance: float = 0.01) -> str:
    if value is None:
        return "missing"
    if value >= 0.0:
        return "positive"
    if value >= -float(tolerance):
        return "near_zero"
    return "negative"


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    verify_sidecar(R476)
    r476 = json.loads(R476.read_text(encoding="utf-8"))
    tolerance = float(prereg["tolerance"]["bootstrap_near_zero_tolerance"])
    rows: list[dict[str, Any]] = []
    for sample in r476["samples"]:
        bootstrap = sample.get("validation_bootstrap")
        if not bootstrap:
            continue
        p05 = float(bootstrap["total_p05"])
        rows.append(
            {
                "label": sample["label"],
                "candidate": sample["added_factor"],
                "candidate_weight": sample["added_weight"],
                "training_gate": bool(sample["training_gate"]),
                "validation_p05": p05,
                "validation_total_p50": float(bootstrap["total_p50"]),
                "classification": classify_p05(p05, tolerance),
            }
        )
    counts = dict(Counter(row["classification"] for row in rows))
    payload = {
        "round": "R478",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_r476_sha256": sha(R476),
        "tolerance": tolerance,
        "near_zero_counts_as_positive": False,
        "hard_gate_changed": False,
        "sample_count": len(rows),
        "classifications": counts,
        "samples": rows,
        "decision": prereg["output_policy"],
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R478 因子库 bootstrap 尾部容忍度审计",
        "",
        "本轮只审计 R476 已冻结的验证期 bootstrap 尾部，不放宽主门槛，也不使用检验/近一年重新选样。",
        "",
        f"- 审计样本：{len(rows)}",
        f"- 正尾：{counts.get('positive', 0)}",
        f"- 近零尾（≥ -{tolerance:.2%}）：{counts.get('near_zero', 0)}",
        f"- 负尾：{counts.get('negative', 0)}",
        "",
        "## 分类",
        "",
        "| 样本 | 候选权重 | 验证 p05 | 分类 |",
        "| --- | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(f"| `{row['label']}` | {row['candidate_weight']:.0%} | {row['validation_p05']:.2%} | {row['classification']} |")
    lines += [
        "",
        "## 结论",
        "",
        "- “近零”只是更准确的尾部描述，不等同于通过 bootstrap，也不改变 R476 的拒绝状态。",
        "- `active_version` 仍为 null，不生成订单或生产配置。",
        "",
        "## 边界",
        "",
    ] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
