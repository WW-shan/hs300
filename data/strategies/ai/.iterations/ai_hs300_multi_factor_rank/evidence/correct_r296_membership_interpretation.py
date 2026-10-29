#!/usr/bin/env python3
"""Preserve an R296 post-run correction separating finance exclusions from member drift."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
PREREG = EVIDENCE / "r296_preregistration.json"
MANIFEST = EVIDENCE / "r296_input_manifest.jsonl"
OFFICIAL_PATH = EVIDENCE / "r296_official_members.json"
MACHINE_PATH = EVIDENCE / "r296_isolated_build_attachment_audit.json"
CORRECTION_PATH = EVIDENCE / "r296_membership_interpretation_erratum.json"
RESULT_V2 = EVIDENCE / "r296_isolated_build_attachment_audit_v2.json"
REPORT_V2 = EVIDENCE / "r296_result_v2.md"
TARGET = date(2026, 10, 27)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    digest = sha_bytes(payload)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def checked_json(path: Path) -> dict:
    expected = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
    actual = sha_file(path)
    if actual != expected:
        raise RuntimeError(f"SHA-256 mismatch: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    if any(path.exists() for path in (CORRECTION_PATH, RESULT_V2, REPORT_V2)):
        raise RuntimeError("R296 correction output exists; refusing overwrite")
    prereg = checked_json(PREREG)
    original = checked_json(MACHINE_PATH)
    official = checked_json(OFFICIAL_PATH)
    if prereg.get("round") != "R296" or original.get("round") != "R296":
        raise RuntimeError("R296 evidence identity mismatch")

    manifest_bytes = MANIFEST.read_bytes()
    manifest_hash = sha_bytes(manifest_bytes)
    if manifest_hash != prereg["frozen_context"]["input_manifest"]["sha256"]:
        raise RuntimeError("R296 manifest checksum mismatch")
    rows = [json.loads(line) for line in manifest_bytes.decode("utf-8").splitlines() if line]
    by_path = {row["path"]: row for row in rows}
    relevant = [
        "data/research/r78_daily_membership/daily_snapshots.parquet",
        "data/research/r157_membership/monthly_snapshots.parquet",
        "data/ext_data/ext_hy_ths/part.parquet",
    ]
    for relative in relevant:
        if relative not in by_path:
            raise RuntimeError(f"Missing frozen membership input: {relative}")
        if sha_file(ROOT / relative) != by_path[relative]["sha256"]:
            raise RuntimeError(f"Frozen membership source changed: {relative}")

    old = pl.read_parquet(ROOT / relevant[1]).select([
        pl.col("query_date").cast(pl.Date).alias("effective_date"),
        pl.col("symbol").cast(pl.String),
    ])
    new = pl.read_parquet(ROOT / relevant[0]).select([
        pl.col("query_date").cast(pl.Date).alias("effective_date"),
        pl.col("symbol").cast(pl.String),
    ])
    raw_membership = (
        pl.concat([old, new], how="diagonal_relaxed")
        .unique(subset=["effective_date", "symbol"], keep="last")
        .filter(pl.col("effective_date") <= TARGET)
        .sort(["effective_date", "symbol"])
    )
    snapshot_date = raw_membership["effective_date"].max()
    raw_snapshot = set(
        raw_membership.filter(pl.col("effective_date") == snapshot_date)["symbol"].to_list()
    )
    from app.services.industry_map import load_industry_l1

    industry = load_industry_l1(ROOT / "data")
    mapped = pl.DataFrame({"symbol": sorted(raw_snapshot)}).join(industry, on="symbol", how="left")
    missing_industry = mapped.filter(pl.col("industry_l1").is_null())["symbol"].to_list()
    excluded_finance = set(
        mapped.filter(pl.col("industry_l1").is_in(["银行", "非银金融"]))["symbol"].to_list()
    )
    eligible_nonfinancial = raw_snapshot - excluded_finance
    official_symbols = {item["symbol"] for item in official["members"]}
    raw_added = sorted(official_symbols - raw_snapshot)
    raw_removed = sorted(raw_snapshot - official_symbols)
    official_nonfinancial = official_symbols & eligible_nonfinancial

    coverage = original["coverage"]
    if coverage["r253_nonnull_official_current_members"] != len(official_nonfinancial):
        raise RuntimeError("Stored R253 coverage does not reconcile to eligible official members")
    if coverage["r257_nonnull_official_current_members"] != len(official_nonfinancial):
        raise RuntimeError("Stored R257 coverage does not reconcile to eligible official members")
    if len(eligible_nonfinancial) != coverage["r257_nonnull_historical_snapshot_members"]:
        raise RuntimeError("Eligible historical membership count does not reconcile")
    if missing_industry:
        raise RuntimeError(f"Industry mapping missing for snapshot members: {missing_industry[:5]}")

    exact = {
        "snapshot_date": snapshot_date.isoformat(),
        "snapshot_calendar_age_days_at_target": (TARGET - snapshot_date).days,
        "raw_historical_snapshot_members": len(raw_snapshot),
        "raw_historical_snapshot_overlap_with_next_day_official_list": len(raw_snapshot & official_symbols),
        "raw_members_added_by_next_day_official_list": raw_added,
        "raw_members_removed_by_next_day_official_list": raw_removed,
        "raw_membership_set_symmetric_difference_count": len(raw_added) + len(raw_removed),
        "historical_financial_sector_exclusions_by_strategy_contract": len(excluded_finance),
        "historical_eligible_nonfinancial_members": len(eligible_nonfinancial),
        "official_current_members_as_of_next_day": len(official_symbols),
        "official_current_nonfinancial_members_in_frozen_snapshot": len(official_nonfinancial),
        "official_current_financial_members_excluded_by_strategy_contract": len(official_symbols - official_nonfinancial),
        "eligible_historical_members_covered_by_r253": coverage["r253_nonnull_historical_snapshot_members"],
        "eligible_historical_members_covered_by_r257": coverage["r257_nonnull_historical_snapshot_members"],
        "official_current_eligible_members_covered_by_r253": coverage["r253_nonnull_official_current_members"],
        "official_current_eligible_members_covered_by_r257": coverage["r257_nonnull_official_current_members"],
    }
    if len(raw_added) + len(raw_removed) != 2:
        raise RuntimeError("Unexpected raw constituent-list difference")

    correction = {
        "round": "R296",
        "classification": "post_result_interpretation_erratum",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "original_preregistration_sha256": sha_file(PREREG),
        "original_machine_result_sha256": sha_file(MACHINE_PATH),
        "frozen_manifest_sha256": manifest_hash,
        "correction": (
            "The first readable report described 51 financial-sector exclusions as historical-vs-official membership drift. "
            "R253/R257 intentionally exclude banks and non-bank financials. The raw historical 300-member snapshot and "
            "the next-day official 300-member list differ by only one added and one removed symbol. Original files remain unchanged."
        ),
        "reconciled_membership": exact,
        "corrected_status": "isolated_build_attachment_coverage_pass_membership_freshness_unconfirmed",
        "status_basis": (
            "All frozen I2-I5 checks passed. Factor coverage is complete for the 249 eligible non-financial names. "
            "The latest historical snapshot is 13 calendar days before target and the official comparison is from the next day. "
            "No precommitted daily-membership freshness SLA exists, so no post-hoc hard gate is added."
        ),
        "limitations": [
            "Post-result correction; original preregistration, machine JSON and first report are preserved unchanged.",
            "The following-day official list is not a historical constituent record as of target date.",
            "This correction does not test returns, alpha or production readiness.",
        ],
    }
    correction_hash = write_once(
        CORRECTION_PATH,
        (json.dumps(correction, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    )

    corrected = dict(original)
    corrected["classification"] = "isolated_single_date_factor_build_and_system_attachment_corrected_membership_interpretation"
    corrected["status"] = correction["corrected_status"]
    corrected["membership_interpretation_erratum_sha256"] = correction_hash
    corrected["universe"] = dict(original["universe"])
    corrected["universe"].update(exact)
    for key in (
        "historical_snapshot_members",
        "historical_snapshot_official_overlap",
        "historical_snapshot_official_overlap_pct",
    ):
        corrected["universe"].pop(key, None)
    corrected["interpretation"] = {
        "all_precommitted_checks_pass": all(original["criteria"].values()),
        "strategy_universe_coverage_complete": (
            exact["eligible_historical_members_covered_by_r257"]
            == exact["historical_eligible_nonfinancial_members"]
        ),
        "raw_membership_added": raw_added,
        "raw_membership_removed": raw_removed,
        "freshness_sla_precommitted": False,
        "operational_current_member_freshness": "unconfirmed_due_to_13_day_snapshot_age_and_next_day_reference",
    }
    corrected_hash = write_once(
        RESULT_V2,
        (json.dumps(corrected, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    )

    report = f"""# R296 结果更正：成分差异与金融行业排除

R296 首份可读报告把 R253/R257 明确排除的 51 只金融行业股票，误写成历史快照与官方名单之间的 51 只成员差异。现已按生产成员函数的真实处理顺序重算口径。原预注册、首份机器结果和首份报告均保留不覆盖；以本更正和 v2 结果为准。

## 正确的名单口径

- 目标日：2026-10-27。生产历史成员输入的最新原始快照是 {snapshot_date}，含 {len(raw_snapshot)} 只；对照的官方名单 as-of {official.get('as_of')}，也是 300 只，但它在目标日之后取得，不能当成目标日历史真值。
- 两份原始 300 只名单只有 {len(raw_added) + len(raw_removed)} 个代码不同：次日官方名单新增 {', '.join(raw_added)}，移除 {', '.join(raw_removed)}；原始交集 {len(raw_snapshot & official_symbols)}/300。
- R253/R257 按冻结策略口径排除银行和非银金融 {len(excluded_finance)} 只。历史快照下 eligible 非金融集合为 {len(eligible_nonfinancial)} 只，R253/R257 覆盖 {exact['eligible_historical_members_covered_by_r257']}/{len(eligible_nonfinancial)}。
- 次日官方名单与该冻结 eligible 非金融集合的交集为 {len(official_nonfinancial)} 只；R253 和 R257 各覆盖 {exact['official_current_eligible_members_covered_by_r257']}/{len(official_nonfinancial)}。另外 51 只属于策略定义排除的金融股，不应计作因子缺失。

## 更正后的结论

冻结的 I2-I5 检查全部通过。目标日行业相对强度为 547/557，三项质量输入同时可用 474/557，R253/R257 因子均覆盖 249 个 eligible 非金融历史成分；系统真实 attach_ext_columns(include_snapshot=False) 分别装配 249/249 个非空值，逐项相等，无额外装配值。隔离构建和精确系统装配通过。

名单新鲜度仍需谨慎：最新原始快照距离目标日 13 个自然日，官方对照来自次日；两者仅一股替换，但它不证明目标日真实历史名单。预注册没有规定日级新鲜度 SLA，因此不事后制造硬失败门槛；状态记作“覆盖/装配通过，当前成分新鲜度未确认”。这不等于生产就绪，也不验证 alpha、财务原始公告版本级 PIT、收益或成交。

## 保护与证据

首份机器结果 SHA-256 {sha_file(MACHINE_PATH)}；更正 SHA-256 {correction_hash}；v2 机器结果 SHA-256 {corrected_hash}。冻结输入 2,624 个文件均未变，正式三层因子 6,396 个分区哈希前后相同。面板候选 48ca7b2f1105431d97c148e3e0574f4c 单次登记/读回成功，状态 pending。未运行回测、provider、筛选或撮合，未读取收益/成交。

R257 保持 research-only，生产维持 pause_production。
"""
    report_hash = write_once(REPORT_V2, report.encode("utf-8"))
    print(json.dumps({
        "round": "R296",
        "raw_historical_members": len(raw_snapshot),
        "official_members": len(official_symbols),
        "raw_membership_added": raw_added,
        "raw_membership_removed": raw_removed,
        "financial_members_excluded_by_strategy": len(excluded_finance),
        "eligible_nonfinancial_members": len(eligible_nonfinancial),
        "r257_eligible_coverage": exact["eligible_historical_members_covered_by_r257"],
        "status": corrected["status"],
        "correction_sha256": correction_hash,
        "machine_v2_sha256": corrected_hash,
        "report_v2_sha256": report_hash,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
