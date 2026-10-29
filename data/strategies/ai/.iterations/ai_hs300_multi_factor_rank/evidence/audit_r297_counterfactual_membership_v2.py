#!/usr/bin/env python3
"""Audit R253/R257 coverage with an isolated, counterfactual membership snapshot."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[6]
DATA = ROOT / "data"
EVIDENCE = Path(__file__).resolve().parent
PRE = EVIDENCE / "r297_preregistration.json"
MANIFEST = EVIDENCE / "r297_input_manifest.jsonl"
OFFICIAL = EVIDENCE / "r297_official_current_members.json"
BASELINE = EVIDENCE / "r297_formal_factor_baseline.json"
RESULT = EVIDENCE / "r297_counterfactual_membership_audit_v2.json"
REPORT = EVIDENCE / "r297_result_v2.md"
TARGET = date(2026, 10, 27)
BASE_ID = "alt_hs300"
R253_ID = "alt_hs300_minrank"
R257_ID = "alt_hs300_qrs"
NEW_SYMBOL = "002294.SZ"
BASE_FIELDS = [
    "industry_mom_20d",
    "industry_mom_60d",
    "industry_rps_20d",
    "industry_breadth_20d",
    "stock_rel_industry_20d",
]
R253_FIELD = "management_ocf_gm_min_rank"
R257_FIELD = "quality_relative_strength_min_rank"
SCOPE_AUDIT_METADATA: dict = {}
sys.path.insert(0, str(ROOT / "backend"))


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load production module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_manifest(prereg: dict) -> list[dict]:
    raw = MANIFEST.read_bytes()
    if sha_bytes(raw) != prereg["frozen_inputs"]["manifest_sha256"]:
        raise RuntimeError("R297 frozen input manifest checksum mismatch")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]
    for row in rows:
        path = ROOT / row["path"]
        stat = path.stat()
        if (
            stat.st_size != row["bytes"]
            or stat.st_mtime_ns != row["mtime_ns"]
            or sha_file(path) != row["sha256"]
        ):
            raise RuntimeError(f"R297 frozen input changed: {row['path']}")
    return rows


def verify_formal_baseline() -> tuple[dict, list[dict]]:
    raw = BASELINE.read_bytes()
    expected = BASELINE.with_name(BASELINE.name + ".sha256").read_text(encoding="ascii").split()[0]
    if sha_bytes(raw) != expected:
        raise RuntimeError("R297 formal factor baseline checksum mismatch")
    baseline = json.loads(raw)
    if baseline.get("round") != "R297" or baseline.get("classification") != "pre_compute_formal_factor_tree_baseline":
        raise RuntimeError("R297 formal factor baseline identity mismatch")
    rows = [row for tree in baseline["factor_trees"] for row in tree["partitions"]]
    for row in rows:
        path = ROOT / row["path"]
        stat = path.stat()
        if (
            stat.st_size != row["size_bytes"]
            or stat.st_mtime_ns != row["mtime_ns"]
            or sha_file(path) != row["sha256"]
        ):
            raise RuntimeError(f"R297 formal factor tree changed: {row['path']}")
    return baseline, rows


def main() -> int:
    outputs = (RESULT, RESULT.with_name(RESULT.name + ".sha256"), REPORT, REPORT.with_name(REPORT.name + ".sha256"))
    if any(path.exists() for path in outputs):
        raise RuntimeError("R297 result exists; refusing overwrite")
    pre_bytes = PRE.read_bytes()
    prereg_hash = sha_bytes(pre_bytes)
    if prereg_hash != PRE.with_name(PRE.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R297 preregistration checksum mismatch")
    prereg = json.loads(pre_bytes)
    if prereg.get("round") != "R297" or prereg.get("status") != "frozen_before_compute":
        raise RuntimeError("R297 preregistration is not frozen")
    frozen_inputs = verify_manifest(prereg)
    formal_baseline, formal_rows = verify_formal_baseline()

    base = load_module(ROOT / "scripts/build_alt_factor_data.py", "build_alt_factor_data_r297")
    r253 = load_module(ROOT / "scripts/build_r253_min_rank_factor.py", "build_r253_min_rank_r297")
    r257 = load_module(ROOT / "scripts/build_r257_quality_relative_strength_factor.py", "build_r257_qrs_r297")
    base.DATA = DATA
    dates, symbols = base._calendar()
    if TARGET not in dates or max(dates) != TARGET or NEW_SYMBOL not in symbols:
        raise RuntimeError("Frozen target or builder universe differs from expected inputs")

    industry_day = base._industry_daily(dates, symbols).filter(pl.col("date") == TARGET)
    if industry_day.is_empty() or industry_day["symbol"].n_unique() != industry_day.height:
        raise RuntimeError("Production industry function returned no rows or duplicate keys")
    if set(industry_day["date"].unique().to_list()) != {TARGET}:
        raise RuntimeError("Production industry function returned an unexpected date")
    financial_frames = [
        base._management_expense_ratio_4q_daily([TARGET], symbols),
        base._ocf_margin_4q_daily([TARGET], symbols),
        base._gross_margin_yoy_improvement_q_daily([TARGET], symbols),
    ]
    base_frame = base._calendar_frame([TARGET], symbols)
    for frame in (industry_day, *financial_frames):
        base_frame = base_frame.join(frame, on=["symbol", "date"], how="left")
    required_fields = [*BASE_FIELDS, *r253.COMPONENTS]
    missing_fields = sorted(set(required_fields) - set(base_frame.columns))
    if missing_fields:
        raise RuntimeError(f"Required production fields are missing: {missing_fields}")
    base_frame = base_frame.select(["symbol", "date", *required_fields]).sort(["date", "symbol"])
    if base_frame.height != len(symbols) or base_frame["symbol"].n_unique() != len(symbols):
        raise RuntimeError("Target base grid has missing or duplicate symbols")

    market_path = DATA / "kline_daily_enriched" / f"date={TARGET.isoformat()}" / "part.parquet"
    market = pl.read_parquet(market_path).with_columns(
        pl.col("symbol").cast(pl.String), pl.col("date").cast(pl.Date)
    )
    if set(market["date"].unique().to_list()) != {TARGET}:
        raise RuntimeError("Target enriched partition has an unexpected date")
    market_keys = market.select(["symbol", "date"])
    official = json.loads(OFFICIAL.read_text(encoding="utf-8"))
    official_symbols = {row["symbol"] for row in official["members"]}
    if len(official_symbols) != 300 or not official_symbols.issubset(set(market_keys["symbol"].to_list())):
        raise RuntimeError("Official members are not all present in target-date enriched data")

    r78_path = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
    r157_path = DATA / "research/r157_membership/monthly_snapshots.parquet"
    historical_r78 = pl.read_parquet(r78_path).filter(
        pl.col("query_date").cast(pl.Date) <= TARGET
    )
    historical_r157 = pl.read_parquet(r157_path)
    if historical_r78.is_empty():
        raise RuntimeError("No historical R78 snapshots are available by target date")
    latest_r78_date = historical_r78["query_date"].max()
    latest_r78_snapshot = historical_r78.filter(pl.col("query_date") == latest_r78_date)
    if NEW_SYMBOL in latest_r78_snapshot["symbol"].to_list():
        raise RuntimeError("Expected uncovered symbol is present in the latest production R78 as-of snapshot")
    historical_new_membership = {
        "latest_r78_snapshot_date": latest_r78_date.isoformat(),
        "latest_r78_snapshot_rows": latest_r78_snapshot.height,
        "new_symbol_present_in_latest_r78_snapshot": False,
        "new_symbol_old_r157_snapshot_rows": historical_r157.filter(
            (pl.col("query_date").cast(pl.Date) <= TARGET)
            & (pl.col("symbol") == NEW_SYMBOL)
        ).height,
    }

    new_market = market_keys.filter(pl.col("symbol") == NEW_SYMBOL)
    new_base = base_frame.filter(pl.col("symbol") == NEW_SYMBOL)
    mapping = base.load_industry_l1(DATA).filter(pl.col("symbol") == NEW_SYMBOL)
    if new_market.height != 1 or new_base.height != 1 or mapping.height != 1:
        raise RuntimeError("New member lacks a unique target market row, base row or industry mapping")
    industry_name = mapping["industry_l1"][0]
    if industry_name in r253.EXCLUDED_INDUSTRIES:
        raise RuntimeError("New official member unexpectedly maps to an excluded industry")
    new_inputs = new_base.row(0, named=True)
    missing_new_inputs = [field for field in (*r253.COMPONENTS, "stock_rel_industry_20d") if new_inputs[field] is None]
    if missing_new_inputs:
        raise RuntimeError(f"New member inputs are incomplete: {missing_new_inputs}")

    # The official list is one day newer, so using it on TARGET is a labelled coverage counterfactual only.
    scratch_root = DATA / ".staging"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="r297_counterfactual_membership_", dir=scratch_root) as scratch_name:
        scratch = Path(scratch_name)
        for relative in (
            "research/r157_membership/monthly_snapshots.parquet",
            "ext_data/ext_hy_ths/part.parquet",
        ):
            target = scratch / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DATA / relative, target)
        for config_id in (BASE_ID, R253_ID, R257_ID):
            relative = Path("ext_data") / config_id / "config.json"
            target = scratch / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DATA / relative, target)

        sorted_members = sorted(official["members"], key=lambda item: item["symbol"])
        synthetic = pl.DataFrame(
            {
                "query_date": [TARGET] * len(sorted_members),
                "update_date": [TARGET] * len(sorted_members),
                "symbol": [item["symbol"] for item in sorted_members],
                "name": [item["name"] for item in sorted_members],
            },
            schema_overrides={"query_date": pl.Date, "update_date": pl.Date, "symbol": pl.String, "name": pl.String},
        )
        scratch_r78 = scratch / "research/r78_daily_membership/daily_snapshots.parquet"
        scratch_r78.parent.mkdir(parents=True, exist_ok=True)
        pl.concat([historical_r78, synthetic], how="vertical_relaxed").write_parquet(scratch_r78)

        base.DATA = scratch
        base_partition = scratch / "ext_data" / BASE_ID / "timeseries" / f"date={TARGET.isoformat()}"
        base_partition.mkdir(parents=True, exist_ok=True)
        base_frame.drop("date").write_parquet(base_partition / "part.parquet")
        r253.DATA = scratch
        r253_result = r253._factor_daily(base)
        r253._write_partitions(r253_result)
        from app.services.ext_data import ExtConfigStore

        r253._upsert_config(ExtConfigStore(scratch))
        r257.DATA = scratch
        r257_result = r257._factor_daily(base)
        r257._write_partitions(r257_result)
        r257._upsert_config(ExtConfigStore(scratch))

        from app.factors.ext_factors import attach_ext_columns, ext_column_name

        attached = attach_ext_columns(market_keys, include_snapshot=False, data_dir=scratch)
        r253_col = ext_column_name(R253_ID, R253_FIELD)
        r257_col = ext_column_name(R257_ID, R257_FIELD)
        if r253_col not in attached.columns or r257_col not in attached.columns:
            raise RuntimeError("Production system attachment omitted a registered staged factor")

        r253_expected = r253_result.select(["symbol", R253_FIELD])
        r257_expected = r257_result.select(["symbol", R257_FIELD])
        r253_cmp = r253_expected.join(attached.select(["symbol", r253_col]), on="symbol", how="left")
        r257_cmp = r257_expected.join(attached.select(["symbol", r257_col]), on="symbol", how="left")
        r253_nonnull = r253_cmp.filter(pl.col(R253_FIELD).is_not_null())
        r257_nonnull = r257_cmp.filter(pl.col(R257_FIELD).is_not_null())
        r253_equal = r253_nonnull.filter(pl.col(R253_FIELD) == pl.col(r253_col)).height
        r257_equal = r257_nonnull.filter(pl.col(R257_FIELD) == pl.col(r257_col)).height
        r253_attach_only = r253_cmp.filter(pl.col(R253_FIELD).is_null() & pl.col(r253_col).is_not_null()).height
        r257_attach_only = r257_cmp.filter(pl.col(R257_FIELD).is_null() & pl.col(r257_col).is_not_null()).height
        r253_values = dict(zip(r253_expected["symbol"].to_list(), r253_expected[R253_FIELD].to_list(), strict=True))
        r257_values = dict(zip(r257_expected["symbol"].to_list(), r257_expected[R257_FIELD].to_list(), strict=True))

        eligible = r253._membership_with_industry(base).filter(pl.col("effective_date") <= TARGET)
        effective_date = eligible["effective_date"].max()
        selected_members = set(
            eligible.filter(pl.col("effective_date") == effective_date)["symbol"].to_list()
        )
        eligible_official = official_symbols & selected_members
        if len(eligible_official) != 250 or NEW_SYMBOL not in eligible_official:
            raise RuntimeError("Counterfactual eligible official universe is not the expected 250 symbols")
        attached_new = attached.filter(pl.col("symbol") == NEW_SYMBOL)
        if attached_new.height != 1:
            raise RuntimeError("New member did not produce one exact system attachment row")

        coverage = {
            "builder_universe_size": len(symbols),
            "counterfactual_effective_date": effective_date.isoformat(),
            "official_snapshot_as_of": official.get("as_of"),
            "official_raw_members": len(official_symbols),
            "counterfactual_eligible_official_members": len(eligible_official),
            "historical_member_snapshot_diagnostic": historical_new_membership,
            "new_symbol_target_market_rows": new_market.height,
            "new_symbol_industry": industry_name,
            "new_symbol_inputs": {
                field: new_inputs[field] for field in (*r253.COMPONENTS, "stock_rel_industry_20d")
            },
            "new_symbol_r253_value": r253_values.get(NEW_SYMBOL),
            "new_symbol_r257_value": r257_values.get(NEW_SYMBOL),
            "r253_counterfactual_eligible_coverage": sum(
                r253_values.get(symbol) is not None for symbol in eligible_official
            ),
            "r257_counterfactual_eligible_coverage": sum(
                r257_values.get(symbol) is not None for symbol in eligible_official
            ),
            "r253_builder_nonnull": int(r253_result[R253_FIELD].is_not_null().sum()),
            "r257_builder_nonnull": int(r257_result[R257_FIELD].is_not_null().sum()),
            "r253_system_equal": r253_equal,
            "r253_system_compared": r253_nonnull.height,
            "r253_attachment_only": r253_attach_only,
            "r257_system_equal": r257_equal,
            "r257_system_compared": r257_nonnull.height,
            "r257_attachment_only": r257_attach_only,
            "new_symbol_r253_system_value": attached_new[r253_col][0],
            "new_symbol_r257_system_value": attached_new[r257_col][0],
        }
        criteria = {
            "I2_new_member_market_and_industry_inputs_present": (
                new_market.height == 1 and industry_name not in r253.EXCLUDED_INDUSTRIES
            ),
            "I2_financial_and_relative_strength_inputs_nonnull": not missing_new_inputs,
            "I3_r253_full_counterfactual_eligible_coverage": (
                coverage["r253_counterfactual_eligible_coverage"] == len(eligible_official)
            ),
            "I3_r257_full_counterfactual_eligible_coverage": (
                coverage["r257_counterfactual_eligible_coverage"] == len(eligible_official)
            ),
            "I4_r253_all_nonnull_values_attach_exactly": (
                r253_equal == r253_nonnull.height and r253_attach_only == 0
            ),
            "I4_r257_all_nonnull_values_attach_exactly": (
                r257_equal == r257_nonnull.height and r257_attach_only == 0
            ),
            "I4_new_symbol_attached_exactly": (
                coverage["new_symbol_r253_value"] == coverage["new_symbol_r253_system_value"]
                and coverage["new_symbol_r257_value"] == coverage["new_symbol_r257_system_value"]
            ),
        }
        status = (
            "counterfactual_membership_restores_full_eligible_coverage_not_PIT"
            if all(criteria.values())
            else "counterfactual_coverage_or_attachment_failed"
        )
        machine = {
            "round": "R297",
            "classification": "isolated_counterfactual_membership_factor_build_and_system_attachment",
            "captured_at_utc": datetime.now(UTC).isoformat(),
            "status": status,
            "target_date": TARGET.isoformat(),
            "counterfactual_not_pit": True,
            "preregistration_sha256": prereg_hash,
            "input_manifest_sha256": prereg["frozen_inputs"]["manifest_sha256"],
            "formal_factor_baseline_sha256": sha_file(BASELINE),
            "coverage": coverage,
            "criteria": criteria,
            "execution_limits": {
                "isolated_staging_root": "unique temporary directory under data/.staging; automatically removed",
                "production_builder_main_called": False,
                "provider_or_network_data_pull_called": False,
                "backtest_screener_strategy_runner_matcher_called": False,
                "returns_or_trade_logs_read": False,
                "formal_market_membership_factor_or_config_writes": False,
                "formal_factor_partitions_before": len(formal_rows),
                "frozen_inputs_before": len(frozen_inputs),
                "scratch_removed": True,
            },
        }

    after_inputs = verify_manifest(prereg)
    after_baseline, after_formal_rows = verify_formal_baseline()
    if after_inputs != frozen_inputs or after_baseline != formal_baseline or after_formal_rows != formal_rows:
        raise RuntimeError("R297 frozen sources or formal factor partitions changed during computation")
    machine["execution_limits"].update(
        {
            "frozen_inputs_after": len(after_inputs),
            "formal_factor_partitions_after": len(after_formal_rows),
            "formal_factor_trees_unchanged": True,
        }
    )
    machine["scope_correction"] = dict(SCOPE_AUDIT_METADATA)
    machine_hash = write_once(
        RESULT,
        (json.dumps(machine, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    report = f"""# R297 官方当前名单覆盖敏感性核验（反事实，非PIT）

## 问题与边界

R296 在 {TARGET.isoformat()} 发现唯一未覆盖成分为 {NEW_SYMBOL}。本轮只区分输入缺失与历史成员快照缺失：沿用生产行业、三项财务、R253/R257 排名和真实系统 attach_ext_columns(include_snapshot=False)，只在 data/.staging 临时目录把中证官方于 {official.get('as_of')} 返回的 300 只名单追加为目标日合成成员快照。官方名单观测晚于目标日，且本轮没有核验调样公告的生效日；因此本轮反事实绝不是目标日历史成分、PIT 信号或生产名单。

## 结果

- 生产 as-of R78 快照日期为 {historical_new_membership['latest_r78_snapshot_date']}、共 {historical_new_membership['latest_r78_snapshot_rows']} 行，不含 {NEW_SYMBOL}；较早 R157 快照中该代码出现 {historical_new_membership['new_symbol_old_r157_snapshot_rows']} 行。它不改变本次关于最新 R78 快照缺口的判断。目标日行情、{industry_name} 行业映射、R253 三项财务输入及行业相对强度均有值。
- 反事实官方有效名单共 {len(eligible_official)} 只；R253 覆盖 {coverage['r253_counterfactual_eligible_coverage']}/{len(eligible_official)}，R257 覆盖 {coverage['r257_counterfactual_eligible_coverage']}/{len(eligible_official)}。
- 新成员隔离计算 R253={coverage['new_symbol_r253_value']}、R257={coverage['new_symbol_r257_value']}；系统装配得到相同值。
- 系统装配逐值一致：R253 {r253_equal}/{r253_nonnull.height}，R257 {r257_equal}/{r257_nonnull.height}；无装配侧额外非空值。
- 判定：{status}。

## 保护与限制

- 冻结输入 {len(frozen_inputs)} 个文件与正式因子 {len(formal_rows)} 个分区执行前后 SHA-256、大小和修改时间一致。
- 运行时 TTL 缓存的原始字节哈希按事前范围更正豁免；其成员语义与独立冻结官方名单一致，其他冻结输入和正式因子分区均执行前后校验。
- 全部重建产物只写入临时目录，结束后自动清理；未改正式成员、行情、扩展因子或缓存。
- 未调用 provider、builder main、筛选、策略执行、回测、撮合或下单；未读收益和成交。
- 预注册 SHA-256：{prereg_hash}；输入清单 SHA-256：{prereg['frozen_inputs']['manifest_sha256']}；正式因子基线 SHA-256：{machine['formal_factor_baseline_sha256']}；机器结果 SHA-256：{machine_hash}。
- 面板候选由独立步骤单次登记并 GET 读回，状态 pending；这只是研究留档，不代表策略获批。

## 结论

证据支持“生产公式和系统装配可以计算该新增成员；R296 的单日缺口来自历史成员快照不含此代码”。但下一日官方名单不能证明该股票在目标日已生效，故不能把反事实扩展成历史信号或 PIT 结论，也不能据此改门禁或更新生产数据。取得目标日有效成分公告和 PIT 证据后，才能讨论真实当前信号。R257 继续 research-only，生产维持 pause_production。
"""
    report_hash = write_once(REPORT, report.encode("utf-8"))
    print(
        json.dumps(
            {
                "round": "R297",
                "status": status,
                "new_symbol": NEW_SYMBOL,
                "counterfactual_eligible_members": len(eligible_official),
                "r253_coverage": coverage["r253_counterfactual_eligible_coverage"],
                "r257_coverage": coverage["r257_counterfactual_eligible_coverage"],
                "new_symbol_r253": coverage["new_symbol_r253_value"],
                "new_symbol_r257": coverage["new_symbol_r257_value"],
                "machine_result_sha256": machine_hash,
                "report_sha256": report_hash,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

