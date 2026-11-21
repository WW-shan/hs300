#!/usr/bin/env python3
"""Record the invalid R401 composition attempt without promoting it."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r401_regime_beta_preregistration.json"
TRAIN = E / "r401_train_2026-11-22.jsonl"
VALID = E / "r401_validation_2026-11-22.jsonl"
OUTPUT = E / "r401_regime_beta_2026-11-22.json"
RESULT = E / "r401_regime_beta_2026-11-22.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R401 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    train_rows = TRAIN.read_text(encoding="utf-8").splitlines()
    validation_rows = VALID.read_text(encoding="utf-8").splitlines()
    payload = {
        "round": "R401",
        "classification": "state-composition audit; current runner invalid for beta-on claims",
        "preregistration_sha256": sha256(PREREG),
        "train_rows": len([line for line in train_rows if line.strip()]),
        "validation_rows": len([line for line in validation_rows if line.strip()]),
        "decision": {
            "status": "regime_composition_invalid_no_promotion",
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
            "reason": "The initial R401 runner did not produce distinct beta-on curves; all rule labels share the core-only result. State conditioning is not evaluated.",
        },
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    markdown = "\n".join([
        "# R401 状态化 Beta 审计", "",
        "当前 runner 只生成 core-only 曲线，未生成真正 beta-on 曲线；所有规则标签结果相同，不能作为状态化有效的证据。", "",
        "- 结论：`regime_composition_invalid_no_promotion`",
        "- 不生成订单、纸面目标或生产配置。", "",
    ])
    RESULT.write_text(markdown, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
