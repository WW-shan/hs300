#!/usr/bin/env python3
"""R522: fresh-start one-year audit of the frozen R506 candidate."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.factor_research.evidence_guard import require_formal_evidence

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r522_r506_fresh_start_year_audit_preregistration.json"
R506 = E / "r506_r499_cap1_confirmation_2026-11-22.json"
OUTPUT = E / "r522_r506_fresh_start_year_audit_2026-11-23.json"
RESULT = E / "r522_r506_fresh_start_year_audit_2026-11-23.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R522 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def audit_window(start: date, end: date) -> tuple[date, date]:
    return start, end


def window_label(start: date, end: date) -> str:
    return "full_year" if (end - start).days >= 350 else "partial"


def block_bootstrap(
    curve: list[dict[str, Any]], *, block: int, paths: int, seed: int
) -> dict[str, float | int]:
    values = [
        float(curve[index]["value"]) / float(curve[index - 1]["value"]) - 1.0
        for index in range(1, len(curve))
        if float(curve[index - 1].get("value") or 0.0) > 0.0
    ]
    if not values:
        return {"total_p05": 0.0, "total_p50": 0.0, "sharpe_p50": 0.0, "n": 0}
    block = min(int(block), len(values))
    rng = random.Random(seed)
    totals = []
    sharpes = []
    for _ in range(paths):
        sampled: list[float] = []
        while len(sampled) < len(values):
            start = rng.randrange(len(values))
            sampled.extend(values[(start + offset) % len(values)] for offset in range(block))
        selected = sampled[: len(values)]
        product = math.prod(1.0 + value for value in selected)
        mean = sum(selected) / len(selected)
        vol = (sum((value - mean) ** 2 for value in selected) / len(selected)) ** 0.5
        totals.append(product - 1.0)
        sharpes.append(mean / vol * math.sqrt(252.0) if vol else 0.0)

    def ordered(items: list[float], q: float) -> float:
        return sorted(items)[min(len(items) - 1, int(q * len(items)))]

    return {
        "total_p05": ordered(totals, 0.05),
        "total_p50": ordered(totals, 0.50),
        "sharpe_p50": ordered(sharpes, 0.50),
        "n": len(values),
    }


def hs300_returns() -> dict[str, float]:
    import polars as pl

    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    frame = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter((pl.col("symbol") == "000300.SH") & (pl.col("close") > 0))
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    dates = [str(value)[:10] for value in frame["date"].to_list()]
    closes = [float(value) for value in frame["close"].to_list()]
    return {dates[index]: closes[index] / closes[index - 1] - 1.0 for index in range(1, len(dates))}


def benchmark_return(start: date, end: date, daily: dict[str, float]) -> float:
    values = [
        value
        for day, value in daily.items()
        if start.isoformat() <= day <= end.isoformat()
    ]
    return round(math.prod(1.0 + value for value in values) - 1.0, 8) if values else 0.0


def build_panel(p5: Any) -> tuple[Any, list[str]]:
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.factor_research.incremental import rank_score_frame
    from app.hs300.current import current_symbols

    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    panel = attach_fundamental_factors(
        panel, snapshot, ["pb_latest", "ocf_debt", "debt_ratio_latest"]
    )
    ext = load_existing_ext_factors(
        DATA,
        {
            "ext_alt_hs300_cash_flow_stability_4q_low": "cash_flow_stability_4q_low",
            "ext_alt_hs300_dividend_yield": "dividend_yield",
            "ext_alt_hs300_fund_holding_ratio_low": "fund_holding_ratio_low",
            "ext_alt_hs300_idio_vol_40d_low": "idio_vol_40d_low",
        },
    )
    panel = panel.join(ext, on=["symbol", "date"], how="left").sort(["symbol", "date"])
    for member, direction in (
        ("pb_latest", "low"),
        ("ocf_debt", "high"),
        ("debt_ratio_latest", "low"),
    ):
        panel = rank_score_frame(panel, member, direction, output_column=f"_r522_vq_{member}")
    panel = panel.with_columns(
        (
            pl.col("_r522_vq_pb_latest").fill_null(0.0)
            + pl.col("_r522_vq_ocf_debt").fill_null(0.0)
            + pl.col("_r522_vq_debt_ratio_latest").fill_null(0.0)
        ).alias("value_quality"),
        (
            pl.col("_r522_vq_pb_latest").is_not_null()
            & pl.col("_r522_vq_ocf_debt").is_not_null()
            & pl.col("_r522_vq_debt_ratio_latest").is_not_null()
        ).alias("_r522_vq_valid"),
    ).filter(pl.col("_r522_vq_valid"))
    return panel, sorted(panel["symbol"].unique().to_list())


def run_frozen(
    p5: Any,
    service: Any,
    panel: Any,
    symbols: list[str],
    prereg: dict[str, Any],
    *,
    start: date,
    end: date,
    cost_label: str,
) -> dict[str, Any]:
    from app.factor_research.incremental import build_rank_composite

    weights = {
        "ext_alt_hs300_cash_flow_stability_4q_low": 0.2375,
        "ext_alt_hs300_dividend_yield": 0.2375,
        "ext_alt_hs300_fund_holding_ratio_low": 0.2375,
        "ext_alt_hs300_idio_vol_40d_low": 0.2375,
        "value_quality": 0.05,
    }
    field = "_r522_r506_composite"
    work, _ = build_rank_composite(
        panel,
        weights,
        {member: "high" for member in weights},
        field,
        minimum_weight_coverage=0.75,
    )
    fixed = prereg["fixed_execution"]
    config = p5.portfolio._backtest_config(
        {field: 1.0},
        {field: "high"},
        start=start,
        end=end,
        max_positions=int(fixed["max_positions"]),
        cost=(
            p5.portfolio._cost_parameters(cost_label)
            if cost_label != "5x"
            else {
                "fees_pct": 0.001,
                "commission_pct": 0.001,
                "stamp_tax_pct": 0.0005,
                "slippage_bps": 25.0,
            }
        ),
        max_hold_days=int(fixed["max_hold_days"]),
    )
    config.params = {
        **(config.params or {}),
        "entry_score": float(fixed["entry_score"]),
        "exit_score": float(fixed["exit_score"]),
        "top_rank": int(fixed["top_rank"]),
    }
    config.max_exposure_pct = float(fixed["max_exposure_pct"])
    config.max_new_positions_per_day = int(fixed["max_new_positions_per_day"])
    config.symbols = list(symbols)
    prepared_base = service.prepare_matrix_optimization([config])
    prepared = p5._extend_prepared_with_panel(prepared_base, work, [field])
    result = service.run_with_evidence(config, prepared=prepared)
    require_formal_evidence(result, label="research_r522_r506_fresh_start_year_audit.py")
    if result.error:
        raise RuntimeError(result.error)
    return {
        "stats": p5._select_stats(result.stats),
        "curve": list(result.equity_curve),
        "trades": len(result.trades),
    }


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    verify_sidecar(R506)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(R506) != prereg["source_r506_sha256"]:
        raise RuntimeError("R506 source changed")
    r453 = load_module(
        ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r522_r453"
    )
    p5 = r453.load_p5()
    panel, symbols = build_panel(p5)
    service = p5.portfolio._service()
    end = date.fromisoformat(prereg["end"])
    benchmark_daily = hs300_returns()
    windows = []
    for start_text in prereg["starts"]:
        start = date.fromisoformat(start_text)
        window_start, window_end = audit_window(start, end)
        base = run_frozen(
            p5, service, panel, symbols, prereg, start=window_start, end=window_end, cost_label="2x"
        )
        stress = run_frozen(
            p5, service, panel, symbols, prereg, start=window_start, end=window_end, cost_label="3x"
        )
        warning = run_frozen(
            p5, service, panel, symbols, prereg, start=window_start, end=window_end, cost_label="5x"
        )
        bench = benchmark_return(window_start, window_end, benchmark_daily)
        window = {
            "start": start_text,
            "end": prereg["end"],
            "label": window_label(window_start, window_end),
            "calendar_days": (window_end - window_start).days,
            "stats_2x": base["stats"],
            "stats_3x": stress["stats"],
            "stats_5x": warning["stats"],
            "bootstrap": block_bootstrap(
                base["curve"],
                block=int(prereg["bootstrap"]["block_days"]),
                paths=int(prereg["bootstrap"]["replications"]),
                seed=int(prereg["bootstrap"]["seed"]),
            ),
            "benchmark_return": bench,
            "excess_return": round(base["stats"]["total_return"] - bench, 8),
            "trades": base["trades"],
        }
        windows.append(window)
        print(
            json.dumps(
                {
                    "start": start_text,
                    "label": window["label"],
                    "annual": window["stats_2x"]["annual_return"],
                    "mdd": window["stats_2x"]["max_drawdown"],
                    "excess": window["excess_return"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    worst_mdd = min(window["stats_2x"]["max_drawdown"] for window in windows)
    worst_p05 = min(window["bootstrap"]["total_p05"] for window in windows)
    checks = {
        "all_windows_positive_absolute": all(
            window["stats_2x"]["annual_return"] > 0 for window in windows
        ),
        "all_windows_positive_excess": all(window["excess_return"] > 0 for window in windows),
        "worst_window_mdd_min": worst_mdd >= prereg["gates"]["worst_window_mdd_min"],
        "stress_3x_positive_all": all(
            window["stats_3x"]["annual_return"] > 0 for window in windows
        ),
        "worst_bootstrap_total_p05_positive": worst_p05 > 0,
        "no_leverage": True,
    }
    recent = windows[0]
    summary = {
        "window_count": len(windows),
        "full_year_count": sum(window["label"] == "full_year" for window in windows),
        "positive_count": sum(window["stats_2x"]["annual_return"] > 0 for window in windows),
        "latest_full_year": recent,
        "worst_mdd": worst_mdd,
        "worst_bootstrap_total_p05": worst_p05,
    }
    payload = {
        "round": "R522",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_r506_sha256": sha(R506),
        "selection_performed": False,
        "summary": summary,
        "windows": windows,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "decision": {
            **prereg["output_policy"],
            "status": "watch_candidate" if all(checks.values()) else "rejected",
        },
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    lines = [
        "# R522 R506 fresh-start 一年审计",
        "",
        "- 冻结 R506，不重新选样。",
        f"- 窗口数：{len(windows)}；完整一年：{summary['full_year_count']}",
        f"- 绝对正收益：{summary['positive_count']}/{len(windows)}",
        f"- 最差 MDD：{worst_mdd:.2%}",
        f"- 最差 bootstrap p05：{worst_p05:.2%}",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        "",
        "| 起点 | 类型 | 年化 | Sharpe | MDD | 超额 | 3×年化 | p05 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for window in windows:
        lines.append(
            f"| {window['start']} | {window['label']} | "
            f"{window['stats_2x']['annual_return']:.2%} | {window['stats_2x']['sharpe']:.3f} | "
            f"{window['stats_2x']['max_drawdown']:.2%} | {window['excess_return']:+.2%} | "
            f"{window['stats_3x']['annual_return']:.2%} | {window['bootstrap']['total_p05']:.2%} |"
        )
    lines += ["", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
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
