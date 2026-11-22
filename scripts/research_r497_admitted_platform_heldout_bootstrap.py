#!/usr/bin/env python3
"""R497: fresh-seed held-out bootstrap of the frozen R496 admitted platform."""

from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r497_admitted_platform_heldout_bootstrap_preregistration.json"
R496 = E / "r496_admitted_five_factor_platform_2026-11-22.json"
OUTPUT = E / "r497_admitted_platform_heldout_bootstrap_2026-11-22.json"
RESULT = E / "r497_admitted_platform_heldout_bootstrap_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def bootstrap_returns(
    values: list[float], *, seed: int, block: int = 20, paths: int = 5000
) -> dict[str, float]:
    rng = random.Random(seed)
    totals, sharpes, mdds = [], [], []
    for _ in range(paths):
        sampled = []
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


def hs300_returns_by_day() -> dict[str, float]:
    import polars as pl

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
    dates = [str(value)[:10] for value in frame["date"].to_list()]
    values = frame["close"].to_list()
    return {
        dates[index]: values[index] / values[index - 1] - 1.0 for index in range(1, len(values))
    }


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R497 output exists: {path.name}")
    verify_sidecar(PREREG)
    verify_sidecar(R496)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    source = json.loads(R496.read_text(encoding="utf-8"))
    selected = source["selected"]
    if selected["label"] != prereg["source_selected_label"]:
        raise RuntimeError("R496 selected sample changed")
    # R496 stored only summary metrics; regenerate the exact frozen curve through its runner.
    import importlib.util
    import sys

    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.hs300.current import current_symbols

    def load(path: Path, name: str):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    r496 = load(ROOT / "scripts" / "research_r496_admitted_five_factor_platform.py", "r497_r496")
    r468 = load(ROOT / "scripts" / "research_r468_operating_efficiency_sleeve.py", "r497_r468")
    r464 = r468.load_module(
        ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r497_r464"
    )
    p5 = r464.load_p5()
    import polars as pl

    panel, _ = r464.build_panel(p5)
    price = p5.load_candidate_panel(ROOT / "data", current_symbols(), [])[0].with_columns(
        (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1.0).alias("momentum_60d")
    )
    panel = panel.join(
        price.select(["symbol", "date", "momentum_60d"]), on=["symbol", "date"], how="left"
    ).sort(["symbol", "date"])
    ext = load_existing_ext_factors(
        ROOT / "data", {"ext_alt_hs300_vol_of_vol_60d_low": "vol_of_vol_60d_low"}
    )
    panel = panel.join(ext, on=["symbol", "date"], how="left").sort(["symbol", "date"])
    snapshot = load_fundamental_snapshot(ROOT / "data")
    missing = [member for member in selected["weights"] if member not in panel.columns]
    if missing:
        panel = attach_fundamental_factors(panel, snapshot, missing)
    symbols = sorted(panel["symbol"].unique().to_list())
    service = p5.portfolio._service()
    windows = prereg["windows"]
    benchmark = hs300_returns_by_day()
    payload = {
        "round": "R497",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_r496_sha256": sha(R496),
        "selected_label": selected["label"],
        "weights": selected["weights"],
        "windows": {},
    }
    for window in ("test", "recent"):
        curve = r496.run_sample(
            p5,
            service,
            panel,
            symbols,
            selected,
            start=windows[f"{window}_start"],
            end=windows["end"],
            cost_label="2x",
        )["curve"]
        returns = [
            (
                str(curve[i]["date"])[:10],
                float(curve[i]["value"]) / float(curve[i - 1]["value"]) - 1.0,
            )
            for i in range(1, len(curve))
        ]
        paired = [
            (day, strategy_return - benchmark[day])
            for day, strategy_return in returns
            if day in benchmark
        ]
        missing_dates = [day for day, _ in returns if day not in benchmark]
        payload["windows"][window] = {
            "strategy_days": len(returns),
            "paired_days": len(paired),
            "missing_benchmark_dates": missing_dates,
            "absolute_bootstrap": bootstrap_returns(
                [x[1] for x in returns], seed=prereg["seeds"]["absolute"]
            ),
            "paired_excess_bootstrap": bootstrap_returns(
                [x[1] for x in paired], seed=prereg["seeds"]["paired_excess"]
            ),
        }
    checks = {
        "test_absolute_p05_positive": payload["windows"]["test"]["absolute_bootstrap"]["total_p05"]
        > 0.0,
        "recent_absolute_p05_positive": payload["windows"]["recent"]["absolute_bootstrap"][
            "total_p05"
        ]
        > 0.0,
        "test_paired_excess_p05_positive": payload["windows"]["test"]["paired_excess_bootstrap"][
            "total_p05"
        ]
        > 0.0,
        "recent_paired_excess_p05_positive": payload["windows"]["recent"][
            "paired_excess_bootstrap"
        ]["total_p05"]
        > 0.0,
        "sample_unchanged": selected["label"] == prereg["source_selected_label"],
    }
    payload["gate"] = {"pass": all(checks.values()), "checks": checks}
    payload["decision"] = {
        **prereg["output_policy"],
        "status": "watch_candidate" if all(checks.values()) else "rejected",
    }
    payload["known_limits"] = prereg["known_limits"]
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    lines = [
        "# R497 R496 冻结样本独立播种 Bootstrap",
        "",
        "本轮不重新选样，只对 R496 选中平台用新随机种子重跑绝对与配对超额 bootstrap。",
        "",
        f"- 冻结样本：`{selected['label']}`",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        "",
        "| 窗口 | 配对天数 | 绝对 p05 | 超额 p05 | 缺失基准日 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for window in ("test", "recent"):
        item = payload["windows"][window]
        lines.append(
            f"| {window} | {item['paired_days']} | "
            f"{item['absolute_bootstrap']['total_p05']:.2%} | "
            f"{item['paired_excess_bootstrap']['total_p05']:.2%} | "
            f"{item['missing_benchmark_dates']} |"
        )
    lines += ["", "## 门槛", ""] + [
        f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()
    ]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii"
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
