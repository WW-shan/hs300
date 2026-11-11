#!/usr/bin/env python3
"""Publish the P7 financial point-in-time audit and bind it to the ledger."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.factor_research.automation import bind_research_evidence
from app.factor_research.financial_pit import audit_financial_pit
from app.factor_research.fingerprints import hash_token, sha256_json, utc_now
from app.factor_research.locking import atomic_write_json, atomic_write_text

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PIT_DIR = DATA / "factor_research" / "pit"
RUNS = PIT_DIR / "runs"
LATEST = PIT_DIR / "latest.json"
OPERATION = "financial_pit_audit_published"
METHOD_VERSION = "financial_pit_audit_v1"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# P7 财务 PIT 审计",
        "",
        f"- Research ID：`{payload['research_id']}`",
        f"- 状态：`{payload['status']}`",
        f"- 样本：{payload['universe']['symbol_count']} 只当前 HS300",
        f"- 区间：{payload['period']['start']} 至 {payload['period']['end']}",
        f"- 通过因子：{len(payload['verified_factor_ids'])} / {len(payload['factors'])}",
        "",
        "## 因子结果",
        "",
        "| 因子 | 状态 | 非空行 | 不一致行 | 首个有效日 |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for factor_id, row in sorted(payload["factors"].items()):
        lines.append(
            f"| `{factor_id}` | {row['status']} | {row.get('non_null_rows', 0)} | "
            f"{row.get('mismatch_count', 0)} | {row.get('first_value_date') or '-'} |"
        )
    lines.extend([
        "",
        "## 口径",
        "",
        "- 内置财务因子按公告日下一交易日生效，公告日当天保持 null。",
        "- 股息字段要求公告日严格早于信号日，且事件位于 730 日窗口内。",
        "- 相同生效日期的多期财务报告按最新 `period_end` 取一行，避免 join_asof 并列竞态。",
        "- HS300 当前成分回填历史，幸存者偏差仍在。",
        "",
    ])
    return "\n".join(lines)


def _publish(payload: dict[str, Any]) -> tuple[dict[str, Any], Path, Path]:
    token = hash_token(payload["research_id"])
    run_json = RUNS / f"{token}.json"
    run_markdown = run_json.with_suffix(".md")
    if run_json.exists():
        existing = _load_json(run_json)
        if existing.get("research_id") != payload["research_id"]:
            raise FileExistsError(f"PIT run identity mismatch: {run_json}")
        if not run_markdown.exists():
            raise FileNotFoundError(run_markdown)
        return existing, run_json, run_markdown
    atomic_write_json(run_json, payload)
    atomic_write_text(run_markdown, _render_markdown(payload))
    return payload, run_json, run_markdown


def _bind(
    payload: dict[str, Any],
    run_json: Path,
    run_markdown: Path,
) -> dict[str, Any]:
    record, _created = bind_research_evidence(
        DATA,
        operation=OPERATION,
        evidence_paths=[run_json, run_markdown],
        research_id=payload["research_id"],
        payload={
            "input_fingerprints": {
                "pit_audit_id": payload["research_id"],
                **payload["inputs"],
            },
            "metrics": {
                "factor_count": len(payload["factors"]),
                "verified_factor_count": len(payload["verified_factor_ids"]),
                "failed_factor_count": len(payload["factors"]) - len(payload["verified_factor_ids"]),
                "status_counts": dict(Counter(
                    row["status"] for row in payload["factors"].values()
                )),
            },
            "verdict": payload["status"],
        },
    )
    return {
        "record_id": record.record_id,
        "record_hash": record.record_hash,
    }


def _publish_latest(
    payload: dict[str, Any],
    run_json: Path,
    run_markdown: Path,
    ledger: dict[str, Any],
) -> None:
    pointer = {
        "schema_version": 1,
        "research_id": payload["research_id"],
        "status": payload["status"],
        "generated_at": payload["generated_at"],
        "run_json": run_json.relative_to(DATA).as_posix(),
        "run_markdown": run_markdown.relative_to(DATA).as_posix(),
        "ledger_record_id": ledger["record_id"],
        "ledger_record_hash": ledger["record_hash"],
        "verified_factor_ids": payload["verified_factor_ids"],
    }
    if LATEST.exists() and _load_json(LATEST) == pointer:
        return
    atomic_write_json(LATEST, pointer)


def main() -> int:
    result = audit_financial_pit(DATA)
    core = {
        "schema_version": result["schema_version"],
        "method_version": METHOD_VERSION,
        "period": result["period"],
        "universe": result["universe"],
        "inputs": result["inputs"],
        "factors": result["factors"],
        "verified_factor_ids": result["verified_factor_ids"],
        "status": result["status"],
    }
    research_id = sha256_json(core)
    payload = {
        **core,
        "research_id": research_id,
        "generated_at": utc_now(),
    }
    published, run_json, run_markdown = _publish(payload)
    ledger = _bind(published, run_json, run_markdown)
    _publish_latest(published, run_json, run_markdown, ledger)
    print(json.dumps({
        "research_id": published["research_id"],
        "status": published["status"],
        "verified_factor_count": len(published["verified_factor_ids"]),
        "factor_count": len(published["factors"]),
        "run_json": run_json.relative_to(DATA).as_posix(),
        "ledger_record_id": ledger["record_id"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
