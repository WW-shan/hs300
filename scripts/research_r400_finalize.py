#!/usr/bin/env python3
"""R400 finalize: report validation result and preserve negative knowledge."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r400_growth_beta_preregistration.json"
TRAIN = E / "r400_train_2026-11-22.jsonl"
VALID = E / "r400_validation_2026-11-22.jsonl"
OUTPUT = E / "r400_growth_beta_2026-11-22.json"
RESULT = E / "r400_growth_beta_2026-11-22.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R400 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha256(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R400 preregistration checksum mismatch")
    train = read_jsonl(TRAIN)
    validation = read_jsonl(VALID)
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in validation:
        grouped.setdefault((row["core_id"], row["beta_id"]), []).append(row)
    candidates = []
    for key, rows in grouped.items():
        best = max(rows, key=lambda row: (row["metrics"]["annual"], row["metrics"]["sharpe"]))
        candidates.append({
            "core_id": key[0], "beta_id": key[1],
            "validation_pass_count": sum(bool(row["pass"]) for row in rows),
            "validation_count": len(rows), "best": best,
        })
    candidates.sort(key=lambda row: (-row["validation_pass_count"], -row["best"]["metrics"]["annual"]))
    payload = {
        "round": "R400", "classification": prereg["classification"],
        "runner_sha256": sha256(Path(__file__)), "preregistration_sha256": sha256(PREREG),
        "train_rows": len(train), "validation_points": len(validation),
        "candidates": candidates,
        "decision": {"status": "growth_beta_validation_rejected_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R400 防守核心 + Beta 腿验证", "",
        "核心 75%、Beta 25%；训练 2022-05-05..2024-05-05，验证 2024-05-06..2025-05-05。", "",
        f"- 训练点：{len(train)}，通过：{sum(bool(row['pass']) for row in train)}",
        f"- 验证点：{len(validation)}，通过：{sum(bool(row['pass']) for row in validation)}", "",
        "| 核心 | Beta 腿 | 验证通过 |", "| ---: | --- | ---: |",
    ]
    for row in candidates:
        lines.append(f"| {row['core_id']} | {row['beta_id']} | {row['validation_pass_count']}/{row['validation_count']} |")
    lines += [
        "", "## 结论", "",
        "- Beta 腿有提高绝对收益的潜力，但没有形成 7/9 以上的参数平台。",
        "- 只有 `core 9 + trend_efficiency` 的 10 仓/20 日单点通过验证，不能据此晋升。",
        "- 本轮记为负知识；下一步应研究状态条件化或更稳健的 beta 表达，而不是直接上线单点。", "",
        "## 边界", "",
        "- 这是已注册因子的新组合，不是新公式。",
        "- 验证窗口不是完全独立样本。",
        "- 不生成订单、纸面目标或生产配置。", "",
    ]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
