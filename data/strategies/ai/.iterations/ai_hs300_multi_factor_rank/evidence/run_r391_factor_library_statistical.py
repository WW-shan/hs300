"""R391: statistical incremental screening of R390 candidates.

This is a statistical-information stage only. It does not run portfolio
backtests, generate orders, or promote any factor to active status.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
DATA = ROOT / "data"
EVIDENCE = Path(__file__).resolve().parent
PREREG = EVIDENCE / "r391_factor_library_statistical_preregistration.json"
R390 = EVIDENCE / "r390_factor_library_screening.json"
OUTPUT = EVIDENCE / "r391_factor_library_statistical.json"
RESULT = EVIDENCE / "r391_factor_library_statistical_result.md"
START = date(2026 - 4, 5, 5)  # replaced below
START = date(2022, 5, 5)
END = date(2026, 11, 5)
RECENT_START = date(2025, 10, 14)
HORIZON = 5
sys.path.insert(0, str(ROOT / "backend"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists():
        raise RuntimeError(f"missing checksum sidecar: {sidecar.name}")
    if sha256(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def load_r390() -> dict[str, Any]:
    verify_sidecar(R390)
    return json.loads(R390.read_text(encoding="utf-8"))


def candidate_ids() -> list[str]:
    return list(load_r390()["candidate_audit"]["eligible_factor_ids"])


def core_members() -> list[dict[str, Any]]:
    head = json.loads((DATA / "factor_research/sets/core_v1/head.json").read_text(encoding="utf-8"))
    version = json.loads(
        (DATA / "factor_research/sets/core_v1/versions" / f"v{head['watch_version']:04d}.json").read_text(encoding="utf-8")
    )
    return [
        row for row in version["members"]
        if row["role"] == "alpha" and row["membership_status"] == "watch"
    ]


def core_ids() -> list[str]:
    return [row["factor_id"] for row in core_members()]


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R391 output exists; refusing overwrite: {path.name}")


def _rank_score(factor_id: str, direction: str) -> pl.Expr:
    rank = pl.col(factor_id).rank(method="average").over("date")
    centered = rank - rank.mean().over("date")
    std = rank.std().over("date")
    score = pl.when(std > 1e-12).then(centered / std).otherwise(None)
    return (-score if direction == "low" else score).alias(f"_score_{factor_id}")


def _load_panel() -> tuple[pl.DataFrame, dict[str, str], dict[str, str]]:
    from app.backtest.engine import BacktestEngine
    from app.backtest.factor import FactorBacktestService, FactorBatchConfig
    from app.factor_research.catalog import load_latest_catalog
    from app.factors.store import load_into_registry
    from app.hs300.current import current_symbols
    from app.tickflow.repository import DataStore, KlineRepository

    catalog = load_latest_catalog(DATA)
    if catalog is None:
        raise RuntimeError("factor catalog missing")
    load_into_registry(DATA)
    core = core_members()
    ids = list(dict.fromkeys([row["factor_id"] for row in core] + candidate_ids()))
    symbols = current_symbols()
    service = FactorBacktestService(BacktestEngine(KlineRepository(DataStore(DATA))))
    config = FactorBatchConfig(
        factor_names=ids,
        symbols=symbols,
        start=START,
        end=END,
        n_groups=5,
        rebalance="daily",
        weight="equal",
        fees_pct=0.0004,
        commission_pct=0.0004,
        stamp_tax_pct=0.0005,
        slippage_bps=10.0,
        asset_type="stock",
        hs300=False,
    )
    generation = service._data_generation(config.asset_type)
    panel = service._load_factor_panel(config, ids, expected_generation=generation)
    trading_dates = service._global_trading_dates(config)
    panel = service._attach_shared_next_return(panel, config, trading_dates=trading_dates)
    panel = panel.filter(
        (pl.col("date") >= START)
        & (pl.col("date") <= END)
        & pl.col("_forward_return_5d").is_not_null()
    )
    directions = {item.factor_id: item.direction for item in catalog.factors}
    score_columns = {}
    exprs = []
    for factor_id in ids:
        if factor_id not in panel.columns:
            continue
        exprs.append(_rank_score(factor_id, directions[factor_id]))
        score_columns[factor_id] = f"_score_{factor_id}"
    if exprs:
        panel = panel.with_columns(exprs)
    weights = {row["factor_id"]: float(row["weight"] or 0.0) for row in core}
    numerator = pl.lit(0.0)
    denominator = pl.lit(0.0)
    for factor_id in core:
        fid = factor_id["factor_id"]
        if fid not in score_columns:
            continue
        score = score_columns[fid]
        weight = weights[fid]
        numerator = numerator + pl.col(score).fill_null(0.0) * weight
        denominator = denominator + pl.when(pl.col(score).is_not_null()).then(weight).otherwise(0.0)
    panel = panel.with_columns(
        pl.when(denominator >= 0.5).then(numerator / denominator).otherwise(None).alias("_core_composite")
    )
    return panel, score_columns, weights


def _incremental(base_ic: pl.DataFrame, combined_ic: pl.DataFrame, direction: str) -> dict[str, Any]:
    from app.backtest import stats_v2

    sign = -1 if direction == "low" else 1
    paired = (
        base_ic.select(["date", pl.col("ic").alias("base_ic")])
        .join(combined_ic.select(["date", pl.col("ic").alias("combined_ic")]), on="date", how="inner")
        .sort("date")
    )
    delta = (paired["combined_ic"] - paired["base_ic"]).to_numpy() * sign
    nw = stats_v2.newey_west_t(delta, lag=HORIZON)
    return {
        "n_dates": int(delta.size),
        "base_ic_mean": round(float(paired["base_ic"].mean()), 6) if paired.height else None,
        "combined_ic_mean": round(float(paired["combined_ic"].mean()), 6) if paired.height else None,
        "directional_delta_mean": round(float(delta.mean()), 6) if delta.size else None,
        "delta_nw_t": round(float(nw[0]), 6) if nw is not None else None,
        "delta_p_value": round(float(stats_v2.normal_two_sided_p(nw[0])), 8) if nw is not None else None,
    }


def build_report() -> dict[str, Any]:
    verify_sidecar(PREREG)
    from app.backtest import stats_v2
    from app.factor_research.signal_scan import calculate_rank_ic

    panel, scores, _weights = _load_panel()
    rows: dict[str, dict[str, Any]] = {}
    for factor_id in candidate_ids():
        score = scores.get(factor_id)
        if score is None or score not in panel.columns:
            rows[factor_id] = {"status": "data_unavailable", "reason": "missing panel column"}
            continue
        working = panel.select(["date", "symbol", "_forward_return_5d", "_core_composite", score]).filter(
            pl.col("_core_composite").is_not_null() & pl.col(score).is_not_null()
        ).with_columns(
            (0.8 * pl.col("_core_composite") + 0.2 * pl.col(score)).alias("_combined")
        )
        base_ic = calculate_rank_ic(working, "_core_composite", "_forward_return_5d", minimum=30)
        combined_ic = calculate_rank_ic(working, "_combined", "_forward_return_5d", minimum=30)
        record = _incremental(base_ic, combined_ic, "high")
        corr = panel.select(["_core_composite", score]).drop_nulls().select(
            pl.corr(pl.col("_core_composite"), pl.col(score), method="spearman")
        ).item() if working.height >= 30 else None
        record["factor_id"] = factor_id
        record["mean_abs_correlation"] = abs(float(corr)) if corr is not None else None
        record["q_value"] = None
        rows[factor_id] = record
    p_values = [row.get("delta_p_value") for row in rows.values()]
    q_values = stats_v2.bh_fdr_qvalues([value if value is not None else 1.0 for value in p_values])
    for row, q_value in zip(rows.values(), q_values, strict=True):
        row["q_value"] = round(float(q_value), 8) if q_value is not None else None
        if row.get("status") == "data_unavailable":
            continue
        delta = row.get("directional_delta_mean")
        p_value = row.get("delta_p_value")
        corr = row.get("mean_abs_correlation")
        if corr is not None and corr >= 0.90:
            row["status"] = "redundant"
        elif delta is not None and delta > 0 and p_value is not None and p_value <= 0.10 and row["q_value"] <= 0.10:
            row["status"] = "statistical_pass"
        else:
            row["status"] = "statistical_rejected"
    counts = {status: sum(1 for row in rows.values() if row.get("status") == status) for status in ("statistical_pass", "redundant", "statistical_rejected", "data_unavailable")}
    return {
        "round": "R391",
        "classification": "statistical screening only; no portfolio promotion",
        "runner_sha256": sha256(Path(__file__)),
        "preregistration_sha256": sha256(PREREG),
        "r390_sha256": sha256(R390),
        "candidate_count": len(candidate_ids()),
        "counts": counts,
        "candidates": rows,
        "no_leverage": True,
        "decision": {
            "status": "statistical_screening_complete_no_promotion",
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }


def markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    return "\n".join([
        "# R391 · 因子库统计增量筛查",
        "",
        "R391 只计算候选与 core_v1 的相关性和 80/20 边际 IC，不做组合回测。",
        "",
        f"- 候选数：{report['candidate_count']}",
        f"- statistical_pass：{counts['statistical_pass']}",
        f"- redundant：{counts['redundant']}",
        f"- statistical_rejected：{counts['statistical_rejected']}",
        f"- data_unavailable：{counts['data_unavailable']}",
        "",
        "## 结论边界",
        "",
        "- statistical_pass 只代表可以进入下一阶段组合 MC，不代表策略有效。",
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
    print(json.dumps({"candidate_count": report["candidate_count"], "counts": report["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
