"""R390: deterministic candidate construction for the remaining factor library.

This stage is deliberately read-only and does not evaluate portfolio returns.
It turns P6 admission, parameter-family metadata and prior P5/P8 research into
an auditable list of stock-alpha candidates that have not already been studied.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
DATA = ROOT / "data"
FACTOR_ROOT = DATA / "factor_research"
EVIDENCE = Path(__file__).resolve().parent
PREREG = EVIDENCE / "r390_factor_library_screening_preregistration.json"
P6_RUN = FACTOR_ROOT / "automation/runs/1513dbc1e614f6ab31552bdb18771bdecf3162ad3b343e2681bda9b2e9cba298.json"
FAMILIES = FACTOR_ROOT / "parameters/families.json"
P5 = FACTOR_ROOT / "research/p5_incremental_2026-11-12.json"
P8 = FACTOR_ROOT / "research/p8_financial_increment_2026-11-12.json"
OUTPUT = EVIDENCE / "r390_factor_library_screening.json"
RESULT = EVIDENCE / "r390_factor_library_screening_result.md"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists():
        raise RuntimeError(f"missing checksum sidecar: {sidecar.name}")
    expected = sidecar.read_text(encoding="ascii").split()[0]
    if sha256(path) != expected:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def ready_factors() -> list[str]:
    payload = _load(P6_RUN)
    return sorted(
        factor_id
        for factor_id, row in payload["admission"].items()
        if row.get("portfolio_ready") is True
    )


def fixed_factors() -> list[str]:
    return sorted(_load(FAMILIES).get("fixed_factor_ids", []))


def blocked_factors() -> list[str]:
    return sorted(_load(FAMILIES).get("blocked_factor_ids", []))


def studied_factors() -> set[str]:
    studied: set[str] = set()
    p5 = _load(P5)
    studied.update(
        row.get("factor_id")
        for row in p5.get("candidates", [])
        if row.get("factor_id")
    )
    p8 = _load(P8)
    studied.update(
        row.get("factor_id")
        for row in p8.get("candidates", [])
        if row.get("factor_id")
    )
    return studied


def _is_conditioner(factor_id: str, admission: dict[str, Any]) -> bool:
    lowered = factor_id.lower()
    return bool(
        admission.get("classification_status") in {"conditioner", "regime"}
        or lowered.endswith("_conditioner")
        or any(token in lowered for token in ("market_emotion", "market_trend", "market_profit", "market_speculation"))
    )


def exclusion_audit() -> dict[str, Any]:
    p6 = _load(P6_RUN)
    admission = p6["admission"]
    ready = set(ready_factors())
    fixed = set(fixed_factors())
    blocked = set(blocked_factors())
    studied = studied_factors()
    family_payload = _load(FAMILIES)
    family_members = {
        factor_id: family["family_id"]
        for family in family_payload.get("families", [])
        for factor_id in family.get("member_factor_ids", [])
    }
    excluded = {
        "studied": set(),
        "fixed": set(),
        "blocked": set(),
        "conditioner": set(),
        "duplicate_family": set(),
        "not_ready": set(),
    }
    eligible = []
    for factor_id in sorted(ready):
        row = admission[factor_id]
        if factor_id in studied:
            excluded["studied"].add(factor_id)
            continue
        if factor_id in fixed:
            excluded["fixed"].add(factor_id)
            continue
        if factor_id in blocked or row.get("classification_status") == "blocked":
            excluded["blocked"].add(factor_id)
            continue
        if _is_conditioner(factor_id, row):
            excluded["conditioner"].add(factor_id)
            continue
        if factor_id not in family_members:
            # Unmapped factors are allowed here only if they are not conditioners;
            # family deduplication is handled below for mapped members.
            pass
        eligible.append(factor_id)
    # Keep one lexicographic representative per mapped family.
    chosen: dict[str, str] = {}
    final: list[str] = []
    for factor_id in eligible:
        family = family_members.get(factor_id)
        if family is None:
            final.append(factor_id)
            continue
        previous = chosen.get(family)
        if previous is None:
            chosen[family] = factor_id
            final.append(factor_id)
        else:
            excluded["duplicate_family"].add(factor_id)
    return {
        "raw_ready_count": len(ready),
        "excluded_studied_count": len(excluded["studied"]),
        "excluded_fixed_count": len(excluded["fixed"]),
        "excluded_blocked_count": len(excluded["blocked"]),
        "excluded_conditioner_count": len(excluded["conditioner"]),
        "excluded_duplicate_family_count": len(excluded["duplicate_family"]),
        "eligible_count": len(final),
        "eligible_factor_ids": final,
        "ready_factor_ids": sorted(ready),
        "excluded_factor_ids": {key: sorted(value) for key, value in excluded.items() if value},
    }


def eligible_factors() -> list[str]:
    return list(exclusion_audit()["eligible_factor_ids"])


def family_representatives() -> list[str]:
    return eligible_factors()


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R390 output exists; refusing overwrite: {path.name}")


def build_report() -> dict[str, Any]:
    verify_sidecar(PREREG)
    audit = exclusion_audit()
    return {
        "round": "R390",
        "classification": "incremental stock-alpha screening of remaining factor library",
        "runner_sha256": sha256(Path(__file__)),
        "preregistration_sha256": sha256(PREREG),
        "candidate_audit": audit,
        "candidate_count": audit["eligible_count"],
        "no_leverage": True,
        "decision": {
            "status": "screening_complete_no_promotion",
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }


def markdown(report: dict[str, Any]) -> str:
    audit = report["candidate_audit"]
    return "\n".join([
        "# R390 · 因子库剩余增量候选筛查",
        "",
        "R390 只从 P6 portfolio-ready 因子中构建未研究候选集，不新增公式，不生成订单。",
        "",
        f"- 原始 ready：{audit['raw_ready_count']}",
        f"- 已研究排除：{audit['excluded_studied_count']}",
        f"- fixed 排除：{audit['excluded_fixed_count']}",
        f"- blocked 排除：{audit['excluded_blocked_count']}",
        f"- conditioner 排除：{audit['excluded_conditioner_count']}",
        f"- 同族重复排除：{audit['excluded_duplicate_family_count']}",
        f"- 最终候选：{audit['eligible_count']}",
        "",
        "## 结论边界",
        "",
        "- 本阶段只完成候选构建，不声称组合有效。",
        "- 不生成订单、纸面目标或生产配置。",
        "- 5× 仅 warning，不硬否决。",
        "",
    ])


def write_json_with_checksum(path: Path, payload: dict[str, Any]) -> None:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(raw)
    path.with_name(path.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n", encoding="ascii")


def write_text_with_checksum(path: Path, text: str) -> None:
    raw = text.encode("utf-8")
    with path.open("xb") as stream:
        stream.write(raw)
    path.with_name(path.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n", encoding="ascii")


def main() -> int:
    ensure_outputs_absent()
    report = build_report()
    write_json_with_checksum(OUTPUT, report)
    write_text_with_checksum(RESULT, markdown(report))
    print(json.dumps({"candidate_count": report["candidate_count"], "no_leverage": report["no_leverage"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
