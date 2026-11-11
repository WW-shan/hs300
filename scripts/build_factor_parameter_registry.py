#!/usr/bin/env python3
"""Build the immutable factor parameter classification report."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from app.factor_research.catalog import load_latest_catalog
from app.factor_research.fingerprints import sha256_file, utc_now
from app.factor_research.ledger import ResearchLedger
from app.factor_research.locking import atomic_write_json, atomic_write_text
from app.factor_research.parameters import (
    build_classification_payload,
    load_parameter_registry,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_OUTPUT = DATA / "factor_research" / "parameters" / "reports" / "classification_2026-11-11.json"
OPERATION = "factor_parameter_classification_published"


def _repo_output_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _render_markdown(payload: dict) -> str:
    counts = payload["counts"]
    lines = [
        "# 因子参数族分类报告",
        "",
        f"- 报告 ID：`{payload['report_id']}`",
        f"- 生成时间：`{payload['generated_at']}`",
        f"- Catalog：`{payload['catalog']['snapshot_id']}`",
        f"- 注册因子：{counts['total']}",
        f"- 参数族：{payload['registry']['family_count']}",
        "",
        "## 分类汇总",
        "",
        "| 状态 | 数量 |",
        "| --- | ---: |",
        f"| tunable | {counts['tunable']} |",
        f"| fixed | {counts['fixed']} |",
        f"| grouped | {counts['grouped']} |",
        f"| blocked | {counts['blocked']} |",
        f"| unclassified | {counts['unclassified']} |",
        "",
        "`unclassified` 为 fail-closed 结果：这些因子出现数字窗口迹象，但尚未登记参数族，不能进入调参或发布。",
        "",
        "## 参数族",
        "",
        "| 参数族 | 参数网格 | 当前参数 | 成员数 | 状态 |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for family in payload["families"]:
        grid = ", ".join(str(value) for value in family["parameter_grid"])
        lines.append(
            f"| `{family['family_id']}` | {grid} | {family['current_parameter']} | "
            f"{family['member_count']} | {family['status']} |"
        )
    lines.extend([
        "",
        "## 因子状态",
        "",
        "| 因子 | 状态 | 参数族 | 识别参数 | 原因 |",
        "| --- | --- | --- | ---: | --- |",
    ])
    for row in payload["factors"]:
        family = f"`{row['family_id']}`" if row["family_id"] else "-"
        parameter = row["parameter"] if row["parameter"] is not None else "-"
        lines.append(
            f"| `{row['factor_id']}` | {row['status']} | {family} | {parameter} | {row['reason']} |"
        )
    lines.append("")
    return "\n".join(lines)


def _publish_json(path: Path, payload: dict) -> dict:
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("report_id") != payload["report_id"]:
            raise FileExistsError(f"classification report already exists: {path}")
        return existing
    atomic_write_json(path, payload)
    return payload


def _publish_markdown(path: Path, payload: dict) -> None:
    text = _render_markdown(payload)
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise FileExistsError(f"classification markdown already exists: {path}")
        return
    atomic_write_text(path, text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT.relative_to(ROOT)),
        help="Repository-relative JSON output path.",
    )
    parser.add_argument("--generated-at", default=None)
    args = parser.parse_args()

    output = _repo_output_path(args.output)
    data_root = DATA.resolve()
    if not output.resolve().is_relative_to(data_root):
        raise ValueError("classification output must stay inside the repository data directory")

    catalog = load_latest_catalog(DATA)
    if catalog is None:
        raise RuntimeError("factor catalog snapshot is unavailable")
    if catalog.source_completeness != "complete":
        raise RuntimeError(
            f"factor catalog source completeness is {catalog.source_completeness}"
        )
    registry = load_parameter_registry(DATA)
    payload = build_classification_payload(
        catalog,
        registry,
        generated_at=args.generated_at or utc_now(),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = _publish_json(output, payload)
    markdown_path = output.with_suffix(".md")
    _publish_markdown(markdown_path, payload)

    ledger = ResearchLedger(DATA)
    ledger.verify()
    existing = [
        record for record in ledger.records()
        if record.operation == OPERATION
        and record.input_fingerprints.get("report_id") == payload["report_id"]
    ]
    if not existing:
        counts = payload["counts"]
        ledger.append(
            OPERATION,
            payload={
                "catalog_snapshot_id": payload["catalog"]["snapshot_id"],
                "input_fingerprints": {
                    "report_id": payload["report_id"],
                    "catalog_registry_fingerprint": payload["catalog"]["registry_fingerprint"],
                    "parameter_registry_sha256": sha256_file(
                        DATA / "factor_research" / "parameters" / "families.json"
                    ),
                    "output_sha256": sha256_file(output),
                    "markdown_sha256": sha256_file(markdown_path),
                },
                "evidence_refs": [
                    {"kind": "factor_parameter_classification", "path": str(output.relative_to(DATA))},
                    {"kind": "factor_parameter_classification_markdown", "path": str(markdown_path.relative_to(DATA))},
                ],
                "metrics": counts,
                "verdict": "classification_only_no_active_version",
            },
        )

    status_counts = Counter(row["status"] for row in payload["factors"])
    print(json.dumps({
        "report_id": payload["report_id"],
        "output": str(output.relative_to(ROOT)),
        "markdown": str(markdown_path.relative_to(ROOT)),
        "counts": dict(sorted(status_counts.items())),
        "family_count": payload["registry"]["family_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
