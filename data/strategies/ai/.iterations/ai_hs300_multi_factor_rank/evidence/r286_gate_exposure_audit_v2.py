"""Audit frozen macro-gate activity without loading equity-return data."""
from __future__ import annotations

import hashlib
import itertools
import json
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl

EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[5]
DATA = ROOT / "data"
PRE = EVIDENCE / "r286_preregistration.json"
PANEL = EVIDENCE / "r286_panel_registration.json"
INPUT_SNAPSHOT = EVIDENCE / "r286_input_snapshot.json"
AUDIT = EVIDENCE / "r286_gate_exposure_audit_v2.json"
RESULT = EVIDENCE / "r286_result_v2.md"
PRIOR_AUDIT = EVIDENCE / "r286_gate_exposure_audit.json"
START = date(2021, 1, 4)
END = date(2026, 10, 14)
GATES = {
    "equity_risk_premium": ("equity_risk_premium", 5.5),
    "credit_spread_5y_low": ("credit_spread_5y_low", -0.8),
    "term_spread_10y_2y": ("term_spread_10y_2y", 0.3),
}
HOLDER = "holder_top10_free_float_share"


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def sha_sidecar(path: Path) -> str:
    sidecar = path.with_name(path.name + ".sha256")
    if not path.is_file() or not sidecar.is_file():
        raise RuntimeError(f"missing artifact or sidecar: {path}")
    actual = sha_file(path)
    expected = sidecar.read_text(encoding="ascii").split()[0]
    if actual != expected:
        raise RuntimeError(f"artifact hash mismatch: {path}")
    return actual


def save_once(path: Path, payload: bytes) -> str:
    if path.exists() or path.with_name(path.name + ".sha256").exists():
        raise RuntimeError(f"refusing to overwrite R286 artifact: {path.name}")
    with path.open("xb") as stream:
        stream.write(payload)
    digest = sha_file(path)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def partition_files(directory: Path, dates: set[date]) -> list[Path]:
    result = []
    for day in sorted(dates):
        path = directory / f"date={day.isoformat()}" / "part.parquet"
        if path.is_file():
            result.append(path)
    return result


def partition_day(path: Path) -> date:
    return date.fromisoformat(path.parent.name.removeprefix("date="))


def digest_inventory(paths: list[Path]) -> dict:
    items = []
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        item_hash = sha_file(path)
        items.append({"path": relative, "size_bytes": path.stat().st_size, "sha256": item_hash})
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item_hash.encode("ascii"))
        digest.update(b"\n")
    return {"partition_count": len(items), "total_size_bytes": sum(item["size_bytes"] for item in items),
            "inventory_sha256": digest.hexdigest(), "partitions": items}


def bool_counts(frame: pl.DataFrame, columns: tuple[str, str, str]) -> dict[str, int]:
    rows = frame.select(columns).iter_rows()
    counts = {"".join(bits): 0 for bits in itertools.product("01", repeat=3)}
    for values in rows:
        key = "".join("1" if value else "0" for value in values)
        counts[key] += 1
    return counts


def longest_failed_run(date_rows: list[dict], gate_column: str) -> int:
    longest = current = 0
    for row in date_rows:
        value = row.get(gate_column)
        if value is None:
            current = 0  # A missing observation breaks the run; it is not treated as a failure.
        elif not value:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def main() -> int:
    for path in (AUDIT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R286 result artifacts already exist: {path.name}")

    prereg_hash = sha_sidecar(PRE)
    prereg = json.loads(PRE.read_text(encoding="utf-8"))
    if prereg_hash != "3c94e091ebdd6c5806cacf1498ca253fd219989e9d743a5fefdd5c61cb861a78":
        raise RuntimeError("R286 preregistration differs from the frozen version")
    panel_hash = sha_sidecar(PANEL)
    panel = json.loads(PANEL.read_text(encoding="utf-8"))
    if panel.get("registration_pass") is not True or panel.get("candidate", {}).get("status") != "pending":
        raise RuntimeError("R286 system-panel registration/readback did not pass")
    if panel.get("candidate", {}).get("source_id") != prereg["panel_candidate"]["source_id"]:
        raise RuntimeError("R286 panel candidate does not match frozen identity")
    prior_audit_hash = sha_sidecar(PRIOR_AUDIT)

    frozen_paths = {key: ROOT / value["path"] for key, value in prereg["source_hashes"].items()}
    frozen_hashes = {key: sha_file(path) for key, path in frozen_paths.items()}
    for key, expected in prereg["source_hashes"].items():
        if frozen_hashes[key] != expected["sha256"]:
            raise RuntimeError(f"R286 frozen input/source hash changed: {key}")

    membership_path = frozen_paths["historical_membership"]
    membership = (
        pl.read_parquet(membership_path)
        .select(pl.col("query_date").cast(pl.Date).alias("date"), pl.col("symbol").cast(pl.String))
        .filter(pl.col("date").is_between(START, END, closed="both"))
        .unique(subset=["date", "symbol"])
        .sort(["date", "symbol"])
    )
    if membership.is_empty():
        raise RuntimeError("historical membership has no rows in the frozen window")
    member_dates = set(membership.get_column("date").unique().to_list())
    alt_dir = DATA / "ext_data/alt_hs300/timeseries"
    enriched_dir = DATA / "kline_daily_enriched"
    alt_paths = partition_files(alt_dir, member_dates)
    enriched_paths = partition_files(enriched_dir, member_dates)
    alt_dates = {partition_day(path) for path in alt_paths}
    enriched_dates = {partition_day(path) for path in enriched_paths}
    used_dates = member_dates & alt_dates & enriched_dates
    if not used_dates:
        raise RuntimeError("no aligned membership, ext-data and enriched dates")
    alt_paths = [path for path in alt_paths if partition_day(path) in used_dates]
    enriched_paths = [path for path in enriched_paths if partition_day(path) in used_dates]

    # Freeze the exact read set before loading any gate or feature values.
    input_snapshot = {
        "round": "R286", "classification": "pre_calculation_read_set_hash_snapshot",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "preregistration_sha256": prereg_hash, "panel_registration_sha256": panel_hash,
        "membership_date_count": len(member_dates), "aligned_date_count": len(used_dates),
        "frozen_file_hashes": frozen_hashes,
        "alt_hs300_relevant_partitions": digest_inventory(alt_paths),
        "enriched_relevant_partitions": digest_inventory(enriched_paths),
    }
    if INPUT_SNAPSHOT.exists():
        input_snapshot_hash = sha_sidecar(INPUT_SNAPSHOT)
        original_snapshot = json.loads(INPUT_SNAPSHOT.read_text(encoding="utf-8"))
        invariant_keys = (
            "round", "classification", "preregistration_sha256",
            "panel_registration_sha256", "membership_date_count",
            "aligned_date_count", "frozen_file_hashes",
            "alt_hs300_relevant_partitions", "enriched_relevant_partitions",
        )
        if any(original_snapshot.get(key) != input_snapshot.get(key) for key in invariant_keys):
            raise RuntimeError("existing R286 input snapshot does not match current source inventory")
    else:
        input_snapshot_hash = save_once(INPUT_SNAPSHOT, (json.dumps(input_snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    alt_schema = pl.scan_parquet([str(path) for path in alt_paths], hive_partitioning=True).collect_schema()
    enriched_schema = pl.scan_parquet([str(path) for path in enriched_paths], hive_partitioning=True).collect_schema()
    required_alt = {HOLDER, *(field for field, _ in GATES.values())}
    if not required_alt.issubset(alt_schema.names()):
        raise RuntimeError("required R286 fields are missing from alt_hs300 schema")
    # MA60 is an indicator computed by the system feature pipeline, not stored in enriched parquet.
    price_columns = {"symbol", "date", "close"}
    if not price_columns.issubset(enriched_schema.names()):
        raise RuntimeError("required close history is missing from enriched schema")

    alt = (
        pl.scan_parquet([str(path) for path in alt_paths], hive_partitioning=True)
        .select(["symbol", "date", HOLDER, *(field for field, _ in GATES.values())])
        .with_columns(pl.lit(1, dtype=pl.UInt8).alias("_ext_present"))
        .collect()
    )
    price_history = (
        pl.scan_parquet([str(path) for path in enriched_paths], hive_partitioning=True)
        .select(["symbol", "date", "close"])
        .with_columns(pl.lit(1, dtype=pl.UInt8).alias("_enriched_present"))
        .collect()
    )
    enriched_keys = price_history.select(["date", "symbol"]).unique()
    requested_symbols = membership.get_column("symbol").unique().to_list()
    # Load only the preceding 59 bars needed to compute MA60 for the frozen window.
    warmup_paths = sorted(enriched_dir.glob("date=*/part.parquet"))
    window_start_path = enriched_dir / f"date={START.isoformat()}" / "part.parquet"
    window_start_index = next((i for i, path in enumerate(warmup_paths) if path == window_start_path), None)
    if window_start_index is None or window_start_index < 59:
        raise RuntimeError("cannot locate 59 enriched warmup sessions before the frozen window")
    warmup_paths = warmup_paths[window_start_index - 59:window_start_index]
    price_history = pl.concat([
        pl.scan_parquet([str(path) for path in warmup_paths], hive_partitioning=True)
        .select(["symbol", "date", "close"])
        .collect(),
        price_history.select(["symbol", "date", "close"]),
    ]).sort(["symbol", "date"])
    price_history = (
        price_history
        .with_columns(pl.col("close").rolling_mean(60).over("symbol").alias("ma60"))
        .filter(pl.col("date").is_between(START, END, closed="both"))
        .with_columns(pl.lit(1, dtype=pl.UInt8).alias("_enriched_present"))
    )
    enriched = price_history.select(["symbol", "date", "ma60", "_enriched_present"])
    for label, frame in (("membership", membership), ("alt_hs300", alt), ("enriched", enriched)):
        if frame.select(pl.struct(["date", "symbol"]).n_unique()).item() != frame.height:
            raise RuntimeError(f"duplicate (date, symbol) keys in {label}")

    macro_aggs = []
    distinct_aggs = []
    for gate_key, (field, _) in GATES.items():
        macro_aggs.extend([pl.col(field).drop_nulls().first().alias(field)])
        distinct_aggs.append(pl.col(field).drop_nulls().n_unique().alias(f"_{gate_key}_distinct"))
    macro_dates = alt.group_by("date").agg([*macro_aggs, *distinct_aggs])
    inconsistent = {
        gate_key: int(macro_dates.filter(pl.col(f"_{gate_key}_distinct") > 1).height)
        for gate_key in GATES
    }
    if any(inconsistent.values()):
        raise RuntimeError(f"market-wide macro values disagree within date: {inconsistent}")

    base_dates = pl.DataFrame({"date": sorted(member_dates)})
    date_frame = base_dates.join(macro_dates, on="date", how="left").sort("date")
    gate_date_columns = []
    for gate_key, (field, threshold) in GATES.items():
        gate_col = f"_{gate_key}_pass"
        date_frame = date_frame.with_columns((pl.col(field).is_not_null() & (pl.col(field) >= threshold)).alias(gate_col))
        gate_date_columns.append(gate_col)
    date_rows = date_frame.to_dicts()
    date_metrics = {}
    for gate_key, (field, threshold) in GATES.items():
        known = date_frame.filter(pl.col(field).is_not_null())
        passed = known.filter(pl.col(f"_{gate_key}_pass")).height
        failed = known.height - passed
        date_metrics[gate_key] = {
            "field": field, "threshold": threshold, "known_dates": known.height,
            "missing_dates": date_frame.height - known.height, "pass_dates": passed,
            "fail_dates": failed, "pass_rate_known": passed / known.height if known.height else None,
            "longest_consecutive_failed_known_date_run": longest_failed_run(date_rows, f"_{gate_key}_pass"),
        }
    complete_dates = date_frame.filter(pl.all_horizontal([pl.col(field).is_not_null() for field, _ in GATES.values()]))
    combos_dates = bool_counts(complete_dates, tuple(gate_date_columns))

    # Construct the pre-gate member-date cohort; never read return or trade files.
    joined = (
        membership.join(alt, on=["date", "symbol"], how="left")
        .join(enriched, on=["date", "symbol"], how="left")
        .join(date_frame.select(["date", *gate_date_columns]), on="date", how="left")
        .sort(["date", "symbol"])
    )
    feature_available = pl.col("_enriched_present").is_not_null() & pl.col("ma60").is_not_null() & pl.col("_ext_present").is_not_null() & pl.col(HOLDER).is_not_null()
    pre_gate_cohort = joined.filter(feature_available)
    cohort = pre_gate_cohort.filter(pl.all_horizontal([pl.col(gate_col) for gate_col in gate_date_columns]))
    cohort_combos = bool_counts(cohort, tuple(gate_date_columns))
    full_eligible_rows = cohort_combos["111"]
    omitted_gate_rows = {}
    for index, (gate_key, _) in enumerate(GATES.items()):
        other_positions = [position for position in range(3) if position != index]
        two_pass_mask = pre_gate_cohort.get_column(gate_date_columns[other_positions[0]]) & pre_gate_cohort.get_column(gate_date_columns[other_positions[1]])
        two_pass = pre_gate_cohort.filter(two_pass_mask)
        blocked = two_pass.filter(~pl.col(gate_date_columns[index]))
        omitted_gate_rows[gate_key] = {
            "other_two_gates_pass_rows": two_pass.height,
            "blocked_rows_released_if_only_this_gate_removed": blocked.height,
            "fraction_of_other_two_pass_rows_blocked": blocked.height / two_pass.height if two_pass.height else None,
            "counterfactual_rows_with_this_gate_removed": two_pass.height,
            "accounting_reconciles": two_pass.height == full_eligible_rows + blocked.height,
        }

    attrition = joined.select([
        pl.len().alias("membership_rows"),
        pl.col("_ext_present").is_not_null().sum().alias("alt_partition_rows_present"),
        pl.col("_enriched_present").is_not_null().sum().alias("enriched_rows_present"),
        pl.col("_enriched_present").is_not_null().and_(pl.col("ma60").is_not_null()).sum().alias("ma60_nonnull_rows"),
        pl.col("_ext_present").is_not_null().and_(pl.col(HOLDER).is_not_null()).sum().alias("holder_nonnull_rows"),
        pl.col("_ext_present").is_null().sum().alias("rows_without_alt_partition_match"),
        pl.col("_enriched_present").is_null().sum().alias("rows_without_enriched_match"),
    ]).row(0, named=True)
    cohort_combos_total = sum(cohort_combos.values())
    date_combos_total = sum(combos_dates.values())
    checks = {
        "preregistration_hash_valid": True, "panel_registration_and_readback_pass": True,
        "frozen_input_hashes_match": True, "unique_membership_keys": True,
        "unique_alt_keys": True, "unique_enriched_keys": True,
        "macro_values_consistent_within_date": not any(inconsistent.values()),
        "row_combo_partition_reconciles": cohort_combos_total == cohort.height,
        "date_combo_partition_reconciles": date_combos_total == complete_dates.height,
        "omitted_gate_row_identities_reconcile": all(row["accounting_reconciles"] for row in omitted_gate_rows.values()),
        "no_returns_or_backtest_data_loaded": True,
    }
    result = {
        "round": "R286", "classification": "frozen_macro_gate_exposure_and_overlap_diagnostic_no_returns_no_backtest",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "preregistration_sha256": prereg_hash, "panel_registration_sha256": panel_hash,
        "input_snapshot_sha256": input_snapshot_hash,
        "window": {"start": START.isoformat(), "end": END.isoformat(),
                    "historical_membership_dates": len(member_dates), "dates_with_macro_inputs": macro_dates.height,
                    "aligned_membership_alt_enriched_dates": len(used_dates),
                    "membership_rows": membership.height, "membership_source": "r78_daily_membership query_date"},
        "frozen_thresholds": {key: {"field": field, "minimum": threshold} for key, (field, threshold) in GATES.items()},
        "market_wide_gate_date_metrics": date_metrics,
        "eight_gate_state_date_counts": combos_dates,
        "feature_attrition_rows": attrition,
        "pre_gate_feature_eligible_cohort": {
            "definition": "historical member-date row with ma60 and holder value and all three macro values non-null, before applying pass/fail gates",
            "rows": pre_gate_cohort.height, "date_count": pre_gate_cohort.get_column("date").n_unique() if pre_gate_cohort.height else 0,
            "eight_gate_state_row_counts": bool_counts(pre_gate_cohort, tuple(gate_date_columns)),
            "all_three_gates_pass_rows": full_eligible_rows,
            "all_three_gates_pass_fraction": full_eligible_rows / pre_gate_cohort.height if pre_gate_cohort.height else None,
        },
        "all_three_gate_eligible_cohort": {
            "rows": cohort.height, "date_count": cohort.get_column("date").n_unique() if cohort.height else 0,
            "eight_gate_state_row_counts": cohort_combos,
            "all_three_gates_pass_rows": full_eligible_rows,
            "all_three_gates_pass_fraction": full_eligible_rows / cohort.height if cohort.height else None,
        },
        "incremental_row_exclusion_by_single_gate": omitted_gate_rows,
        "source_integrity": {"frozen_files": frozen_hashes,
                               "alt_partition_count": input_snapshot["alt_hs300_relevant_partitions"]["partition_count"],
                               "alt_inventory_sha256": input_snapshot["alt_hs300_relevant_partitions"]["inventory_sha256"],
                               "enriched_partition_count": input_snapshot["enriched_relevant_partitions"]["partition_count"],
                               "enriched_inventory_sha256": input_snapshot["enriched_relevant_partitions"]["inventory_sha256"],
                               "macro_within_date_distinct_values_over_one": inconsistent},
        "checks": checks, "all_checks_pass": all(checks.values()),
        "supersedes_audit_sha256": prior_audit_hash,
        "classification_limits": [
            "This measures how often frozen thresholds exclude feature-eligible historical constituent rows, not whether those excluded rows would have earned returns.",
            "Supersedes the first R286 audit: that version mistakenly defined the pre-gate cohort by requiring all three gates to pass, making its single-gate row exclusions zero by construction; preserve that artifact as an error record.",
            "MA60 is computed from enriched close history using a 60-observation rolling mean, matching the system indicator definition; 59 prior market sessions are loaded for warmup.",
            "Feature cohort omits the strategy's price, market-cap, amount, ST, new-listing and score-rank constraints; it is not a final executable candidate mask.",
            "Historical membership snapshots and current local macro values can include revision/timing bias; source publication-time PIT is not proven.",
            "No outcome series, trade log, equity curve, backtest, screener run, provider fetch, paper account or order was used or created.",
        ],
        "alpha_decision": "not_tested", "strategy_status": "research_only",
        "production_status": "pause_production",
    }
    audit_hash = save_once(AUDIT, (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8"))

    removed = result["incremental_row_exclusion_by_single_gate"]
    eligible = result["pre_gate_feature_eligible_cohort"]
    lines = [
        "# R286 宏观门禁覆盖与重叠诊断", "",
        "## 研究范围", "",
        f"冻结窗口为 {START.isoformat()} 至 {END.isoformat()}，使用 R78 历史逐日成分和 R266 三项原阈值。",
        "本轮只统计门槛通过率、重叠和特征合格成分行的增量排除数，没有读取股票收益、交易、净值或回测结果。", "",
        "## 数据覆盖", "",
        f"窗口内有 {len(member_dates):,} 个历史成分交易日、{membership.height:,} 个成分日行；特征源对齐日期 {len(used_dates):,} 天。",
        f"预门槛特征合格队列为 {eligible['rows']:,} 行，包含 MA60、前十大流通股东占比和三项宏观值。三门槛全部通过 {full_eligible_rows:,} 行（{eligible['all_three_gates_pass_fraction']:.2%}）。",
        "门槛日期通过率按非空日期计算；缺值不算通过，缺失日期单独报告。宏观门槛按市场日期统计，因为同一宏观值会复制到成分股行。", "",
        "## 单项门槛的增量排除", "",
        "每项“增量排除”都限定在另外两项门槛已通过的行中；若移除该项，这些行会回到候选池。", "",
        "| 门槛 | 另外两项通过 | 被本项额外排除 | 排除占比 |", "| --- | ---: | ---: | ---: |",
    ]
    for gate_key, label in (("equity_risk_premium", "ERP ≥ 5.5"), ("credit_spread_5y_low", "信用利差 ≥ -0.8"), ("term_spread_10y_2y", "期限利差 ≥ 0.3")):
        row = removed[gate_key]
        rate = row["fraction_of_other_two_pass_rows_blocked"]
        lines.append(f"| {label} | {row['other_two_gates_pass_rows']:,} | {row['blocked_rows_released_if_only_this_gate_removed']:,} | {rate:.2%} |")
    lines.extend(["", "## 结论", "",
                  "更正说明：初版 R286 把全部门槛通过作为队列入选条件，导致增量排除数被算成 0。该初版文件仍保留，完整更正结果见本版本。",
                  "这轮可以判断门槛在历史输入上是否活跃、排除是否重叠；不能判断被排除标的后来是否上涨，因此不是放宽规则或确认 alpha 的证据。R275 的回测表现不可替代这项门槛覆盖统计，也不能由覆盖率推导收益。",
                  "当前数据链缺少可验证的供应商发布时间与历史修订版本；结果有时间点数据限制。候选保持 research-only，生产维持 pause_production。", "",
                  f"证据：预注册 SHA-256 `{prereg_hash}`；面板登记与读回 SHA-256 `{panel_hash}`；输入快照 SHA-256 `{input_snapshot_hash}`；审计 SHA-256 `{audit_hash}`。",
                  f"初版审计 SHA-256 `{prior_audit_hash}`；更正审计 `r286_gate_exposure_audit_v2.json` 保存逐门槛日期统计、八种组合、输入分区哈希和计数核对。输入快照见 `r286_input_snapshot.json`。", ""])
    result_hash = save_once(RESULT, "\n".join(lines).encode("utf-8"))
    print(json.dumps({"round": "R286", "all_checks_pass": result["all_checks_pass"],
                      "audit_sha256": audit_hash, "result_sha256": result_hash,
                      "feature_eligible_rows": cohort.height, "all_gates_pass_rows": full_eligible_rows,
                      "gate_exclusions": {key: value["blocked_rows_released_if_only_this_gate_removed"] for key, value in removed.items()},
                      "alpha_decision": "not_tested"}, ensure_ascii=False, indent=2))
    return 0 if result["all_checks_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
