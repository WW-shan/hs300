#!/usr/bin/env python3
"""Record the corrected per-symbol eligible universe for R296."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
TARGET = date(2026, 10, 27)
FILES = {
    "official": EVIDENCE / "r296_official_members.json",
    "result": EVIDENCE / "r296_isolated_build_attachment_audit.json",
    "prior_erratum": EVIDENCE / "r296_membership_interpretation_erratum.json",
    "erratum": EVIDENCE / "r296_membership_eligibility_erratum.json",
    "result_v3": EVIDENCE / "r296_isolated_build_attachment_audit_v3.json",
    "report_v3": EVIDENCE / "r296_result_v3.md",
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def checked(path: Path) -> dict:
    if sha(path) != path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"SHA-256 mismatch: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    value = hashlib.sha256(payload).hexdigest()
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{value}  {path.name}\n")
    return value


def main() -> int:
    if any(FILES[name].exists() for name in ("erratum", "result_v3", "report_v3")):
        raise RuntimeError("R296 eligibility correction already exists")
    official_payload = checked(FILES["official"])
    machine = checked(FILES["result"])
    prior = checked(FILES["prior_erratum"])
    official = {item["symbol"] for item in official_payload["members"]}

    from app.services.industry_map import load_industry_l1

    industry = load_industry_l1(ROOT / "data")
    current_map = pl.DataFrame({"symbol": sorted(official)}).join(industry, on="symbol", how="left")
    if current_map["industry_l1"].null_count():
        raise RuntimeError("Official member industry mapping is incomplete")
    current_finance = set(current_map.filter(
        pl.col("industry_l1").is_in(["银行", "非银金融"])
    )["symbol"].to_list())
    current_eligible = official - current_finance

    history = pl.read_parquet(ROOT / "data/research/r78_daily_membership/daily_snapshots.parquet")
    history = history.filter(pl.col("query_date") <= TARGET)
    snapshot_date = history["query_date"].max()
    snapshot = set(history.filter(pl.col("query_date") == snapshot_date)["symbol"].to_list())
    snapshot_map = pl.DataFrame({"symbol": sorted(snapshot)}).join(industry, on="symbol", how="left")
    if snapshot_map["industry_l1"].null_count():
        raise RuntimeError("Historical member industry mapping is incomplete")
    historical_eligible = set(snapshot_map.filter(
        ~pl.col("industry_l1").is_in(["银行", "非银金融"])
    )["symbol"].to_list())

    added_raw = sorted(official - snapshot)
    removed_raw = sorted(snapshot - official)
    added_eligible = sorted(current_eligible - historical_eligible)
    removed_eligible = sorted(historical_eligible - current_eligible)
    cov = machine["coverage"]
    historical_coverage = cov["r257_nonnull_historical_snapshot_members"]
    official_coverage = cov["r257_nonnull_official_current_members"]
    if len(historical_eligible) != historical_coverage:
        raise RuntimeError("Historical eligible count does not reconcile with stored factor coverage")
    if len(current_eligible) != 250 or official_coverage != 249:
        raise RuntimeError("Official current eligible coverage does not reconcile")
    if added_raw != ["002294.SZ"] or removed_raw != ["601059.SH"]:
        raise RuntimeError("Unexpected raw membership changes")
    if added_eligible != ["002294.SZ"] or removed_eligible:
        raise RuntimeError("Unexpected eligible membership changes")

    detail = {
        "historical_snapshot_date": str(snapshot_date),
        "target_date": TARGET.isoformat(),
        "snapshot_age_calendar_days": (TARGET - snapshot_date).days,
        "historical_raw_members": len(snapshot),
        "historical_financial_exclusions": len(snapshot - historical_eligible),
        "historical_eligible_nonfinancial_members": len(historical_eligible),
        "next_day_official_list_as_of": official_payload["as_of"],
        "official_raw_members": len(official),
        "official_financial_exclusions": len(current_finance),
        "official_eligible_nonfinancial_members": len(current_eligible),
        "raw_added_members": added_raw,
        "raw_removed_members": removed_raw,
        "eligible_added_members": added_eligible,
        "eligible_removed_members": removed_eligible,
        "historical_eligible_r257_coverage": historical_coverage,
        "official_eligible_r257_coverage": official_coverage,
        "official_eligible_r257_coverage_fraction": round(official_coverage / len(current_eligible), 6),
        "uncovered_official_eligible_members": added_eligible,
    }
    erratum = {
        "round": "R296",
        "classification": "second_post_result_membership_eligibility_correction",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "prior_erratum_sha256": sha(FILES["prior_erratum"]),
        "original_result_sha256": sha(FILES["result"]),
        "correction": (
            "The first report confused strategy-excluded financial symbols with missing data. The first correction then "
            "used the historical eligible subset as the next-day official eligible denominator. Evaluate industry eligibility "
            "per symbol: 002294.SZ is eligible, while removed 601059.SH is excluded financial. The current eligible universe "
            "is 250; 249 frozen-date factors are present. Earlier evidence remains unchanged."
        ),
        "membership": detail,
        "interpretation": "Historical snapshot eligible universe coverage is 249/249. Next-day official eligible universe coverage is 249/250; the uncovered code was added after the last historical snapshot.",
        "limitations": [
            "Post-result correction, preserving all earlier evidence files.",
            "Next-day official membership is not target-date historical membership evidence.",
            "Current industry mapping does not establish historical point-in-time sector classification.",
            "No return, alpha or production-readiness test was run.",
        ],
    }
    erratum_hash = write_once(FILES["erratum"], (json.dumps(erratum, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

    v3 = dict(machine)
    v3["classification"] = "isolated_single_date_build_attachment_membership_eligibility_corrected"
    v3["status"] = "historical_eligible_coverage_pass_official_next_day_eligible_coverage_249_of_250"
    v3["membership_eligibility"] = detail
    v3["membership_eligibility_erratum_sha256"] = erratum_hash
    v3["interpretation"] = {
        "precommitted_checks_pass": all(machine["criteria"].values()),
        "historical_snapshot_eligible_coverage_complete": historical_coverage == len(historical_eligible),
        "next_day_official_eligible_coverage_complete": official_coverage == len(current_eligible),
        "next_day_official_eligible_coverage_fraction": round(official_coverage / len(current_eligible), 6),
        "missing_current_eligible_symbol_due_to_snapshot_lag": added_eligible,
        "freshness_sla_precommitted": False,
        "production_ready": False,
    }
    v3_hash = write_once(FILES["result_v3"], (json.dumps(v3, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    report = f"""# R296 第二项结果更正：当前名单有效成分覆盖

原预注册、首份结果和第一项更正均保留。本更正重新按每只成分股的行业分类计算策略 eligible universe。

历史快照为 {snapshot_date}，距目标日 {(TARGET - snapshot_date).days} 天，原始 300 只。按策略规则排除 {len(snapshot - historical_eligible)} 只银行/非银金融后，有效集合 249 只；R257 覆盖 249/249。

次日官方名单 as-of {official_payload['as_of']} 有 300 只，其中 50 只按同一规则排除，当前有效集合为 250 只。两份原始名单新增 {', '.join(added_raw)}、移除 {', '.join(removed_raw)}。新增代码是非金融，进入当前 eligible universe；移除代码属于非银金融，本来就被策略排除。因此冻结因子只覆盖次日官方有效名单的 249/250，缺少新增的 {', '.join(added_eligible)}。

生产计算与系统装配在历史 eligible 集合上完整通过。次日官方 eligible 集合覆盖率为 99.6%；要声称当前名单完整覆盖，必须先更新历史成员输入并重建因子。本轮没有执行生产更新。没有预注册新鲜度硬门槛，故不改动原预承诺计算检查；不把该结果解释为收益、alpha、历史 PIT 或生产就绪。

第二项更正 SHA-256 {erratum_hash}；机器结果 v3 SHA-256 {v3_hash}。首份结果 SHA-256 {sha(FILES['result'])}，第一项更正 SHA-256 {sha(FILES['prior_erratum'])}，均未覆盖。R257 继续 research-only，生产 pause_production。
"""
    report_hash = write_once(FILES["report_v3"], report.encode("utf-8"))
    print(json.dumps({
        "round": "R296",
        "historical_eligible": len(historical_eligible),
        "historical_coverage": historical_coverage,
        "official_current_eligible": len(current_eligible),
        "official_current_coverage": official_coverage,
        "coverage_fraction": detail["official_eligible_r257_coverage_fraction"],
        "uncovered_member": added_eligible,
        "status": v3["status"],
        "erratum_sha256": erratum_hash,
        "machine_v3_sha256": v3_hash,
        "report_v3_sha256": report_hash,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
