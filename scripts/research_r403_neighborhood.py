#!/usr/bin/env python3
"""R403 follow-up: neighbourhood audit for frozen core+HS300 blends."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
SOURCE = E / "r403_core_beta_2026-11-22.json"
PREREG = E / "r403_neighborhood_preregistration.json"
OUTPUT = E / "r403_neighborhood_2026-11-22.json"
RESULT = E / "r403_neighborhood_2026-11-22.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R403 neighborhood exists: {path.name}")
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows = source["rows"]
    selected = [row for row in rows if row["core_weight"] in (0.7, 0.8, 0.9)]
    if not PREREG.exists():
        prereg = {
            "round": "R403N",
            "classification": "frozen core+HS300 blend neighbourhood audit; no promotion",
            "source_sha256": sha256(SOURCE),
            "variants": [row["core_weight"] for row in selected],
            "neighbourhood_rule": "+/- 10 percentage points core weight, evaluated over the frozen grid",
            "output_policy": {"status": "core_beta_neighborhood_complete_no_promotion", "active_version": None},
        }
        raw = (json.dumps(prereg, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        PREREG.write_bytes(raw)
        PREREG.with_name(PREREG.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {PREREG.name}\n", encoding="ascii")
    if sha256(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R403 neighborhood preregistration changed")
    audit = []
    for row in selected:
        neighbours = [item for item in rows if abs(item["core_weight"] - row["core_weight"]) <= 0.1000001]
        audit.append({
            "core_weight": row["core_weight"],
            "neighbour_core_weights": [item["core_weight"] for item in neighbours],
            "neighbour_pass_count": sum(bool(item["pass"]) for item in neighbours),
            "neighbour_count": len(neighbours),
            "stable": all(item["pass"] for item in neighbours),
        })
    payload = {
        "round": "R403N", "classification": "frozen core+HS300 blend neighbourhood audit; no promotion",
        "runner_sha256": sha256(Path(__file__)), "preregistration_sha256": sha256(PREREG), "source_sha256": sha256(SOURCE),
        "audit": audit,
        "decision": {"status": "core_beta_neighborhood_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R403 核心+HS300 邻域复核", "", "| 核心权重 | 邻域 | 通过 | 稳定 |", "| ---: | --- | ---: | :---: |"]
    for item in audit:
        lines.append(f"| {item['core_weight']:.0%} | {item['neighbour_core_weights']} | {item['neighbour_pass_count']}/{item['neighbour_count']} | {'是' if item['stable'] else '否'} |")
    lines += ["", "## 边界", "", "- 这是已冻结混合比例的邻域检查，不再重新挑比例。", "- 不生成订单、纸面目标或生产配置。", ""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
