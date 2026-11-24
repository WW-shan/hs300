#!/usr/bin/env python3
"""R531: build an independent research core from R530 admitted factors."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

RESEARCH_TYPE = "statistical_screen"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r531_factor_library_core_preregistration.json"
)
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R530_RESULT = RESEARCH / "r530_unified_factor_library_mc_result.json"
R530_PREREG = RESEARCH / "r530_unified_factor_library_mc_preregistration.json"
PREREG = RESEARCH / "r531_factor_library_core_preregistration.json"
RESULT = RESEARCH / "r531_factor_library_core_result.json"
MARKDOWN = RESEARCH / "r531_factor_library_core_result.md"
START = date(2022, 5, 5)
TRAIN_END = date(2024, 12, 31)
TEST_START = date(2025, 1, 1)
END = date(2026, 11, 5)
RECENT_START = date(2025, 10, 14)
CORRELATION_THRESHOLD = 0.70
MIN_MONTHS = 12
FINAL_MC = 5000
BLOCK_SIZES = (10, 20, 40)
SEED = 20261125


@dataclass(frozen=True)
class Admitted:
    candidate: Any
    r530_row: dict[str, Any]

    @property
    def family_id(self) -> str:
        return str(self.candidate.family_id)

    @property
    def parameter(self) -> int | float:
        return self.candidate.parameter


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> str:
    side = path.with_name(path.name + ".sha256")
    if not side.is_file():
        raise RuntimeError(f"missing sidecar: {path}")
    parts = side.read_text(encoding="ascii").strip().split()
    if len(parts) != 2 or parts[1] != path.name or parts[0] != sha256(path):
        raise RuntimeError(f"invalid sidecar: {path}")
    return parts[0]


def _load_r530() -> Any:
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    name = "research_r530_unified_factor_library_mc"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_admitted_candidates(root: Path) -> list[Admitted]:
    root = Path(root)
    r530 = _load_r530()
    verify_sidecar(root / R530_PREREG.relative_to(ROOT))
    verify_sidecar(root / R530_RESULT.relative_to(ROOT))
    payload = json.loads((root / R530_RESULT.relative_to(ROOT)).read_text(encoding="utf-8"))
    admitted_ids = {
        row["family_id"]
        for row in payload["metrics"]["candidates"]
        if row["conclusion"] == "admitted_for_research"
    }
    rows = {row["family_id"]: row for row in payload["metrics"]["candidates"]}
    candidates = [
        Admitted(candidate, rows[candidate.family_id])
        for candidate in r530.load_recommended_candidates(root)
        if candidate.family_id in admitted_ids
    ]
    if len(candidates) != 11:
        raise RuntimeError(f"expected 11 R530 admitted candidates, got {len(candidates)}")
    return candidates


def month_end_dates(frame: pl.DataFrame, *, start: date, end: date) -> list[date]:
    dates = (
        frame.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        .select(pl.col("date").cast(pl.Date).alias("date"))
        .unique()
        .sort("date")
        .with_columns([
            pl.col("date").dt.year().alias("year"),
            pl.col("date").dt.month().alias("month"),
        ])
        .group_by(["year", "month"])
        .agg(pl.col("date").max())
        .sort(["year", "month"])["date"]
        .to_list()
    )
    return dates


def _union_find_clusters(nodes: list[str], pairs: list[dict[str, Any]], threshold: float) -> list[list[str]]:
    parent = {node: node for node in nodes}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    for row in pairs:
        if abs(float(row["rho"])) >= threshold:
            union(str(row["left"]), str(row["right"]))
    groups: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        groups[find(node)].append(node)
    return sorted([sorted(group) for group in groups.values()], key=lambda item: item[0])


def cluster_pairs(pairs: list[dict[str, Any]], *, threshold: float) -> list[list[str]]:
    nodes = sorted({str(row["left"]) for row in pairs} | {str(row["right"]) for row in pairs})
    return _union_find_clusters(nodes, pairs, threshold)


def _load_factor_frames(candidates: list[Admitted], context: Any, membership: pl.DataFrame) -> dict[str, pl.DataFrame]:
    r530 = _load_r530()
    frames = {}
    for admitted in candidates:
        frame = r530._factor_frame(admitted.candidate, admitted.candidate.parameter, context)
        frames[admitted.candidate.family_id] = frame
    return frames


def pairwise_factor_correlations(
    frames: dict[str, pl.DataFrame],
    membership: pl.DataFrame,
    dates: list[date],
) -> list[dict[str, Any]]:
    date_set = set(dates)
    member_keys = membership.filter(pl.col("date").is_in(date_set)).select("symbol", "date")
    rows = []
    names = sorted(frames)
    for index, left_name in enumerate(names):
        for right_name in names[index + 1:]:
            left = frames[left_name].rename({"factor_value": "left"}).join(
                member_keys, on=["symbol", "date"], how="inner"
            )
            right = frames[right_name].rename({"factor_value": "right"}).join(
                member_keys, on=["symbol", "date"], how="inner"
            )
            pair = left.join(right, on=["symbol", "date"], how="inner").drop_nulls(
                ["left", "right"]
            )
            if pair.height < len(dates) * 10:
                rows.append({"left": left_name, "right": right_name, "rho": None, "n_dates": 0})
                continue
            ranked = pair.with_columns([
                pl.col("left").rank("average").over("date").alias("_left_rank"),
                pl.col("right").rank("average").over("date").alias("_right_rank"),
            ])
            daily = (
                ranked.group_by("date")
                .agg(pl.corr("_left_rank", "_right_rank").alias("rho"))
                .drop_nulls()
            )
            rho = float(daily["rho"].mean()) if daily.height else None
            rows.append({
                "left": left_name,
                "right": right_name,
                "rho": round(rho, 6) if rho is not None else None,
                "n_dates": daily.height,
            })
    return rows


def choose_representatives(
    clusters: list[list[str]], rows: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    result = []
    for cluster in clusters:
        candidates = [
            (family_id, float(rows[family_id]["train"]["directional_ic"]))
            for family_id in cluster
            if isinstance(rows[family_id].get("train", {}).get("directional_ic"), (int, float))
        ]
        if not candidates:
            continue
        family_id, train_ic = max(candidates, key=lambda item: (item[1], -len(item[0]), item[0]))
        result.append({
            "family_id": family_id,
            "cluster": cluster,
            "cluster_size": len(cluster),
            "selection_basis": "highest R530 training directional IC; validation/recent not used",
            "training_directional_ic": round(train_ic, 6),
        })
    return sorted(result, key=lambda row: row["family_id"])


def _core_frame(
    representatives: list[dict[str, Any]],
    candidates: dict[str, Admitted],
    frames: dict[str, pl.DataFrame],
    membership: pl.DataFrame,
) -> pl.DataFrame:
    parts = []
    for representative in representatives:
        family_id = representative["family_id"]
        candidate = candidates[family_id].candidate
        factor = frames[family_id]
        joined = factor.join(membership, on=["symbol", "date"], how="inner")
        denominator = pl.col("factor_value").is_not_null().sum().over("date")
        expression = (
            pl.col("factor_value").rank("average").over("date") / denominator
        )
        if candidate.family["direction"] == "low":
            expression = 1.0 - expression
        parts.append(
            joined.select(
                "symbol",
                "date",
                expression.alias("rank_value"),
            ).with_columns(pl.lit(family_id).alias("family_id"))
        )
    combined = (
        pl.concat(parts, how="vertical")
        .drop_nulls("rank_value")
        .group_by(["symbol", "date"])
        .agg(
            pl.col("rank_value").mean().alias("factor_value"),
            pl.len().alias("n_factors"),
        )
        .sort(["date", "symbol"])
    )
    return combined


def _core_ic(
    core: pl.DataFrame,
    context: Any,
    membership: pl.DataFrame,
) -> tuple[dict[str, Any], pl.DataFrame]:
    base = context.prices.select(
        "symbol", "date", "forward_return_5d", "industry_l1", "log_float_mv"
    )
    joined = (
        base.join(membership, on=["symbol", "date"], how="inner")
        .join(core.select("symbol", "date", "factor_value"), on=["symbol", "date"], how="inner")
        .filter(
            pl.col("forward_return_5d").is_not_null()
            & (pl.col("date") >= START)
            & (pl.col("date") <= END)
        )
        .sort(["date", "symbol"])
    )
    p3 = context.p3
    raw_ic = p3.calculate_rank_ic(
        joined, "factor_value", "forward_return_5d", minimum=30
    )
    industry_ic = p3._neutralized_ic(joined, ["industry"], minimum_cross_section=30)
    size_ic = p3._neutralized_ic(joined, ["size"], minimum_cross_section=30)
    combined_ic = p3._neutralized_ic(joined, ["industry", "size"], minimum_cross_section=30)
    gate_ic = p3._gate_ic(raw_ic, industry_ic, size_ic, combined_ic, ["industry", "size"])
    block = p3._ic_block(gate_ic, START, END, 1)
    return {
        "coverage": p3._coverage(joined),
        "full": block["full"],
        "train": block["train"],
        "test": block["test"],
        "recent": block["recent"],
        "industry_neutral": p3._ic_block(industry_ic, START, END, 1),
        "size_neutral": p3._ic_block(size_ic, START, END, 1),
        "industry_size_neutral": p3._ic_block(combined_ic, START, END, 1),
    }, gate_ic


def _bootstrap_core(gate_ic: pl.DataFrame, block_size: int, p3: Any) -> dict[str, Any]:
    token = hashlib.sha256(
        f"{SEED}:library_core:{block_size}:{FINAL_MC}".encode()
    ).hexdigest()
    return p3._bootstrap_ic_or_empty(
        gate_ic,
        START,
        END,
        1,
        block_size=block_size,
        n_bootstrap=FINAL_MC,
        seed=int(token[:8], 16),
    )


def run_study(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    r530 = _load_r530()
    candidates = load_admitted_candidates(root)
    membership = r530.load_pit_membership(root)
    context = r530._load_context(root, membership)
    frames = _load_factor_frames(candidates, context, membership)
    training_dates = month_end_dates(membership, start=START, end=TRAIN_END)
    pairs = pairwise_factor_correlations(frames, membership, training_dates)
    usable_pairs = [row for row in pairs if row.get("rho") is not None]
    clusters = _union_find_clusters(sorted(frames), usable_pairs, CORRELATION_THRESHOLD)
    candidate_rows = {row.candidate.family_id: row.r530_row for row in candidates}
    representatives = choose_representatives(clusters, candidate_rows)
    core = _core_frame(representatives, {row.candidate.family_id: row for row in candidates}, frames, membership)
    core_stats, gate_ic = _core_ic(core, context, membership)
    bootstrap = {str(block): _bootstrap_core(gate_ic, block, context.p3) for block in BLOCK_SIZES}
    coverage = core_stats["coverage"]
    full = core_stats["full"]
    train = core_stats["train"]
    test = core_stats["test"]
    recent = core_stats["recent"]
    gates = {
        "at_least_two_clusters": len(clusters) >= 2,
        "representatives_selected": len(representatives) == len(clusters) and bool(representatives),
        "coverage_ge_0_90": coverage.get("factor_non_null_ratio", 0) >= 0.90,
        "full_ic_ge_0_01": full.get("directional_ic") is not None and full["directional_ic"] >= 0.01,
        "train_ic_ge_0_01": train.get("directional_ic") is not None and train["directional_ic"] >= 0.01,
        "validation_ic_positive": test.get("directional_ic") is not None and test["directional_ic"] > 0,
        "recent_ic_positive": recent.get("directional_ic") is not None and recent["directional_ic"] > 0,
        "bootstrap_10_20_40_ci_positive": all(
            item.get("ci_low") is not None and item["ci_low"] > 0 for item in bootstrap.values()
        ),
    }
    if not gates["at_least_two_clusters"] or not gates["representatives_selected"]:
        conclusion = "rejected"
    elif all(gates.values()):
        conclusion = "admitted_for_research"
    elif not gates["full_ic_ge_0_01"] or not gates["validation_ic_positive"]:
        conclusion = "rejected"
    else:
        conclusion = "inconclusive"
    payload = {
        "schema_version": 1,
        "research_id": "R531",
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_r530": R530_RESULT.relative_to(ROOT).as_posix(),
        "admitted_candidates": [row.candidate.family_id for row in candidates],
        "training_month_end_count": len(training_dates),
        "correlation_threshold": CORRELATION_THRESHOLD,
        "correlation_pairs": pairs,
        "clusters": clusters,
        "representatives": representatives,
        "core": {"stats": core_stats, "bootstrap": bootstrap},
        "core_gates": gates,
        "conclusion": conclusion,
        "known_limits": [
            "Clusters are selected only from training-period month-end factor ranks.",
            "The core is a research factor rank, not a portfolio allocation or return stream.",
            "Correlation clustering is threshold-sensitive; 0.70 was frozen before validation results.",
            "R526 membership and industry vintages are reconstructed research inputs.",
            "No leverage, orders, paper targets or active version are produced.",
        ],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }
    return payload, context


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# R531 因子库核心独立性与综合能力",
        "",
        "## 输入",
        "",
        f"- R530 准入候选：{len(payload['admitted_candidates'])}",
        f"- 训练期月末相关截面：{payload['training_month_end_count']}",
        f"- 相关性阈值：{payload['correlation_threshold']}",
        "",
        "## 独立性与代表因子",
        "",
        "| 聚类 | 代表因子 | 训练期 IC | 聚类大小 |",
        "| --- | --- | ---: | ---: |",
    ]
    for row in payload["representatives"]:
        lines.append(
            f"| {', '.join(row['cluster'])} | {row['family_id']} | "
            f"{row['training_directional_ic']} | {row['cluster_size']} |"
        )
    lines += [
        "",
        "## 核心 rank 因子",
        "",
        f"- full IC：{payload['core']['stats']['full']['directional_ic']}",
        f"- validation IC：{payload['core']['stats']['test']['directional_ic']}",
        f"- recent IC：{payload['core']['stats']['recent']['directional_ic']}",
        f"- coverage：{payload['core']['stats']['coverage']['factor_non_null_ratio']}",
        "- bootstrap 10/20/40 CI low："
        + "/".join(
            str(payload["core"]["bootstrap"][str(block)].get("ci_low"))
            for block in BLOCK_SIZES
        ),
        "",
        "## 结论",
        "",
        f"- 总体结论：{payload['conclusion']}",
        f"- 门槛：{payload['core_gates']}",
        "",
        "## 边界",
        "",
        *[f"- {item}" for item in payload["known_limits"]],
    ]
    return "\n".join(lines) + "\n"


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R531 preregistration already exists: {PREREG}")
    candidates = load_admitted_candidates(root)
    r530 = _load_r530()
    bundle = root / r530.PIT_BUNDLE.relative_to(ROOT) / "daily_snapshots.parquet"
    industry = root / r530.PIT_BUNDLE.relative_to(ROOT) / "industry_history.parquet"
    payload = {
        "schema_version": 1,
        "research_id": "R531",
        "research_type": RESEARCH_TYPE,
        "question": "After removing highly correlated R530 admitted factors, does a training-only factor core retain predictive breadth?",
        "hypothesis": "A small set of independent factor representatives preserves positive validation and recent IC.",
        "study_scope": "research_only",
        "runner": Path(__file__).resolve().relative_to(root).as_posix(),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project_data_with_r526_pit_membership",
        "input_hashes": {
            "r530_result": sha256(root / R530_RESULT.relative_to(ROOT)),
            "r530_preregistration": sha256(root / R530_PREREG.relative_to(ROOT)),
            "r526_membership": sha256(bundle),
            "r526_industry": sha256(industry),
        },
        "parameters": {
            "admitted_candidates": [
                {"family_id": row.candidate.family_id, "parameter": row.candidate.parameter}
                for row in candidates
            ],
            "correlation_threshold": CORRELATION_THRESHOLD,
            "representative_rule": "highest R530 training directional IC within each training-only cluster",
            "final_mc": FINAL_MC,
            "block_sizes": list(BLOCK_SIZES),
            "seed": SEED,
        },
        "sample": {
            "start": str(START),
            "end": str(END),
            "train_end": str(TRAIN_END),
            "test_start": str(TEST_START),
            "recent_start": str(RECENT_START),
            "training_month_ends_min": MIN_MONTHS,
        },
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "gates": {
            "at_least_two_clusters": True,
            "coverage_min": 0.90,
            "full_ic_min": 0.01,
            "train_ic_min": 0.01,
            "validation_ic_positive": True,
            "recent_ic_positive": True,
            "bootstrap_ci_low_positive": True,
        },
        "falsification_checks": [
            "training_only_correlation_clustering",
            "validation_and_recent_not_used_for_representatives",
            "fixed_seed_block_bootstrap_10_20_40",
        ],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    PREREG.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    PREREG.with_name(PREREG.name + ".sha256").write_text(
        f"{sha256(PREREG)}  {PREREG.name}\n", encoding="ascii"
    )
    return PREREG


def publish_result(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    from app.factor_research.research_contract import (
        ResearchResultEnvelope,
        publish_research_artifacts,
    )

    root = Path(root)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError(f"R531 result already exists: {RESULT}")
    prereg_hash = verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    result = ResearchResultEnvelope.model_validate({
        "schema_version": 1,
        "research_id": prereg["research_id"],
        "research_type": RESEARCH_TYPE,
        "runner": prereg["runner"],
        "runner_sha256": prereg["runner_sha256"],
        "preregistration_sha256": prereg_hash,
        "generated_at": payload["generated_at"],
        "conclusion": payload["conclusion"],
        "gate_results": payload["core_gates"],
        "metrics": {
            "source_r530": payload["source_r530"],
            "admitted_candidates": payload["admitted_candidates"],
            "training_month_end_count": payload["training_month_end_count"],
            "correlation_pairs": payload["correlation_pairs"],
            "clusters": payload["clusters"],
            "representatives": payload["representatives"],
            "core": payload["core"],
            "core_gates": payload["core_gates"],
        },
        "source_result_hashes": [sha256(R530_RESULT)],
        "known_limits": payload["known_limits"],
        "negative_knowledge": [] if payload["conclusion"] == "admitted_for_research" else [payload["conclusion"]],
        "output_policy": payload["output_policy"],
    })
    published = publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=result,
        markdown=render_markdown(payload),
        operation="r531_factor_library_core_published",
    )
    return published.__dict__


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--freeze-preregistration", action="store_true")
    args = parser.parse_args()
    if args.freeze_preregistration:
        path = freeze_preregistration(ROOT)
        print(json.dumps({"preregistration": path.relative_to(ROOT).as_posix()}))
        return 0
    if args.preflight:
        print(json.dumps({
            "admitted_candidates": [row.candidate.family_id for row in load_admitted_candidates(ROOT)],
            "correlation_threshold": CORRELATION_THRESHOLD,
        }, ensure_ascii=False, indent=2))
        return 0
    if not PREREG.exists():
        raise FileNotFoundError("R531 preregistration is missing; run --freeze-preregistration")
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R531 results already exist; refusing overwrite")
    payload, _context = run_study(ROOT)
    published = publish_result(ROOT, payload)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "core_gates": payload["core_gates"],
        "published_result": str(published.get("result_path")),
        "ledger_record_id": published.get("ledger_record_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
