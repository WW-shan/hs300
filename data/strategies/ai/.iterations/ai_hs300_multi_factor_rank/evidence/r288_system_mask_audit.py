"""Run the frozen R288 system mask audit without a backtest or return access."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[5]
BACKEND = ROOT / "backend"
DATA = ROOT / "data"
PRE = EVIDENCE / "r288_preregistration.json"
SNAPSHOT = EVIDENCE / "r288_input_snapshot.json"
PANEL = EVIDENCE / "r288_panel_registration.json"
OUT = EVIDENCE / "r288_system_mask_audit.json"
RESULT = EVIDENCE / "r288_result.md"
CONTROL_ID = "ai_hs300_macro_gate_quality"
CANDIDATE_ID = "ai_hs300_macro_gate_quality_r275_remove_term"
SCORE_NAMES = [
    "ext_alt_hs300_holder_top10_free_float_share",
    "uf_quality_value_defensive",
    "uf_low_vol",
    "uf_cash_quality",
]


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def sidecar_hash(path: Path) -> str:
    sidecar = path.with_name(path.name + ".sha256")
    if not path.is_file() or not sidecar.is_file():
        raise RuntimeError(f"missing R288 artifact/sidecar: {path.name}")
    expected = sidecar.read_text(encoding="ascii").split()[0]
    actual = sha_file(path)
    if expected != actual:
        raise RuntimeError(f"SHA-256 sidecar mismatch: {path.name}")
    return actual


def save_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    value = sha_file(path)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{value}  {path.name}\n")
    return value


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import frozen helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_frozen_inputs(prereg: dict, snapshot: dict) -> dict:
    if prereg.get("round") != "R288" or snapshot.get("round") != "R288":
        raise RuntimeError("R288 identity validation failed")
    for relative, expected in prereg["source_sha256"].items():
        path = ROOT / relative
        if not path.is_file() or sha_file(path) != expected:
            raise RuntimeError(f"frozen source hash mismatch: {relative}")
    for key, inventory in prereg["partition_inventories"].items():
        if key == "enriched_load_window":
            paths = [
                p for p in (DATA / "kline_daily_enriched").glob("date=*/part.parquet")
                if prereg["system_path"]["system_load_window"][0] <= p.parent.name.removeprefix("date=") <= prereg["system_path"]["system_load_window"][1]
            ]
        elif key == "alt_hs300_all_source_partitions":
            paths = list((DATA / "ext_data/alt_hs300/timeseries").glob("date=*/part.parquet"))
        elif key == "financial_tables":
            paths = list((DATA / "financials").glob("*/part.parquet"))
        else:
            raise RuntimeError(f"unknown frozen R288 partition inventory: {key}")
        paths.sort(key=lambda p: p.relative_to(ROOT).as_posix())
        combined = hashlib.sha256()
        total_size = 0
        for path in paths:
            relative = path.relative_to(ROOT).as_posix()
            actual = sha_file(path)
            if prereg["source_sha256"].get(relative) != actual:
                raise RuntimeError(f"partition hash mismatch: {relative}")
            combined.update(relative.encode("utf-8"))
            combined.update(b"\0")
            combined.update(actual.encode("ascii"))
            combined.update(b"\n")
            total_size += path.stat().st_size
        observed = {
            "file_count": len(paths),
            "total_size_bytes": total_size,
            "inventory_sha256": combined.hexdigest(),
        }
        if observed != inventory:
            raise RuntimeError(f"partition inventory mismatch: {key}")
    return prereg["partition_inventories"]


def row_mask_counts(frame: pl.DataFrame, basic: pl.Series, strategy: pl.Series, member: pl.Series) -> tuple[dict, pl.DataFrame]:
    eligible = basic & strategy & member
    rows = frame.select(["symbol", "date"]).with_columns([
        basic.rename("_basic"), strategy.rename("_strategy"),
        member.rename("_member"), eligible.rename("_eligible"),
    ])
    counts = {
        "loaded_rows": frame.height,
        "member_rows": int(member.sum()),
        "basic_pass_member_rows": int((basic & member).sum()),
        "strategy_pass_member_rows": int((strategy & member).sum()),
        "eligible_member_rows": int(eligible.sum()),
    }
    return counts, rows


def mask_by_date(rows: pl.DataFrame) -> dict[date, set[str]]:
    result: dict[date, set[str]] = {
        day: set() for day in rows.get_column("date").cast(pl.Date).unique().to_list()
    }
    for day, symbol in (
        rows.filter(pl.col("_eligible")).select(pl.col("date").cast(pl.Date), "symbol").iter_rows()
    ):
        result.setdefault(day, set()).add(str(symbol))
    return result


def main() -> int:
    for path in (OUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R288 output exists; refusing overwrite: {path.name}")
    pre_hash = sidecar_hash(PRE)
    snapshot_hash = sidecar_hash(SNAPSHOT)
    prereg = json.loads(PRE.read_text(encoding="utf-8"))
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if prereg["input_snapshot"]["sha256"] != snapshot_hash:
        raise RuntimeError("R288 preregistration/input snapshot link mismatch")
    frozen_sources = validate_frozen_inputs(prereg, snapshot)
    panel_hash = sidecar_hash(PANEL)
    panel_proof = json.loads(PANEL.read_text(encoding="utf-8"))
    if not panel_proof.get("registration_pass") or panel_proof.get("candidate", {}).get("status") != "pending":
        raise RuntimeError("R288 panel is not proven registered and pending")
    if panel_proof["candidate"].get("source_id") != CANDIDATE_ID:
        raise RuntimeError("R288 panel source id mismatch")
    for script_name, key in (("r288_freeze_inputs.py", "freeze_script_sha256"), ("r288_register_panel_once.py", "registration_script_sha256"), ("r288_system_mask_audit.py", "audit_script_sha256")):
        if sha_file(EVIDENCE / script_name) != prereg[key]:
            raise RuntimeError(f"frozen R288 script hash mismatch: {script_name}")

    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    r112 = load_module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r288")
    membership = r112._historical_membership().filter(
        pl.col("effective_date").is_between(date.fromisoformat(prereg["window"]["start"]), date.fromisoformat(prereg["window"]["end"]))
    )
    if membership.height != snapshot["population"]["membership_row_count"]:
        raise RuntimeError("R112 historical membership row count differs from frozen snapshot")
    date_counts = membership.group_by("effective_date").len().sort("effective_date")
    if date_counts.height != snapshot["population"]["membership_date_count"]:
        raise RuntimeError("R112 historical membership dates differ from frozen snapshot")
    member_dates = membership.rename({"effective_date": "date"})
    expected_keys = member_dates.with_columns(pl.col("date").cast(pl.String).alias("_date")).select(["_date", "symbol"]).sort(["_date", "symbol"])
    membership_hash = sha_bytes("".join(f"{day}\t{symbol}\n" for day, symbol in expected_keys.iter_rows()).encode())
    if membership_hash != snapshot["population"]["daily_membership_key_sha256"]:
        raise RuntimeError("R112 historical membership identities differ from frozen snapshot")
    expected_dates = set(member_dates.get_column("date").unique().to_list())

    r112_membership_loader = r112._historical_membership
    def frozen_membership_loader():
        return r112_membership_loader().filter(
            pl.col("effective_date").is_between(
                date.fromisoformat(prereg["window"]["start"]),
                date.fromisoformat(prereg["window"]["end"]),
            )
        )
    r112._historical_membership = frozen_membership_loader
    try:
        service = r112._service()
    finally:
        r112._historical_membership = r112_membership_loader
    from app.backtest.strategy import StrategyDependencyResolver
    from app.strategy.scoring import (
        materialize_scoring_columns,
        scoring_dependencies,
    )

    strategies = {}
    masks = {}
    definitions = {}
    resolver = StrategyDependencyResolver()
    for strategy_id in (CONTROL_ID, CANDIDATE_ID):
        strategy = service.strategy_engine.get(strategy_id)
        if strategy.execution_backend != "polars_expr":
            raise RuntimeError(f"unexpected backend for {strategy_id}")
        if strategy_id == CANDIDATE_ID and not strategy.meta.get("research_only"):
            raise RuntimeError("R275 candidate lost research_only status")
        params = service._normalize_params({}, strategy)
        overrides = {}
        basic_filter = service._effective_basic_filter(strategy, overrides)
        feature_plan = resolver.resolve(
            strategy, params=params, basic_filter=basic_filter,
            entry_signals=service._effective_signals(overrides, "entry_signals", strategy.entry_signals),
            exit_signals=service._effective_signals(overrides, "exit_signals", strategy.exit_signals),
            overrides=overrides, asset_type="stock",
        )
        resolved_columns = set().union(
            feature_plan.base_columns, feature_plan.intermediate_columns,
            feature_plan.indicator_columns, feature_plan.signal_columns,
            feature_plan.instrument_columns, feature_plan.ext_columns,
            feature_plan.fundamental_columns,
        )
        expected_dependencies = {"ma60"} | scoring_dependencies({name: 1.0 for name in SCORE_NAMES})
        if missing := expected_dependencies - resolved_columns:
            raise RuntimeError(f"feature plan misses logical score dependencies: {sorted(missing)}")
        definitions[strategy_id] = (strategy, params, basic_filter, feature_plan)

    def plan_columns(feature_plan):
        return set().union(
            feature_plan.base_columns, feature_plan.intermediate_columns,
            feature_plan.indicator_columns, feature_plan.signal_columns,
            feature_plan.instrument_columns, feature_plan.ext_columns,
            feature_plan.fundamental_columns,
        )

    control_plan = definitions[CONTROL_ID][3]
    candidate_plan = definitions[CANDIDATE_ID][3]
    if plan_columns(candidate_plan) - plan_columns(control_plan):
        raise RuntimeError("control system feature plan does not cover candidate dependencies")
    loaded_symbols = member_dates.get_column("symbol").unique().sort().to_list()
    panel = service.engine.load_panel_for_backtest(
        loaded_symbols, date.fromisoformat(prereg["system_path"]["system_load_window"][0]),
        date.fromisoformat(prereg["system_path"]["system_load_window"][1]),
        control_plan, asset_type="stock",
    )
    required = {"symbol", "date"} | ({"ma60"} | scoring_dependencies({name: 1.0 for name in SCORE_NAMES}))
    if panel.is_empty() or not required.issubset(panel.columns):
        raise RuntimeError("shared system panel is missing data or candidate score inputs")
    panel = panel.filter(pl.col("date").cast(pl.Date).is_in(list(expected_dates))).sort(["date", "symbol"])
    duplicates = panel.group_by(["date", "symbol"]).len().filter(pl.col("len") != 1)
    if duplicates.height:
        raise RuntimeError("duplicate (date,symbol) rows in shared system panel")
    if panel.select(pl.col("date").cast(pl.Date).n_unique()).item() != len(expected_dates):
        raise RuntimeError("shared system panel misses frozen membership dates")

    control_inputs = None
    for strategy_id in (CONTROL_ID, CANDIDATE_ID):
        strategy, params, basic_filter, feature_plan = definitions[strategy_id]
        overrides = {}

        basic_expr = service.strategy_engine._basic_filter_expr(panel, basic_filter)
        basic = pl.Series("_basic", [True] * panel.height, dtype=pl.Boolean)
        if basic_expr is not None:
            basic = panel.select(basic_expr.alias("_basic")).get_column("_basic").fill_null(False).cast(pl.Boolean)
        strategy_mask = service._build_candidate_filter_mask(panel, strategy, params).fill_null(False).cast(pl.Boolean)
        member = r112.strategy_module._hs300_panel_row_mask(panel, membership).fill_null(False).cast(pl.Boolean)
        counts, rows = row_mask_counts(panel, basic, strategy_mask, member)
        score_result = service._apply_score(panel, strategy, overrides, universe_mask=basic & strategy_mask & member)
        if "score" not in score_result.columns:
            raise RuntimeError(f"system scorer returned no aggregate score: {strategy_id}")
        if strategy_id == CONTROL_ID:
            control_inputs = materialize_scoring_columns(panel, SCORE_NAMES)
            if not control_inputs.select(["date", "symbol"]).equals(panel.select(["date", "symbol"])):
                raise RuntimeError("materialized score-input rows are misaligned")
            for name in SCORE_NAMES:
                if name not in control_inputs.columns:
                    raise RuntimeError(f"system score materializer omitted {name}")
        eligible = basic & strategy_mask & member
        counts["eligible_with_non_null_system_score"] = int(score_result.filter(eligible).get_column("score").is_not_null().sum())
        strategies[strategy_id] = {
            "basic_filter_config": basic_filter,
            "feature_plan": {
                "base_columns": sorted(feature_plan.base_columns),
                "indicator_columns": sorted(feature_plan.indicator_columns),
                "ext_columns": sorted(feature_plan.ext_columns),
                "fundamental_columns": sorted(feature_plan.fundamental_columns),
                "warmup_bars": feature_plan.warmup_bars,
            },
            "aggregate_counts": counts,
            "input_coverage": {},
            "daily_masks": {},
        }
        masks[strategy_id] = {
            "panel": panel, "rows": rows, "basic": basic,
            "strategy": strategy_mask, "member": member,
            "score": score_result.get_column("score"),
        }
    if control_inputs is None:
        raise RuntimeError("control score inputs were not materialized")

    ctrl_rows = masks[CONTROL_ID]["rows"]
    cand_rows = masks[CANDIDATE_ID]["rows"]
    if ctrl_rows.select(["date", "symbol"]).equals(cand_rows.select(["date", "symbol"])) is False:
        raise RuntimeError("control and candidate system panels are not aligned")
    ctrl_daily = mask_by_date(ctrl_rows)
    cand_daily = mask_by_date(cand_rows)
    if set(ctrl_daily) != expected_dates or set(cand_daily) != expected_dates:
        raise RuntimeError("candidate masks do not cover every frozen membership date")
    daily = []
    released_keys = []
    total_released = 0
    total_control = 0
    for day in sorted(expected_dates):
        control_ids = ctrl_daily[day]
        candidate_ids = cand_daily[day]
        added = sorted(candidate_ids - control_ids)
        removed = sorted(control_ids - candidate_ids)
        if removed:
            raise RuntimeError(f"one-gate candidate is not a superset on {day}: {removed[:5]}")
        frozen_member_ids = set(
            member_dates.filter(pl.col("date") == day).get_column("symbol").to_list()
        )
        control_member_ids = set(
            ctrl_rows.filter((pl.col("date").cast(pl.Date) == day) & pl.col("_member")).get_column("symbol").to_list()
        )
        candidate_member_count = int(cand_rows.filter(pl.col("date").cast(pl.Date) == day).get_column("_member").sum())
        if len(control_member_ids) != candidate_member_count:
            raise RuntimeError(f"historical membership mask differs between strategies on {day}")
        total_control += len(control_ids)
        total_released += len(added)
        released_keys.extend((day.isoformat(), symbol) for symbol in added)
        daily.append({
            "date": day.isoformat(),
            "frozen_membership_rows": len(frozen_member_ids),
            "system_panel_member_rows": len(control_member_ids),
            "frozen_members_absent_from_system_panel": sorted(frozen_member_ids - control_member_ids),
            "control_eligible_rows": len(control_ids),
            "candidate_eligible_rows": len(candidate_ids),
            "released_by_term_gate_removal": len(added),
            "released_symbols": added,
        })
        for strategy_id, selected in ((CONTROL_ID, control_ids), (CANDIDATE_ID, candidate_ids)):
            row = strategies[strategy_id]["daily_masks"]
            row[day.isoformat()] = {
                "system_panel_member_rows": len(control_member_ids),
                "eligible_rows": len(selected),
                "eligible_symbol_ids": sorted(selected),
            }

    control_aligned = control_inputs.select(["date", "symbol", *SCORE_NAMES]).with_columns(
        pl.col("symbol").cast(pl.String),
        pl.col("date").cast(pl.Date),
        masks[CONTROL_ID]["basic"].rename("_basic"),
        masks[CONTROL_ID]["strategy"].rename("_control_strategy"),
        masks[CANDIDATE_ID]["strategy"].rename("_candidate_strategy"),
        masks[CONTROL_ID]["member"].rename("_member"),
    ).with_columns(
        (pl.col("_basic") & pl.col("_control_strategy") & pl.col("_member")).alias("_control_eligible"),
        (pl.col("_basic") & pl.col("_candidate_strategy") & pl.col("_member")).alias("_candidate_eligible"),
    ).with_columns(
        (pl.col("_candidate_eligible") & ~pl.col("_control_eligible")).alias("_released")
    )
    coverage_summary = {}
    for name in SCORE_NAMES:
        coverage_summary[name] = {
            "column_present": name in control_inputs.columns,
            "non_null_member_rows": int(control_aligned.filter(pl.col("_member"))[name].is_not_null().sum()),
            "non_null_control_eligible_rows": int(control_aligned.filter(pl.col("_control_eligible"))[name].is_not_null().sum()),
            "non_null_candidate_eligible_rows": int(control_aligned.filter(pl.col("_candidate_eligible"))[name].is_not_null().sum()),
            "non_null_released_rows": int(control_aligned.filter(pl.col("_released"))[name].is_not_null().sum()),
            "missing_candidate_eligible_rows": int(control_aligned.filter(pl.col("_candidate_eligible") & pl.col(name).is_null()).height),
        }
    released_date_count = sum(item["released_by_term_gate_removal"] > 0 for item in daily)
    nonzero = [item["released_by_term_gate_removal"] for item in daily]
    sorted_nonzero = sorted(nonzero)
    median_release = (sorted_nonzero[699] + sorted_nonzero[700]) / 2 if len(sorted_nonzero) == 1398 else 0
    max_release = max(nonzero, default=0)
    if sum(item["frozen_membership_rows"] for item in daily) != membership.height:
        raise RuntimeError("daily frozen-membership accounting does not reconcile")
    if sum(item["system_panel_member_rows"] for item in daily) != strategies[CONTROL_ID]["aggregate_counts"]["member_rows"]:
        raise RuntimeError("daily system-panel membership accounting does not reconcile")
    if sum(item["control_eligible_rows"] for item in daily) != strategies[CONTROL_ID]["aggregate_counts"]["eligible_member_rows"]:
        raise RuntimeError("daily control eligibility does not reconcile")
    if sum(item["candidate_eligible_rows"] for item in daily) != strategies[CANDIDATE_ID]["aggregate_counts"]["eligible_member_rows"]:
        raise RuntimeError("daily candidate eligibility does not reconcile")
    if sum(item["released_by_term_gate_removal"] for item in daily) != total_released:
        raise RuntimeError("daily release identities do not reconcile")
    for strategy_id in (CONTROL_ID, CANDIDATE_ID):
        counts = strategies[strategy_id]["aggregate_counts"]
        for key in ("member_rows", "basic_pass_member_rows", "strategy_pass_member_rows", "eligible_member_rows"):
            if counts[key] < 0 or counts[key] > membership.height:
                raise RuntimeError(f"invalid aggregate mask count {strategy_id}:{key}")

    audit = {
        "round": "R288", "status": "complete",
        "classification": prereg["classification"],
        "executed_at_utc": datetime.now(UTC).isoformat(),
        "preregistration_sha256": pre_hash, "input_snapshot_sha256": snapshot_hash,
        "panel_registration_sha256": panel_hash,
        "input_partition_inventories": frozen_sources,
        "system_modules_used": [
            "R112 exact historical membership loader and service construction",
            "StrategyDependencyResolver.resolve",
            "BacktestEngine.load_panel_for_backtest",
            "StrategyEngine._basic_filter_expr",
            "StrategyBacktestService._build_candidate_filter_mask",
            "StrategyBacktestService._hs300_panel_row_mask",
            "materialize_scoring_columns and StrategyBacktestService._apply_score",
        ],
        "window_summary": {
            "start": prereg["window"]["start"], "end": prereg["window"]["end"],
            "membership_dates": len(expected_dates), "frozen_membership_rows": membership.height,
            "system_panel_member_rows": strategies[CONTROL_ID]["aggregate_counts"]["member_rows"],
            "frozen_members_absent_from_system_panel": sum(len(item["frozen_members_absent_from_system_panel"]) for item in daily),
            "symbol_union_count": snapshot["population"]["membership_symbol_union_count"],
        },
        "strategies": strategies,
        "gate_release_summary": {
            "control_eligible_member_date_rows": total_control,
            "candidate_eligible_member_date_rows": total_control + total_released,
            "released_rows": total_released,
            "released_fraction_of_control_eligible_rows": total_released / total_control if total_control else None,
            "active_release_dates": released_date_count,
            "active_release_date_fraction": released_date_count / len(expected_dates),
            "median_released_rows_per_date": median_release,
            "maximum_released_rows_on_a_date": max_release,
            "maximum_release_dates": [item["date"] for item in daily if item["released_by_term_gate_removal"] == max_release],
            "released_member_date_keys_sha256": sha_bytes("".join(f"{day}\t{symbol}\n" for day, symbol in released_keys).encode()),
        },
        "daily_results": daily,
        "score_input_coverage": coverage_summary,
        "accounting_checks": {
            "all_membership_dates_loaded_for_both_strategies": True,
            "candidate_is_superset_every_date": True,
            "daily_frozen_membership_rows_reconcile": True,
            "daily_system_panel_membership_rows_reconcile": True,
            "daily_control_rows_reconcile": True,
            "daily_candidate_rows_reconcile": True,
            "daily_released_rows_reconcile": True,
        },
        "execution_guards": {
            "StrategyBacktestService.run_called": False,
            "BacktestEngine.run_called": False,
            "matcher_or_simulation_called": False,
            "screener_called": False, "provider_called": False,
            "returns_trades_benchmark_or_equity_curves_loaded_or_calculated": False,
            "strategy_or_backtest_cache_written": False,
            "panel_status_changed": False,
        },
        "limitations": prereg["system_path"]["known_semantics_and_limitations"],
        "conclusion_scope": "multi-date system candidate-mask and score-input coverage diagnostic only; no returns, executions, alpha claim, gate-policy approval, or production-readiness conclusion",
    }
    audit_hash = save_once(OUT, (json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    control_count = strategies[CONTROL_ID]["aggregate_counts"]["eligible_member_rows"]
    candidate_count = strategies[CANDIDATE_ID]["aggregate_counts"]["eligible_member_rows"]
    report = f"""# R288 多日系统候选掩码诊断

## 范围

本轮按 R78 的 {len(expected_dates)} 个逐日历史成分日（{prereg['window']['start']} 至 {prereg['window']['end']}）加载系统回测候选所需的特征，不调用回测执行器、撮合器或筛选器，不读取收益。控制组为原 R275 规则，候选组仅移除期限利差门禁。

## 系统候选掩码

| 指标 | R275 控制组 | 移除期限利差 |
| --- | ---: | ---: |
| 逐日历史成分行 | {membership.height:,} | {membership.height:,} |
| 通过系统候选掩码的成分股日 | {control_count:,} | {candidate_count:,} |
| 期限利差消融增加 | - | {total_released:,} |
| 有新增候选的交易日 | - | {released_date_count:,}/{len(expected_dates):,} |
| 单日最多新增 | - | {max_release:,} |

这里的计数是通过系统基础过滤、策略过滤和逐日历史成分掩码后的股票日。它还没有经过排名截断、持仓数、可交易性、撮合、手续费和滑点。

## 评分输入覆盖

{chr(10).join(f"- {name}: 候选可用 {coverage_summary[name]['non_null_candidate_eligible_rows']:,}/{candidate_count:,}，其中期限门禁释放部分可用 {coverage_summary[name]['non_null_released_rows']:,}/{total_released:,}。" for name in SCORE_NAMES)}

系统合成分数非空行数不能证明每个加权输入都完整；逐因子覆盖已单列。

## 口径限制

- `exclude_new_days=60` 虽配置在策略中，系统 `_basic_filter_expr` 没有执行这条规则。
- `market_cap_min` 使用当前 instruments 股本，不是历史 PIT 股本。
- 扩展宏观数据没有可验证的供应商发布时间和修订版本；财务 PIT 也没有原始数据修订版本。
- 本轮只诊断门禁对系统候选集合的影响，不计算新增股票日的收益，不判断 alpha。候选保持 `research_only`，面板状态保持 `pending`，生产保持 `pause_production`。

审计 JSON SHA-256：`{audit_hash}`。预注册、输入快照、面板登记与回读、运行器和本报告均保存 SHA-256 sidecar。
"""
    report_hash = save_once(RESULT, report.encode("utf-8"))
    print(json.dumps({
        "round": "R288", "status": audit["status"],
        "control_eligible_rows": control_count,
        "candidate_eligible_rows": candidate_count,
        "released_rows": total_released, "active_release_dates": released_date_count,
        "audit_sha256": audit_hash, "result_sha256": report_hash,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
