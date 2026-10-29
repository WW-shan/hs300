"""Run the frozen R287 system panel/filter/score diagnostic; never run a backtest."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl

EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[5]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
DATA = ROOT / "data"
PRE = EVIDENCE / "r287_preregistration.json"
SNAPSHOT = EVIDENCE / "r287_input_snapshot.json"
PANEL = EVIDENCE / "r287_panel_registration.json"
OUT = EVIDENCE / "r287_system_mask_audit.json"
RESULT = EVIDENCE / "r287_result.md"
AS_OF = date(2026, 10, 14)
CONTROL_ID = "ai_hs300_macro_gate_quality"
CANDIDATE_ID = "ai_hs300_macro_gate_quality_r275_remove_term"
SCORE_NAMES = [
    "ext_alt_hs300_holder_top10_free_float_share",
    "uf_quality_value_defensive",
    "uf_low_vol",
    "uf_cash_quality",
]


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sidecar_hash(path: Path) -> str:
    sidecar = path.with_name(path.name + ".sha256")
    if not path.is_file() or not sidecar.is_file():
        raise RuntimeError(f"missing R287 artifact or sidecar: {path.name}")
    expected = sidecar.read_text(encoding="ascii").split()[0]
    actual = sha_file(path)
    if expected != actual:
        raise RuntimeError(f"SHA-256 sidecar mismatch: {path.name}")
    return actual


def inventory(paths: list[Path], root: Path) -> dict:
    ordered = sorted(paths, key=lambda path: path.relative_to(root).as_posix())
    digest = hashlib.sha256()
    for path in ordered:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha_file(path).encode("ascii"))
        digest.update(b"\n")
    return {
        "file_count": len(ordered),
        "total_size_bytes": sum(path.stat().st_size for path in ordered),
        "inventory_sha256": digest.hexdigest(),
    }


def save_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    digest = sha_file(path)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_frozen_inputs(prereg: dict) -> dict:
    for relative, expected in prereg["source_sha256"].items():
        path = ROOT / relative
        if not path.is_file() or sha_file(path) != expected:
            raise RuntimeError(f"frozen source hash mismatch: {relative}")
    start, end = prereg["system_path"]["load_window"]
    enriched = [
        path for path in (DATA / "kline_daily_enriched").glob("date=*/part.parquet")
        if start <= path.parent.name.removeprefix("date=") <= end
    ]
    # attach_ext_columns currently loads the full time-series and exact-joins it.
    ext = sorted((DATA / "ext_data/alt_hs300/timeseries").glob("date=*/part.parquet"))
    financial = sorted((DATA / "financials").glob("*/part.parquet"))
    current = {
        "enriched_warmup": inventory(enriched, ROOT),
        "alt_hs300_all_source_partitions": inventory(ext, ROOT),
        "financial_tables": inventory(financial, ROOT),
    }
    if current != prereg["partition_inventories"]:
        raise RuntimeError("frozen R287 partition inventory changed")
    return current


def membership_mask(panel: pl.DataFrame, membership: pl.DataFrame) -> pl.Series:
    from app.backtest.strategy import _hs300_panel_row_mask

    return _hs300_panel_row_mask(panel, membership)


def mask_counts(frame: pl.DataFrame, basic: pl.Series, strategy: pl.Series, member: pl.Series) -> tuple[dict, pl.DataFrame, pl.Series]:
    eligible = basic & strategy & member
    rows = frame.select(["symbol"]).with_columns([
        basic.rename("_basic"), strategy.rename("_strategy"), member.rename("_member"),
        eligible.rename("_eligible"),
    ])
    symbols = rows.filter(pl.col("_eligible")).get_column("symbol").to_list()
    return ({
        "loaded_as_of_rows": frame.height,
        "historical_member_rows": int(member.sum()),
        "basic_filter_pass_rows": int((basic & member).sum()),
        "strategy_filter_pass_rows": int((strategy & member).sum()),
        "eligible_rows": int(eligible.sum()),
        "rejected_by_basic_within_members": int((~basic & member).sum()),
        "rejected_by_strategy_after_basic_within_members": int((basic & ~strategy & member).sum()),
        "symbol_ids": sorted(symbols),
    }, rows, eligible)


def main() -> int:
    for path in (OUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R287 result exists; refusing overwrite: {path.name}")
    pre_hash = sidecar_hash(PRE)
    snapshot_hash = sidecar_hash(SNAPSHOT)
    prereg = json.loads(PRE.read_text(encoding="utf-8"))
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if prereg.get("round") != "R287" or prereg["input_snapshot"]["sha256"] != snapshot_hash:
        raise RuntimeError("R287 preregistration/snapshot identity check failed")
    audit_script_hash = sha_file(Path(__file__).resolve())
    amendment_files = sorted(
        EVIDENCE.glob("r287_preregistration_amendment*.json"),
        key=lambda path: (0 if path.stem == "r287_preregistration_amendment" else int(path.stem.rsplit("_v", 1)[1])),
    )
    if not amendment_files:
        raise RuntimeError("R287 append-only runner amendment chain is missing")
    previous_runner_hash = prereg["audit_script_sha256"]
    previous_amendment_hash = None
    amendment_chain = []
    for amendment_path in amendment_files:
        amendment_hash = sidecar_hash(amendment_path)
        amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
        if (
            amendment.get("round") != "R287"
            or amendment.get("preregistration_sha256") != pre_hash
            or amendment.get("supersedes_audit_script_sha256") != previous_runner_hash
            or amendment.get("prior_amendment_sha256") != previous_amendment_hash
        ):
            raise RuntimeError(f"R287 amendment chain broken at {amendment_path.name}")
        amendment_chain.append({
            "path": amendment_path.name,
            "sha256": amendment_hash,
            "classification": amendment.get("classification"),
            "failure": amendment.get("failure"),
            "correction": amendment.get("correction"),
        })
        previous_runner_hash = amendment.get("replacement_audit_script_sha256")
        previous_amendment_hash = amendment_hash
    if previous_runner_hash != audit_script_hash:
        raise RuntimeError("latest R287 amendment does not bind current audit script")
    if audit_script_hash == prereg["audit_script_sha256"]:
        raise RuntimeError("R287 audit script changed after preregistration")
    if sha_file(EVIDENCE / "r287_freeze_inputs.py") != prereg["freeze_script_sha256"]:
        raise RuntimeError("R287 freeze script changed after preregistration")
    panel_hash = sidecar_hash(PANEL)
    panel_proof = json.loads(PANEL.read_text(encoding="utf-8"))
    if not panel_proof.get("registration_pass") or panel_proof.get("candidate", {}).get("status") != "pending":
        raise RuntimeError("R287 pending panel registration/readback is not proven")
    if panel_proof["candidate"].get("source_id") != CANDIDATE_ID:
        raise RuntimeError("R287 panel source id does not match preregistration")
    partitions = check_frozen_inputs(prereg)

    r112 = load_module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r287")
    membership = r112._historical_membership().filter(pl.col("effective_date") == AS_OF)
    expected_symbols = snapshot["membership"]["symbols"]
    if membership.get_column("symbol").sort().to_list() != expected_symbols:
        raise RuntimeError("R287 exact historical membership differs from frozen identities")

    service = r112._service()
    from app.backtest.strategy import StrategyDependencyResolver
    from app.strategy.scoring import scoring_dependencies

    outcomes: dict = {}
    masks_by_id: dict = {}
    for strategy_id in (CONTROL_ID, CANDIDATE_ID):
        strategy = service.strategy_engine.get(strategy_id)
        if strategy.execution_backend != "polars_expr":
            raise RuntimeError(f"unexpected execution backend for {strategy_id}")
        if strategy_id == CANDIDATE_ID and not strategy.meta.get("research_only"):
            raise RuntimeError("R275 candidate is no longer research_only")
        params = service._normalize_params({}, strategy)
        overrides: dict = {}
        basic_filter = service._effective_basic_filter(strategy, overrides)
        feature_plan = StrategyDependencyResolver().resolve(
            strategy,
            params=params,
            basic_filter=basic_filter,
            entry_signals=service._effective_signals(overrides, "entry_signals", strategy.entry_signals),
            exit_signals=service._effective_signals(overrides, "exit_signals", strategy.exit_signals),
            overrides=overrides,
            asset_type="stock",
        )
        resolved_columns = set().union(
            feature_plan.base_columns,
            feature_plan.intermediate_columns,
            feature_plan.indicator_columns,
            feature_plan.signal_columns,
            feature_plan.instrument_columns,
            feature_plan.ext_columns,
            feature_plan.fundamental_columns,
        )
        expected_dependencies = {"ma60"} | scoring_dependencies({name: 1.0 for name in SCORE_NAMES})
        missing_dependencies = expected_dependencies - resolved_columns
        if missing_dependencies:
            raise RuntimeError(f"system feature plan omits resolved scoring dependencies for {strategy_id}: {sorted(missing_dependencies)}")
        full_panel = service.engine.load_panel_for_backtest(
            expected_symbols,
            date.fromisoformat(prereg["system_path"]["load_window"][0]),
            AS_OF,
            feature_plan,
            asset_type="stock",
        )
        day_panel = full_panel.filter(pl.col("date").cast(pl.Date) == AS_OF).sort("symbol")
        if day_panel.is_empty() or not {"symbol", "date", *expected_dependencies}.issubset(day_panel.columns):
            raise RuntimeError(f"system panel omitted required rows/fields for {strategy_id}")
        if day_panel.get_column("symbol").n_unique() != day_panel.height:
            raise RuntimeError(f"duplicate AS_OF symbol rows in system panel for {strategy_id}")

        basic_expr = service.strategy_engine._basic_filter_expr(day_panel, basic_filter)
        basic = pl.Series("_basic", [True] * day_panel.height, dtype=pl.Boolean)
        if basic_expr is not None:
            basic = day_panel.select(basic_expr.alias("_basic")).get_column("_basic").fill_null(False).cast(pl.Boolean)
        strategy_mask = service._build_candidate_filter_mask(day_panel, strategy, params).fill_null(False).cast(pl.Boolean)
        member = membership_mask(day_panel, membership)
        counts, rows, eligible = mask_counts(day_panel, basic, strategy_mask, member)
        from app.strategy.scoring import materialize_scoring_columns

        scoring_frame = materialize_scoring_columns(day_panel, SCORE_NAMES)
        scored = service._apply_score(day_panel, strategy, overrides, universe_mask=eligible)
        coverage = {}
        for name in SCORE_NAMES:
            coverage[name] = {
                "column_present_after_system_materialization": name in scoring_frame.columns,
                "non_null_member_rows": int(scoring_frame.select(pl.col(name).is_not_null().sum()).item()) if name in scoring_frame.columns else 0,
                "non_null_eligible_rows": int(scoring_frame.filter(eligible).get_column(name).is_not_null().sum()) if name in scoring_frame.columns else 0,
            }
        if "score" not in scored.columns:
            raise RuntimeError(f"system scorer produced no score column for {strategy_id}")
        counts["eligible_with_non_null_system_score"] = int(scored.filter(eligible).get_column("score").is_not_null().sum())
        outcomes[strategy_id] = {
            "basic_filter_config": basic_filter,
            "feature_plan": {
                "base_columns": sorted(feature_plan.base_columns),
                "indicator_columns": sorted(feature_plan.indicator_columns),
                "ext_columns": sorted(feature_plan.ext_columns),
                "fundamental_columns": sorted(feature_plan.fundamental_columns),
                "warmup_bars": feature_plan.warmup_bars,
            },
            "mask_counts": counts,
            "score_coverage": coverage,
            "scoring_weights": strategy.meta.get("scoring", {}),
        }
        masks_by_id[strategy_id] = {"rows": rows, "basic": basic, "strategy": strategy_mask, "member": member}

    control_mask = masks_by_id[CONTROL_ID]["rows"].get_column("_eligible")
    candidate_mask = masks_by_id[CANDIDATE_ID]["rows"].get_column("_eligible")
    control_ids = set(masks_by_id[CONTROL_ID]["rows"].filter(pl.col("_eligible")).get_column("symbol").to_list())
    candidate_ids = set(masks_by_id[CANDIDATE_ID]["rows"].filter(pl.col("_eligible")).get_column("symbol").to_list())
    added = sorted(candidate_ids - control_ids)
    removed = sorted(control_ids - candidate_ids)
    if removed:
        raise RuntimeError("one-predicate ablation did not preserve the control candidate set")
    for key in (CONTROL_ID, CANDIDATE_ID):
        values = outcomes[key]["mask_counts"]
        if values["eligible_rows"] != int((masks_by_id[key]["basic"] & masks_by_id[key]["strategy"] & masks_by_id[key]["member"]).sum()):
            raise RuntimeError(f"mask count identity failed for {key}")
    del control_mask, candidate_mask

    audit = {
        "round": "R287",
        "status": "complete",
        "classification": prereg["classification"],
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "preregistration_sha256": pre_hash,
        "input_snapshot_sha256": snapshot_hash,
        "panel_registration_sha256": panel_hash,
        "runner_amendment_chain_sha256": previous_amendment_hash,
        "runner_amendment_chain": amendment_chain,
        "as_of": AS_OF.isoformat(),
        "system_modules_used": [
            "R112 historical membership loader and service construction",
            "StrategyDependencyResolver.resolve",
            "BacktestEngine.load_panel_for_backtest (enriched, historical ext and PIT financial loading)",
            "StrategyBacktestService._build_candidate_filter_mask",
            "StrategyEngine._basic_filter_expr",
            "StrategyBacktestService._hs300_panel_row_mask",
            "StrategyBacktestService._apply_score and materialize_scoring_columns",
        ],
        "input_partition_inventories": partitions,
        "strategies": outcomes,
        "ablation_difference": {
            "candidate_id": CANDIDATE_ID,
            "control_id": CONTROL_ID,
            "candidate_minus_control_symbol_count": len(added),
            "candidate_minus_control_symbols": added,
            "control_minus_candidate_symbol_count": len(removed),
            "control_minus_candidate_symbols": removed,
        },
        "execution_guards": {
            "StrategyBacktestService.run_called": False,
            "BacktestEngine.run_called": False,
            "matcher_or_simulation_called": False,
            "screener_called": False,
            "provider_called": False,
            "returns_trades_equity_curves_loaded_or_calculated": False,
            "market_or_strategy_cache_written": False,
            "panel_status_changed": False,
        },
        "limitations": prereg["system_path"]["semantics_and_limitations"],
        "conclusion_scope": "one frozen historical session's exact system candidate mask and score coverage only; no alpha, profitability, general gate-policy, matcher-selection, macro PIT-vintage, or production-readiness claim",
    }
    audit_hash = save_once(OUT, (json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    control = outcomes[CONTROL_ID]["mask_counts"]
    candidate = outcomes[CANDIDATE_ID]["mask_counts"]
    report = f"""# R287 系统回测候选掩码与评分装配诊断

## 口径

冻结日期为 {AS_OF.isoformat()}，成分池来自 R78 当日历史名单（300 只）。逐只加载系统回测需要的日线预热、扩展时序字段和 PIT 财务字段；只执行系统基础过滤、策略过滤、历史成分掩码和评分物化，没有调用回测撮合器、筛选器、provider 或未来日期数据。

R275 对照的期限利差门槛是 term_spread_10y_2y >= 0.3；候选只移除这一条。两者保留 ERP、信用利差、MA60、股东字段、评分权重和其余设置。

## 系统候选掩码

| 项目 | R275 对照 | 移除期限门禁候选 |
| --- | ---: | ---: |
| 当日系统面板行 | {control['loaded_as_of_rows']} | {candidate['loaded_as_of_rows']} |
| 历史成分行 | {control['historical_member_rows']} | {candidate['historical_member_rows']} |
| 基础过滤通过 | {control['basic_filter_pass_rows']} | {candidate['basic_filter_pass_rows']} |
| 策略过滤通过 | {control['strategy_filter_pass_rows']} | {candidate['strategy_filter_pass_rows']} |
| 最终系统候选行 | {control['eligible_rows']} | {candidate['eligible_rows']} |

仅移除期限门禁后增加 {len(added)} 个系统候选。候选差异名单和因子覆盖明细保存在 r287_system_mask_audit.json。

## 口径限制

策略配置虽含 exclude_new_days=60，系统 _basic_filter_expr 没有执行该项，本轮按系统实际行为记录。market_cap_min 使用当前 instruments 股本回滤历史；宏观扩展值缺少供应商发布时间和修订 vintage。回测掩码也不等于成交持仓选择，META limit=30 是筛选器排序限制，本轮没有调用筛选器。

本结果只回答这一个冻结日期的系统候选数量和评分装配情况，不能证明被释放行未来盈利、不能据此普遍放宽门禁，也不是 alpha 或生产验证。候选保持 research_only，面板保持 pending，生产保持 pause_production。

审计 SHA-256：{audit_hash}。预注册、输入快照、面板请求意图/响应/读回及执行脚本均另有 SHA-256 sidecar。
"""
    report_hash = save_once(RESULT, report.encode("utf-8"))
    print(json.dumps({
        "status": "complete",
        "as_of": AS_OF.isoformat(),
        "control_eligible": control["eligible_rows"],
        "candidate_eligible": candidate["eligible_rows"],
        "added_candidates": len(added),
        "audit_sha256": audit_hash,
        "result_sha256": report_hash,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
