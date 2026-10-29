from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
START, END = date(2022, 5, 5), date(2023, 4, 28)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    digest = hashlib.sha256(payload).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return digest


def load_script(root: Path, relative: str, name: str):
    spec = importlib.util.spec_from_file_location(name, root / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> int:
    output = EVIDENCE / "r298_temporal_holdout_backtest.json"
    report = EVIDENCE / "r298_result.md"
    if not (EVIDENCE / "r298_panel_registration.json").is_file():
        raise RuntimeError("panel registration and readback proof is required")
    if output.exists() or output.with_name(output.name + ".sha256").exists() or report.exists():
        raise FileExistsError("R298 result exists; refusing overwrite or rerun")

    sys.path.insert(0, str(ROOT / "backend"))
    import polars as pl

    prereg_path = EVIDENCE / "r298_preregistration.json"
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    prereg_hash = sha256_file(prereg_path)
    sidecar_hash = prereg_path.with_name(prereg_path.name + ".sha256").read_text(encoding="ascii").split()[0]
    if prereg_hash != sidecar_hash:
        raise RuntimeError("preregistration checksum mismatch")
    manifest_path = EVIDENCE / "r298_input_manifest.jsonl"
    manifest_hash = sha256_file(manifest_path)
    if manifest_hash != prereg.get("input_manifest_sha256"):
        raise RuntimeError("input manifest checksum mismatch")
    manifest = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest_addendum_path = EVIDENCE / "r298_manifest_execution_addendum.json"
    manifest_addendum = json.loads(manifest_addendum_path.read_text(encoding="utf-8")) if manifest_addendum_path.exists() else None
    allowed_changes = {
        item["path"]: item for item in (manifest_addendum or {}).get("execution_script_changes", [])
    }
    if manifest_addendum is not None:
        addendum_hash = sha256_file(manifest_addendum_path)
        addendum_sidecar = manifest_addendum_path.with_name(manifest_addendum_path.name + ".sha256").read_text(encoding="ascii").split()[0]
        if addendum_hash != addendum_sidecar or manifest_addendum.get("parent_manifest_sha256") != manifest_hash:
            raise RuntimeError("execution addendum checksum or parent-manifest mismatch")
    for item in manifest:
        path = ROOT / item["path"]
        stat = path.stat()
        actual_hash = sha256_file(path)
        if stat.st_size != item["bytes"] or stat.st_mtime_ns != item["mtime_ns"] or actual_hash != item["sha256"]:
            amended = allowed_changes.get(item["path"])
            if (
                amended is None
                or amended.get("original_sha256") != item["sha256"]
                or amended.get("current_sha256") != actual_hash
                or amended.get("current_bytes") != stat.st_size
                or amended.get("current_mtime_ns") != stat.st_mtime_ns
                or amended.get("change_class") != "execution_harness_integrity_only"
            ):
                raise RuntimeError(f"frozen input changed without an execution-only amendment: {item['path']}")

    r112 = load_script(ROOT, "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r298")
    r221 = load_script(ROOT, "scripts/backtest_r221_sga_nonfin_quality.py", "r221_r298")
    r229 = load_script(ROOT, "scripts/backtest_r229_ocf_margin.py", "r229_r298")
    r239 = load_script(ROOT, "scripts/backtest_r239_concentration_audit.py", "r239_r298")
    r253 = load_script(ROOT, "scripts/backtest_r253_min_rank.py", "r253_r298")
    r257 = load_script(ROOT, "scripts/backtest_r257_quality_relative_strength.py", "r257_r298")

    calendar = r229._benchmark_dates(START, END)
    expected_window = prereg["window"]
    if len(calendar) != expected_window["trading_days"] or str(calendar[0]) != expected_window["observed_first_last"][0] or str(calendar[-1]) != expected_window["observed_first_last"][1]:
        raise RuntimeError("frozen trading calendar changed")
    members = r221._nonfin_membership(r221._membership_with_industry())
    factor = r257._factor_rows().filter((pl.col("date") >= START) & (pl.col("date") <= END))
    eligible = r257._member_daily(calendar, members).join(factor, on=["date", "symbol"], how="left")
    valid = eligible.filter(pl.col("quality_relative_strength_min_rank").is_not_null())
    coverage = valid.height / eligible.height if eligible.height else 0.0
    factor_days = valid["date"].n_unique()
    if coverage < 0.99 or factor_days / len(calendar) < 0.95:
        raise RuntimeError("pre-registered coverage gate failed; backtest not run")

    cache = ROOT / "data" / "research" / "r298_cache"
    if cache.exists():
        raise FileExistsError("R298 cache directory already exists")
    r253.CACHE = cache
    service = r112._service()
    specs = [
        ("candidate_3x", "ai_hs300_quality_relative_strength_h20_sl20", 3.0),
        ("control_3x", "ai_hs300_quality_relative_strength_control", 3.0),
        ("candidate_4x", "ai_hs300_quality_relative_strength_h20_sl20", 4.0),
        ("candidate_5x", "ai_hs300_quality_relative_strength_h20_sl20", 5.0),
    ]
    curves, runs, trades = {}, {}, {}
    for label, strategy_id, cost in specs:
        print(f"Starting {label} at {cost:g}x cost", flush=True)
        curve, info, trade_rows = r253._run_segment(service, members, r112, r229, strategy_id, START, END, cost)
        curves[label], runs[label], trades[label] = curve, info, trade_rows
        print(f"Finished {label}: {info.get('curve_stats')}", flush=True)

    from app.services.industry_map import load_industry_l1

    industry_frame = load_industry_l1(ROOT / "data")
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}
    concentration = r239._aggregate(trades["candidate_3x"], industry)
    candidate = r112._stats(curves["candidate_3x"])
    control = r112._stats(curves["control_3x"])
    stress = {key: r112._stats(curves[f"candidate_{key}"]) for key in ("4x", "5x")}
    decision = {
        "F1": coverage >= 0.99 and factor_days / len(calendar) >= 0.95,
        "G1": candidate["total_return"] > 0,
        "G2": candidate["total_return"] >= control["total_return"] + 0.05,
        "G3": candidate["mdd"] >= -0.40,
        "G4": stress["4x"]["total_return"] > 0 and stress["5x"]["total_return"] > 0,
        "C1": len(trades["candidate_3x"]) >= 30 and concentration["distinct_symbols"] >= 20,
        "C2": concentration["total_pnl_amount"] > 0 and concentration["top1_net_share"] <= 0.25,
        "C3": concentration["total_pnl_amount"] > 0 and concentration["top3_net_share"] <= 0.55,
        "C4": concentration["total_pnl_amount"] > 0 and concentration["top5_net_share"] <= 0.75,
    }
    decision["all_pass"] = all(decision.values())
    audit = {
        "round": "R298",
        "classification": "system_engine_pre_discovery_temporal_holdout_supplement",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "preregistration_sha256": prereg_hash,
        "input_manifest_sha256": manifest_hash,
        "input_manifest_files_verified_before_run": len(manifest),
        "manifest_execution_addendum_sha256": sha256_file(manifest_addendum_path) if manifest_addendum_path.exists() else None,
        "manifest_execution_addendum_files": sorted(allowed_changes),
        "window": [str(START), str(END)],
        "calendar_days": len(calendar),
        "universe": {
            "historical_nonfinancial_member_rows": eligible.height,
            "valid_factor_rows": valid.height,
            "factor_coverage": round(coverage, 8),
            "valid_factor_days": factor_days,
            "calendar_days_with_factor": round(factor_days / len(calendar), 8),
            "membership_snapshots": members["effective_date"].n_unique(),
            "industry_source": "current industry snapshot; not historical PIT",
        },
        "factor": {"id": "ext_alt_hs300_qrs_quality_relative_strength_min_rank", "existing_values": True, "builder_invoked": False},
        "execution": {
            "engine": "StrategyBacktestService via existing R253 system segment runner",
            "matching": "open_t+1", "score_limit": 30, "max_positions": 10,
            "position_sizing": "equal", "holding_days": 20, "stop_loss": -0.20,
            "cost_3x": {"fees_pct": 0.0006, "commission_pct": 0.0006, "stamp_tax_pct": 0.001, "slippage_bps": 15.0},
            "runs": runs, "candidate_3x": candidate, "control_3x": control, "candidate_stress": stress,
        },
        "concentration": {key: value for key, value in concentration.items() if key != "per_symbol"},
        "per_symbol": concentration["per_symbol"],
        "pre_registered_gates": prereg["decision_gates"],
        "decision": decision,
        "limitations": prereg["universe_and_data"]["limitations"],
        "side_effects": {
            "formal_market_or_factor_data_written": False, "provider_calls": False,
            "strategy_or_backtest_api_write": False, "paper_or_order_operations": False,
            "research_cache": str(cache.relative_to(ROOT)), "cache_is_new_and_round_scoped": True,
        },
    }
    write_once(output, (json.dumps(audit, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8"))
    lines = [
        "# R298：R257 发现期前一年补充回测", "",
        "## 结论", "",
        "预注册判定：{}。这是 R257 原发现期之前的历史时间外补充，不是完全独立研究，也不构成部署或纸面交易批准。".format("全部通过" if decision["all_pass"] else "未全部通过"), "",
        "## 结果", "",
        "- 区间：{} 至 {}，{} 个系统交易日。".format(START, END, len(calendar)),
        "- 因子覆盖：{:,}/{:,} 个历史非金融成分股日（{:.3%}），{}/{} 个交易日有有效值。".format(valid.height, eligible.height, coverage, factor_days, len(calendar)),
        "- 候选 3x：收益 {:.2%}、Sharpe {:.4f}、最大回撤 {:.2%}；匹配对照 3x：收益 {:.2%}、Sharpe {:.4f}、最大回撤 {:.2%}。".format(candidate["total_return"], candidate["sharpe"], candidate["mdd"], control["total_return"], control["sharpe"], control["mdd"]),
        "- 候选成本压力：4x 收益 {:.2%}；5x 收益 {:.2%}。".format(stress["4x"]["total_return"], stress["5x"]["total_return"]),
        "- 交易 {} 笔、{} 只股票；头部 1/3/5 只净 P&L 份额 {:.2%}/{:.2%}/{:.2%}。".format(concentration["n_trades"], concentration["distinct_symbols"], concentration["top1_net_share"], concentration["top3_net_share"], concentration["top5_net_share"]),
        "", "## 门槛", "",
    ]
    for key, passed in decision.items():
        if key != "all_pass":
            lines.append("- {}：{}。".format(key, "通过" if passed else "失败"))
    lines.extend([
        "", "## 局限与副作用", "",
        "R257 因子及相关家族、底层数据和策略曾在其他轮次研究；当前行业分类、当前股本与可能修订的财务数据不是完整历史 PIT。早于 R257 预注册发现区间不代表未触碰的独立样本。回测缓存只写入新的 data/research/r298_cache；正式行情、因子、策略、provider、模拟账户和订单未触碰。", "",
        "R257 继续 research-only，生产继续 pause_production。完整机器结果见 evidence/r298_temporal_holdout_backtest.json。", "",
    ])
    write_once(report, "\n".join(lines).encode("utf-8"))
    print(json.dumps({"decision": decision, "candidate": candidate, "control": control, "stress": stress, "concentration": audit["concentration"], "result": str(output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
