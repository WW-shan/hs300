#!/usr/bin/env python3
"""R492: paired absolute versus HS300-excess moving-block bootstrap."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r492_paired_excess_bootstrap_preregistration.json"
R491 = E / "r491_weight_quality_audit_2026-11-22.json"
OUTPUT = E / "r492_paired_excess_bootstrap_2026-11-22.json"
RESULT = E / "r492_paired_excess_bootstrap_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R492 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def pair_returns(
    strategy: dict[str, float], benchmark: dict[str, float]
) -> list[tuple[str, float, float]]:
    missing = sorted(set(strategy) - set(benchmark))
    missing_ratio = len(missing) / len(strategy) if strategy else 0.0
    if missing_ratio >= 0.01:
        raise ValueError(f"benchmark dates missing at or above 1%: {len(missing)}/{len(strategy)}")
    return [
        (day, float(strategy[day]), float(benchmark[day]))
        for day in sorted(strategy)
        if day in benchmark
    ]


def block_bootstrap(values: list[float], *, block: int, paths: int, seed: int) -> dict[str, float]:
    rng = random.Random(seed)
    totals, sharpes, mdds = [], [], []
    for _ in range(paths):
        sampled: list[float] = []
        while len(sampled) < len(values):
            start = rng.randrange(len(values))
            sampled.extend(values[(start + offset) % len(values)] for offset in range(block))
        selected = sampled[: len(values)]
        product = math.prod(1.0 + item for item in selected)
        mean = sum(selected) / len(selected)
        vol = (sum((item - mean) ** 2 for item in selected) / len(selected)) ** 0.5
        path = [1.0]
        for item in selected:
            path.append(path[-1] * (1.0 + item))
        peak = path[0]
        mdd = 0.0
        for item in path:
            peak = max(peak, item)
            mdd = min(mdd, item / peak - 1.0)
        totals.append(product - 1.0)
        sharpes.append(mean / vol * math.sqrt(252.0) if vol else 0.0)
        mdds.append(mdd)

    def q(items: list[float], fraction: float) -> float:
        return sorted(items)[min(len(items) - 1, int(fraction * len(items)))]

    return {
        "total_p05": q(totals, 0.05),
        "total_p50": q(totals, 0.5),
        "sharpe_p50": q(sharpes, 0.5),
        "mdd_p05": q(mdds, 0.05),
    }


def load_benchmark() -> dict[str, float]:
    paths = sorted((ROOT / "data" / "kline_index_daily").glob("date=*/part.parquet"))
    frame = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(
            (pl.col("symbol") == "000300.SH")
            & pl.col("close").is_not_null()
            & (pl.col("close") > 0)
        )
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    values = frame["close"].to_list()
    dates = [str(value)[:10] for value in frame["date"].to_list()]
    return {
        dates[index]: values[index] / values[index - 1] - 1.0 for index in range(1, len(values))
    }


def curve_returns(curve: list[dict[str, Any]]) -> dict[str, float]:
    return {
        str(curve[index]["date"])[:10]: float(curve[index]["value"])
        / float(curve[index - 1]["value"])
        - 1.0
        for index in range(1, len(curve))
    }


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    verify_sidecar(R491)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r491 = load_module(ROOT / "scripts" / "research_r491_weight_quality_audit.py", "r492_r491")
    r468 = load_module(
        ROOT / "scripts" / "research_r468_operating_efficiency_sleeve.py", "r492_r468"
    )
    r464 = r468.load_module(
        ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r492_r464"
    )
    p5 = r464.load_p5()
    panel, _ = r464.build_panel(p5)
    from app.hs300.current import current_symbols

    price = p5.load_candidate_panel(ROOT / "data", current_symbols(), [])[0].with_columns(
        (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1.0).alias("momentum_60d")
    )
    panel = panel.join(
        price.select(["symbol", "date", "momentum_60d"]), on=["symbol", "date"], how="left"
    ).sort(["symbol", "date"])
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot

    snapshot = load_fundamental_snapshot(ROOT / "data")
    selected_r491 = json.loads(R491.read_text(encoding="utf-8"))["selected"]
    members = json.loads(
        (E / "r491_weight_quality_audit_preregistration.json").read_text(encoding="utf-8")
    )["members"]
    missing = [factor for factor in members if factor not in panel.columns]
    if missing:
        panel = attach_fundamental_factors(panel, snapshot, missing)
    symbols = sorted(panel["symbol"].unique().to_list())
    service = p5.portfolio._service()
    benchmark = load_benchmark()
    windows = prereg["windows"]
    output = {}
    for window in ("test", "recent"):
        result = r491.run_sample(
            p5,
            service,
            panel,
            symbols,
            selected_r491["weights"],
            members,
            start=windows[f"{window}_start"],
            end=windows["end"],
            cost_label="2x",
        )
        strategy = curve_returns(result["curve"])
        pairs = pair_returns(strategy, benchmark)
        active = [strategy_value - benchmark_value for _, strategy_value, benchmark_value in pairs]
        absolute = [strategy_value for _, strategy_value, _ in pairs]
        output[window] = {
            "pair_count": len(pairs),
            "strategy_date_count": len(strategy),
            "missing_benchmark_dates": sorted(set(strategy) - set(benchmark)),
            "first_date": pairs[0][0] if pairs else None,
            "last_date": pairs[-1][0] if pairs else None,
            "absolute": {
                str(block): block_bootstrap(absolute, block=block, paths=5000, seed=492)
                for block in prereg["bootstrap"]["block_days"]
            },
            "active": {
                str(block): block_bootstrap(active, block=block, paths=5000, seed=493)
                for block in prereg["bootstrap"]["block_days"]
            },
        }
    payload = {
        "round": "R492",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_r491_sha256": sha(R491),
        "benchmark": prereg["benchmark"],
        "windows": output,
        "decision": prereg["output_policy"],
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    lines = [
        "# R492 配对超额 Bootstrap",
        "",
        "本轮把 R490 的绝对收益尾部和相对沪深300 的超额尾部拆开，严格按共同交易日配对。",
        "",
        "| 窗口 | 配对天数 | 块长 | 绝对收益 p05 | 超额 p05 | 绝对中位 | 超额中位 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for window, item in output.items():
        for block in prereg["bootstrap"]["block_days"]:
            abs_value = item["absolute"][str(block)]
            active_value = item["active"][str(block)]
            lines.append(
                f"| {window} | {item['pair_count']} | {block} | "
                f"{abs_value['total_p05']:.2%} | {active_value['total_p05']:.2%} | "
                f"{abs_value['total_p50']:.2%} | {active_value['total_p50']:.2%} |"
            )
    lines += (
        [
            "",
            "## 结论",
            "",
            "- 绝对 p05 与超额 p05 分开报告；不能用超额稳健掩盖绝对亏损风险。",
            "- R490 仍为 rejected，`active_version` 保持 null。",
            "",
            "## 边界",
            "",
        ]
        + [f"- {item}" for item in prereg["known_limits"]]
        + [""]
    )
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii"
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
