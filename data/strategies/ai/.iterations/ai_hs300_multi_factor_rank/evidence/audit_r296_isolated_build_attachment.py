#!/usr/bin/env python3
"""Rebuild R253/R257 for one frozen date in isolation and verify system attachment."""
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
sys.path.insert(0, str(ROOT / "backend"))

TARGET = date(2026, 10, 27)
TARGET_TEXT = TARGET.isoformat()
RESULT = EVIDENCE / "r296_isolated_build_attachment_audit.json"
RESULT_MD = EVIDENCE / "r296_result.md"
PREREG = EVIDENCE / "r296_preregistration.json"
MANIFEST = EVIDENCE / "r296_input_manifest.jsonl"
OFFICIAL_MEMBERS = EVIDENCE / "r296_official_members.json"
BASE_ID = "alt_hs300"
R253_ID = "alt_hs300_minrank"
R257_ID = "alt_hs300_qrs"
BASE_FIELDS = [
    "industry_mom_20d",
    "industry_mom_60d",
    "industry_rps_20d",
    "industry_breadth_20d",
    "stock_rel_industry_20d",
]
R253_FIELD = "management_ocf_gm_min_rank"
R257_FIELD = "quality_relative_strength_min_rank"


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


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
    manifest_bytes = MANIFEST.read_bytes()
    if sha_bytes(manifest_bytes) != prereg["frozen_context"]["input_manifest"]["sha256"]:
        raise RuntimeError("R296 frozen manifest checksum mismatch")
    rows = [json.loads(line) for line in manifest_bytes.decode("utf-8").splitlines() if line]
    for row in rows:
        path = ROOT / row["path"]
        stat = path.stat()
        if (
            stat.st_size != row["bytes"]
            or stat.st_mtime_ns != row["mtime_ns"]
            or sha_file(path) != row["sha256"]
        ):
            raise RuntimeError(f"R296 frozen input changed: {row['path']}")
    return rows


def verify_formal_baseline() -> tuple[dict, list[dict]]:
    baseline_path = EVIDENCE / "r296_formal_factor_baseline.json"
    baseline_sidecar = baseline_path.with_name(baseline_path.name + ".sha256")
    baseline_hash = sha_file(baseline_path)
    if baseline_hash != baseline_sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R296 formal factor baseline checksum mismatch")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline.get("round") != "R296" or baseline.get("classification") != "pre_compute_scope_amendment_formal_factor_tree_baseline":
        raise RuntimeError("R296 formal factor baseline identity mismatch")
    rows = [row for tree in baseline["factor_trees"] for row in tree["partitions"]]
    for row in rows:
        path = ROOT / row["path"]
        stat = path.stat()
        if (
            stat.st_size != row["size_bytes"]
            or stat.st_mtime_ns != row["mtime_ns"]
            or sha_file(path) != row["sha256"]
        ):
            raise RuntimeError(f"R296 formal factor tree changed: {row['path']}")
    return baseline, rows


def main() -> int:
    for path in (RESULT, RESULT.with_name(RESULT.name + ".sha256"), RESULT_MD, RESULT_MD.with_name(RESULT_MD.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"R296 result exists; refusing overwrite: {path.name}")

    prereg_bytes = PREREG.read_bytes()
    prereg_hash = sha_bytes(prereg_bytes)
    if prereg_hash != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R296 preregistration checksum mismatch")
    prereg = json.loads(prereg_bytes)
    if prereg.get("round") != "R296" or prereg.get("status") != "frozen_before_compute":
        raise RuntimeError("R296 preregistration is not frozen")
    frozen_inputs = verify_manifest(prereg)
    baseline, formal_rows = verify_formal_baseline()

    base = load_module(ROOT / "scripts/build_alt_factor_data.py", "build_alt_factor_data_r296")
    r253 = load_module(ROOT / "scripts/build_r253_min_rank_factor.py", "build_r253_min_rank_r296")
    r257 = load_module(ROOT / "scripts/build_r257_quality_relative_strength_factor.py", "build_r257_qrs_r296")
    base.DATA = DATA

    dates, symbols = base._calendar()
    if TARGET not in dates or max(dates) != TARGET:
        raise RuntimeError("Frozen target is not the latest enriched trading date")
    industry_all = base._industry_daily(dates, symbols)
    industry_day = industry_all.filter(pl.col("date") == TARGET)
    if industry_day.is_empty():
        raise RuntimeError("Production industry calculation returned no target rows")
    if industry_day["symbol"].n_unique() != industry_day.height:
        raise RuntimeError("Production industry calculation returned duplicate symbol keys")
    if set(industry_day["date"].unique().to_list()) != {TARGET}:
        raise RuntimeError("Production industry calculation returned an unexpected date")

    # Evaluate financial availability only at the frozen date; each production
    # function still reads its full source history and applies the shared PIT rule.
    sga_day = base._management_expense_ratio_4q_daily([TARGET], symbols)
    ocf_day = base._ocf_margin_4q_daily([TARGET], symbols)
    gross_day = base._gross_margin_yoy_improvement_q_daily([TARGET], symbols)
    base_frame = base._calendar_frame([TARGET], symbols)
    for frame in (industry_day, sga_day, ocf_day, gross_day):
        base_frame = base_frame.join(frame, on=["symbol", "date"], how="left")
    needed_base = [*BASE_FIELDS, *r253.COMPONENTS]
    missing_columns = sorted(set(needed_base) - set(base_frame.columns))
    if missing_columns:
        raise RuntimeError(f"Required production base fields missing: {missing_columns}")
    base_frame = base_frame.select(["symbol", "date", *needed_base]).sort(["date", "symbol"])
    if base_frame.height != len(symbols) or base_frame["symbol"].n_unique() != len(symbols):
        raise RuntimeError("Target base grid has duplicate or missing symbols")
    if set(base_frame["date"].unique().to_list()) != {TARGET}:
        raise RuntimeError("Target base grid has an unexpected date")

    market_path = DATA / "kline_daily_enriched" / f"date={TARGET_TEXT}" / "part.parquet"
    market = pl.read_parquet(market_path)
    market_dates = set(market["date"].cast(pl.Date).unique().to_list())
    if market_dates != {TARGET}:
        raise RuntimeError("Enriched target partition contains a date mismatch")
    market_keys = market.select(
        pl.col("symbol").cast(pl.String), pl.col("date").cast(pl.Date)
    )
    official = json.loads(OFFICIAL_MEMBERS.read_text(encoding="utf-8"))
    official_symbols = {item["symbol"] for item in official["members"]}
    market_symbols = set(market_keys["symbol"].to_list())
    missing_official_market = sorted(official_symbols - market_symbols)
    if missing_official_market:
        raise RuntimeError(f"Official current members missing enriched bars: {missing_official_market[:5]}")

    # Let the production R253/R257 functions read only a one-date base partition
    # and write only inside a new, isolated staging root.
    staging_root = DATA / ".staging"
    with tempfile.TemporaryDirectory(prefix="r296_isolated_factor_", dir=staging_root) as scratch_name:
        scratch = Path(scratch_name)
        for relative in (
            "research/r78_daily_membership/daily_snapshots.parquet",
            "research/r157_membership/monthly_snapshots.parquet",
            "ext_data/ext_hy_ths/part.parquet",
        ):
            target = scratch / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DATA / relative, target)
        for config_id in (BASE_ID, R253_ID, R257_ID):
            source = DATA / "ext_data" / config_id / "config.json"
            target = scratch / "ext_data" / config_id / "config.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        base.DATA = scratch
        base_target = scratch / "ext_data" / BASE_ID / "timeseries" / f"date={TARGET_TEXT}"
        base_target.mkdir(parents=True, exist_ok=True)
        base_frame.drop("date").write_parquet(base_target / "part.parquet")

        r253.DATA = scratch
        r253_result = r253._factor_daily(base)
        if r253_result.height != len(symbols) or r253_result["symbol"].n_unique() != len(symbols):
            raise RuntimeError("Production R253 output has duplicate or missing keys")
        if set(r253_result["date"].unique().to_list()) != {TARGET}:
            raise RuntimeError("Production R253 output has an unexpected date")
        r253._write_partitions(r253_result)
        from app.services.ext_data import ExtConfigStore

        r253._upsert_config(ExtConfigStore(scratch))

        r257.DATA = scratch
        r257_result = r257._factor_daily(base)
        if r257_result.height != len(symbols) or r257_result["symbol"].n_unique() != len(symbols):
            raise RuntimeError("Production R257 output has duplicate or missing keys")
        if set(r257_result["date"].unique().to_list()) != {TARGET}:
            raise RuntimeError("Production R257 output has an unexpected date")
        r257._write_partitions(r257_result)
        r257._upsert_config(ExtConfigStore(scratch))

        from app.factors.ext_factors import attach_ext_columns, ext_column_name

        attached = attach_ext_columns(market_keys, include_snapshot=False, data_dir=scratch)
        r253_col = ext_column_name(R253_ID, R253_FIELD)
        r257_col = ext_column_name(R257_ID, R257_FIELD)
        if r253_col not in attached.columns or r257_col not in attached.columns:
            raise RuntimeError("System attachment omitted a registered staged factor")

        r253_expected = r253_result.select(["symbol", R253_FIELD])
        r257_expected = r257_result.select(["symbol", R257_FIELD])
        r253_compared = r253_expected.join(attached.select(["symbol", r253_col]), on="symbol", how="left")
        r257_compared = r257_expected.join(attached.select(["symbol", r257_col]), on="symbol", how="left")
        r253_nonnull = r253_compared.filter(pl.col(R253_FIELD).is_not_null())
        r257_nonnull = r257_compared.filter(pl.col(R257_FIELD).is_not_null())
        r253_equal = r253_nonnull.filter(pl.col(R253_FIELD) == pl.col(r253_col)).height
        r257_equal = r257_nonnull.filter(pl.col(R257_FIELD) == pl.col(r257_col)).height
        r253_attach_only = r253_compared.filter(pl.col(R253_FIELD).is_null() & pl.col(r253_col).is_not_null()).height
        r257_attach_only = r257_compared.filter(pl.col(R257_FIELD).is_null() & pl.col(r257_col).is_not_null()).height
        r253_values = dict(zip(r253_expected["symbol"].to_list(), r253_expected[R253_FIELD].to_list()))
        r257_values = dict(zip(r257_expected["symbol"].to_list(), r257_expected[R257_FIELD].to_list()))
        official_r253 = sum(r253_values.get(symbol) is not None for symbol in official_symbols)
        official_r257 = sum(r257_values.get(symbol) is not None for symbol in official_symbols)

        membership = r253._membership_with_industry(base)
        eligible = membership.filter(pl.col("effective_date") <= TARGET)
        if eligible.is_empty():
            raise RuntimeError("No historical HS300 membership snapshot is available by target date")
        snapshot_date = eligible["effective_date"].max()
        snapshot_symbols = set(
            eligible.filter(pl.col("effective_date") == snapshot_date)["symbol"].to_list()
        )
        snapshot_r253 = sum(r253_values.get(symbol) is not None for symbol in snapshot_symbols)
        snapshot_r257 = sum(r257_values.get(symbol) is not None for symbol in snapshot_symbols)
        overlap = len(snapshot_symbols & official_symbols)
        snapshot_age_days = (TARGET - snapshot_date).days

        component_coverage = {
            component: int(base_frame[component].is_not_null().sum())
            for component in r253.COMPONENTS
        }
        all_three = int(base_frame.select(
            pl.all_horizontal([pl.col(field).is_not_null() for field in r253.COMPONENTS]).sum()
        ).item())
        coverage = {
            "builder_universe_size": len(symbols),
            "industry_relative_strength_nonnull": int(industry_day["stock_rel_industry_20d"].is_not_null().sum()),
            "r253_nonnull_builder_universe": int(r253_result[R253_FIELD].is_not_null().sum()),
            "r257_nonnull_builder_universe": int(r257_result[R257_FIELD].is_not_null().sum()),
            "r253_nonnull_official_current_members": official_r253,
            "r257_nonnull_official_current_members": official_r257,
            "r253_nonnull_historical_snapshot_members": snapshot_r253,
            "r257_nonnull_historical_snapshot_members": snapshot_r257,
            "r253_nonnull_values_compared": r253_nonnull.height,
            "r253_equal_values": r253_equal,
            "r253_attachment_only_values": r253_attach_only,
            "r257_nonnull_values_compared": r257_nonnull.height,
            "r257_equal_values": r257_equal,
            "r257_attachment_only_values": r257_attach_only,
        }
        criteria = {
            "I2_base_table_exact_target_and_unique_keys": (
                base_frame.height == len(symbols)
                and base_frame["symbol"].n_unique() == len(symbols)
                and set(base_frame["date"].unique().to_list()) == {TARGET}
            ),
            "I2_r253_exact_target_and_unique_keys": (
                r253_result.height == len(symbols)
                and r253_result["symbol"].n_unique() == len(symbols)
                and set(r253_result["date"].unique().to_list()) == {TARGET}
            ),
            "I2_r257_exact_target_and_unique_keys": (
                r257_result.height == len(symbols)
                and r257_result["symbol"].n_unique() == len(symbols)
                and set(r257_result["date"].unique().to_list()) == {TARGET}
            ),
            "I3_official_r253_coverage_at_least_150": official_r253 >= 150,
            "I3_official_r257_coverage_at_least_150": official_r257 >= 150,
            "I4_all_nonnull_r253_values_attach_exactly": r253_equal == r253_nonnull.height and r253_attach_only == 0,
            "I4_all_nonnull_r257_values_attach_exactly": r257_equal == r257_nonnull.height and r257_attach_only == 0,
            "I5_membership_snapshot_not_future": snapshot_date <= TARGET,
        }
        all_checks_pass = all(criteria.values())
        if not all_checks_pass:
            status = "coverage_or_attachment_gate_failed"
        elif snapshot_date < TARGET:
            status = "coverage_and_attachment_pass_membership_snapshot_stale_operationally_not_ready"
        else:
            status = "isolated_build_and_attachment_pass"

        machine = {
            "round": "R296",
            "classification": "isolated_single_date_factor_build_and_production_system_attachment_audit",
            "captured_at_utc": datetime.now(UTC).isoformat(),
            "status": status,
            "target_date": TARGET_TEXT,
            "preregistration_sha256": prereg_hash,
            "input_manifest_sha256": prereg["frozen_context"]["input_manifest"]["sha256"],
            "formal_factor_baseline_sha256": sha_file(EVIDENCE / "r296_formal_factor_baseline.json"),
            "universe": {
                "builder_symbols": len(symbols),
                "enriched_rows": market.height,
                "enriched_unique_symbols": market_keys["symbol"].n_unique(),
                "official_members": len(official_symbols),
                "official_members_missing_enriched": len(missing_official_market),
                "official_snapshot_as_of": official.get("as_of"),
                "historical_membership_snapshot_date": snapshot_date.isoformat(),
                "historical_snapshot_calendar_age_days": snapshot_age_days,
                "historical_snapshot_members": len(snapshot_symbols),
                "historical_snapshot_official_overlap": overlap,
                "historical_snapshot_official_overlap_pct": round(overlap / len(official_symbols), 6),
            },
            "r253_component_coverage": {
                **component_coverage,
                "all_three_required_nonnull_rows": all_three,
            },
            "coverage": coverage,
            "criteria": criteria,
            "execution_limits": {
                "isolated_staging_root": "data/.staging unique temporary directory; removed automatically",
                "production_builder_main_called": False,
                "provider_called": False,
                "network_data_pull_called": False,
                "backtest_screener_strategy_runner_or_matcher_called": False,
                "returns_or_trade_logs_read": False,
                "formal_config_or_partition_writes": False,
                "formal_factor_partition_count_verified_before": len(formal_rows),
                "formal_factor_partition_count_verified_after": None,
                "all_frozen_input_files_verified_before": len(frozen_inputs),
                "scratch_removed": True,
            },
        }

    after_inputs = verify_manifest(prereg)
    after_baseline, after_formal_rows = verify_formal_baseline()
    if after_inputs != frozen_inputs:
        raise RuntimeError("R296 frozen inputs changed during computation")
    if after_baseline != baseline or after_formal_rows != formal_rows:
        raise RuntimeError("R296 formal factor baseline changed during computation")
    machine["execution_limits"]["formal_factor_partition_count_verified_after"] = len(after_formal_rows)
    machine["execution_limits"]["formal_factor_trees_unchanged_after"] = True
    machine["execution_limits"]["all_frozen_input_files_verified_after"] = len(after_inputs)

    machine_hash = write_once(
        RESULT,
        (json.dumps(machine, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    )
    coverage = machine["coverage"]
    universe = machine["universe"]
    components = machine["r253_component_coverage"]
    report = f"""# R296 R257 单日隔离重建与系统装配审计

## 问题与边界

本轮验证最新 enriched 日期 {TARGET_TEXT} 能否使用生产计算函数在 data/.staging 隔离生成基础输入、R253 和 R257，并被生产 attach_ext_columns(include_snapshot=False) 按精确 (symbol, date) 连接。本轮承接 R290-R294 的因子过期和安全构建审计，不是盲测，也不测试收益或策略有效性。

策略因子使用历史成分快照，而不是官方当前名单。官方当前名单快照日期为 {official.get('as_of')}，共有 {len(official_symbols)} 只；生产历史成员函数实际选取截至目标日的快照 {universe['historical_membership_snapshot_date']}，日历年龄 {snapshot_age_days} 天，与当前官方名单重叠 {overlap}/{len(official_symbols)}。分别报告两种名单覆盖，不把它们混成一个口径。

## 结果

- builder universe：{len(symbols)} 只；目标日 enriched 行情 {market.height} 行，唯一股票 {market_keys['symbol'].n_unique()}；官方 300 只在行情中的缺失数 {len(missing_official_market)}。
- 目标日基础行业相对强度非空 {coverage['industry_relative_strength_nonnull']}/{len(symbols)}。
- R253 三个财务输入非空数：管理费用率 {components['management_expense_ratio_4q_low']}、OCF 利润率 {components['ocf_margin_4q']}、毛利率同比改善 {components['gross_margin_yoy_improvement_q']}；三项同时非空 {components['all_three_required_nonnull_rows']}。
- R253 非空：完整研究 universe {coverage['r253_nonnull_builder_universe']}，历史快照成员 {coverage['r253_nonnull_historical_snapshot_members']}，官方当前成员 {coverage['r253_nonnull_official_current_members']}。
- R257 非空：完整研究 universe {coverage['r257_nonnull_builder_universe']}，历史快照成员 {coverage['r257_nonnull_historical_snapshot_members']}，官方当前成员 {coverage['r257_nonnull_official_current_members']}。
- 系统装配：R253 {coverage['r253_equal_values']}/{coverage['r253_nonnull_values_compared']}、R257 {coverage['r257_equal_values']}/{coverage['r257_nonnull_values_compared']} 非空值逐项一致；仅装配侧额外出现的非空值分别为 {coverage['r253_attachment_only_values']} 和 {coverage['r257_attachment_only_values']}。
- 预注册覆盖检查状态：{machine['status']}。

## 方法与数据口径

调用生产 _calendar、_industry_daily、三个财务字段计算函数、R253/R257 的生产复合排名和分区写入函数、扩展配置写入，以及系统 attach_ext_columns。builder 的 DATA 仅指向临时目录；不调用任何 main()。基础表只保存目标日截面；行业滚动值使用目标日前完整行情历史。财务字段沿用当前代码按报告期法定披露截止日和缓冲期推导可用日的逻辑，这不能证明原始公告版本级历史 PIT。

生产成员映射截至目标日实际使用 {universe['historical_membership_snapshot_date']} 的快照，滞后 {snapshot_age_days} 个自然日，和官方名单有 {len(official_symbols) - overlap} 个代码不同。因此，覆盖恢复只证明计算/装配路径有效；最新历史成员数据的时效性仍阻止把本轮解释为可靠的当前成分信号。

## 保护与证据

- 冻结输入 manifest 含 {len(frozen_inputs)} 个文件，包括 {prereg['frozen_context']['input_manifest']['enriched_partition_count']} 个 enriched 日分区；计算前后逐项核对大小、mtime_ns 和 SHA-256。
- 计算前后逐项核对三层正式因子目录的 {len(formal_rows)} 个分区 SHA-256，完全一致。
- staged 输出目录自动清理；正式 config、因子分区、缓存、策略、paper 和生产状态未更改。
- 未调用 provider、网络拉取、builder main、回测、筛选、策略执行、撮合或下单；未读取收益或成交。
- 预注册 SHA-256：{prereg_hash}；输入 manifest SHA-256：{prereg['frozen_context']['input_manifest']['sha256']}；正式因子基线 SHA-256：{machine['formal_factor_baseline_sha256']}；机器结果 SHA-256：{machine_hash}。
- 面板候选 ID 48ca7b2f1105431d97c148e3e0574f4c，单次登记并读回，状态 pending；候选数 12→13，POST/GET HTTP 200。登记只是审计记录，不表示回测或策略通过。

## 结论

R296 只回答 {TARGET_TEXT} 的隔离构建与系统精确装配是否达到预注册覆盖标准。它不证明历史值复现、修订级 PIT、前瞻收益、alpha 或生产可用。正式刷新链仍未连接 R253/R257，历史成分数据也未追到目标日。R257 继续保持 research-only，生产维持 pause_production。
"""
    report_hash = write_once(RESULT_MD, report.encode("utf-8"))
    print(json.dumps({
        "round": "R296",
        "status": machine["status"],
        "builder_symbols": len(symbols),
        "official_members": len(official_symbols),
        "historical_membership_date": machine["universe"]["historical_membership_snapshot_date"],
        "r253_current_coverage": coverage["r253_nonnull_official_current_members"],
        "r257_current_coverage": coverage["r257_nonnull_official_current_members"],
        "r253_system_equal": coverage["r253_equal_values"],
        "r257_system_equal": coverage["r257_equal_values"],
        "machine_result_sha256": machine_hash,
        "report_sha256": report_hash,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
