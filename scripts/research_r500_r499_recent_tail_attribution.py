#!/usr/bin/env python3
"""R500: recent-tail attribution for the frozen R499 weak-state tilt."""

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

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r500_r499_recent_tail_attribution_preregistration.json"
R499 = E / "r499_weak_state_value_quality_tilt_2026-11-22.json"
R498 = E / "r498_drawdown_conditional_attribution_2026-11-22.json"
OUTPUT = E / "r500_r499_recent_tail_attribution_2026-11-22.json"
RESULT = E / "r500_r499_recent_tail_attribution_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R500 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def monthly_returns(curve: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in curve:
        groups.setdefault(str(item["date"])[:7], []).append(item)
    output = {}
    for month, items in sorted(groups.items()):
        start_value = float(items[0]["value"])
        end_value = float(items[-1]["value"])
        output[month] = {
            "return": end_value / start_value - 1.0 if start_value else 0.0,
            "start": str(items[0]["date"])[:10],
            "end": str(items[-1]["date"])[:10],
        }
    return output


def daily_returns(curve: list[dict[str, Any]]) -> list[tuple[str, float]]:
    return [
        (
            str(curve[index]["date"])[:10],
            float(curve[index]["value"]) / float(curve[index - 1]["value"]) - 1.0,
        )
        for index in range(1, len(curve))
        if float(curve[index - 1].get("value") or 0.0) > 0.0
    ]


def paired_values(
    strategy: list[tuple[str, float]], benchmark: dict[str, float]
) -> list[tuple[str, float]]:
    return [
        (day, strategy_return - benchmark[day])
        for day, strategy_return in strategy
        if day in benchmark
    ]


def bootstrap_returns(
    values: list[float], *, block: int = 20, paths: int = 5000, seed: int = 500
) -> dict[str, float]:
    if not values:
        return {"total_p05": 0.0, "total_p50": 0.0, "sharpe_p50": 0.0, "mdd_p05": 0.0}
    rng = random.Random(seed)
    totals, sharpes, mdds = [], [], []
    for _ in range(paths):
        sampled: list[float] = []
        while len(sampled) < len(values):
            start = rng.randrange(len(values))
            sampled.extend(values[(start + offset) % len(values)] for offset in range(block))
        selected = sampled[: len(values)]
        product = math.prod(1.0 + value for value in selected)
        mean = sum(selected) / len(selected)
        vol = (sum((value - mean) ** 2 for value in selected) / len(selected)) ** 0.5
        path = [1.0]
        for value in selected:
            path.append(path[-1] * (1.0 + value))
        peak = path[0]
        mdd = 0.0
        for value in path:
            peak = max(peak, value)
            mdd = min(mdd, value / peak - 1.0)
        totals.append(product - 1.0)
        sharpes.append(mean / vol * math.sqrt(252.0) if vol else 0.0)
        mdds.append(mdd)

    def ordered(items: list[float], q: float) -> float:
        return sorted(items)[min(len(items) - 1, int(q * len(items)))]

    return {
        "total_p05": ordered(totals, 0.05),
        "total_p50": ordered(totals, 0.50),
        "sharpe_p50": ordered(sharpes, 0.50),
        "mdd_p05": ordered(mdds, 0.05),
    }


def hs300_returns_by_day() -> dict[str, float]:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
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
    closes = [float(value) for value in frame["close"].to_list()]
    return {dates[index]: closes[index] / closes[index - 1] - 1.0 for index in range(1, len(dates))}


def build_panel(p5: Any, r499_mod: Any) -> tuple[pl.DataFrame, list[str]]:
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
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
    from app.factor_research.incremental import rank_score_frame

    for member, direction in (
        ("pb_latest", "low"),
        ("ocf_debt", "high"),
        ("debt_ratio_latest", "low"),
    ):
        panel = rank_score_frame(panel, member, direction, output_column=f"_r500_vq_{member}")
    panel = panel.with_columns(
        (
            pl.col("_r500_vq_pb_latest").fill_null(0.0)
            + pl.col("_r500_vq_ocf_debt").fill_null(0.0)
            + pl.col("_r500_vq_debt_ratio_latest").fill_null(0.0)
        ).alias("value_quality"),
        (
            pl.col("_r500_vq_pb_latest").is_not_null()
            & pl.col("_r500_vq_ocf_debt").is_not_null()
            & pl.col("_r500_vq_debt_ratio_latest").is_not_null()
        ).alias("_r500_vq_valid"),
    ).filter(pl.col("_r500_vq_valid"))
    del r499_mod
    return panel, sorted(panel["symbol"].unique().to_list())


def leg_ic(
    panel: pl.DataFrame, factor: str, return_column: str, minimum: int = 30
) -> dict[str, Any]:
    from app.factor_research.incremental import rank_score_frame

    work = rank_score_frame(panel, factor, "high", output_column=f"_r500_{factor}")
    scored = work.filter(
        pl.col(f"_r500_{factor}").is_not_null()
        & pl.col(return_column).is_not_null()
        & pl.col(return_column).is_finite()
    )
    daily = (
        scored.group_by("date")
        .agg(
            pl.len().alias("n"),
            pl.corr(pl.col(f"_r500_{factor}"), pl.col(return_column)).alias("ic"),
        )
        .filter((pl.col("n") >= minimum) & pl.col("ic").is_not_null())
    )
    values = daily["ic"].to_numpy() if daily.height else []
    return {
        "n_dates": len(values),
        "mean_ic": round(float(sum(values) / len(values)), 8) if len(values) else None,
    }


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    verify_sidecar(R499)
    verify_sidecar(R498)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r499 = json.loads(R499.read_text(encoding="utf-8"))
    r498 = json.loads(R498.read_text(encoding="utf-8"))
    selected = r499["selected"]
    if selected["weak_value_quality"] != 0.05:
        raise RuntimeError("R499 selected sample changed")
    threshold = r498["weak_state"]

    r453 = load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r500_r453")
    r498_mod = load_module(
        ROOT / "scripts" / "research_r498_drawdown_conditional_attribution.py", "r500_r498"
    )
    r499_mod = load_module(
        ROOT / "scripts" / "research_r499_weak_state_value_quality_tilt.py", "r500_r499"
    )
    p5 = r453.load_p5()
    panel, symbols = build_panel(p5, r499_mod)
    service = p5.portfolio._service()
    windows = prereg["windows"]

    regime = (
        pl.read_parquet(DATA / "regime_history" / "part.parquet")
        .sort("date")
        .with_columns(pl.col("date").cast(pl.Utf8))
        .select(["date", "score", "above_ma20_pct"])
    )
    labelled = r498_mod.apply_weak_state(regime.to_dicts(), threshold)
    weak_dates = {str(row["date"])[:10] for row in labelled if row["weak"]}
    benchmark = hs300_returns_by_day()

    payload: dict[str, Any] = {
        "round": "R500",
        "classification": prereg["classification"],
        "source_round": "R499",
        "source_r499_sha256": sha(R499),
        "source_r498_sha256": sha(R498),
        "selection_performed": False,
        "selected_weak_value_quality": selected["weak_value_quality"],
        "weak_threshold": threshold,
        "windows": {},
        "diagnostics": {"five_x_warning_only": True},
        "decision": {**prereg["output_policy"]},
        "known_limits": prereg["known_limits"],
    }
    for window in ("test", "recent"):
        start = windows[f"{window}_start"]
        end = windows["test_end"]
        curve = r499_mod.run_sample(
            p5,
            service,
            panel,
            symbols,
            weak_dates,
            float(selected["weak_value_quality"]),
            json.loads(
                (E / "r499_weak_state_value_quality_tilt_preregistration.json").read_text(
                    encoding="utf-8"
                )
            ),
            start=start,
            end=end,
            cost_label="2x",
        )["curve"]
        daily = daily_returns(curve)
        paired = paired_values(daily, benchmark)
        months = monthly_returns(curve)
        worst_months = sorted(months.items(), key=lambda item: item[1]["return"])[:5]
        weak = [value for day, value in daily if day in weak_dates]
        nonweak = [value for day, value in daily if day not in weak_dates]
        losing_months = {month for month, row in months.items() if row["return"] < 0}
        scoped = panel.filter(
            (pl.col("date") >= date.fromisoformat(start))
            & (pl.col("date") <= date.fromisoformat(end))
            & pl.col("date").cast(pl.Utf8).str.slice(0, 7).is_in(list(losing_months))
        )
        leg_rows = {
            factor: leg_ic(scoped, factor, "forward_return_5d") for factor in prereg["legs"]
        }
        payload["windows"][window] = {
            "days": len(daily),
            "paired_days": len(paired),
            "absolute_bootstrap": bootstrap_returns(
                [value for _, value in daily],
                block=int(prereg["bootstrap"]["block_days"]),
                paths=int(prereg["bootstrap"]["replications"]),
                seed=int(prereg["bootstrap"]["seeds"][window]),
            ),
            "paired_excess_bootstrap": bootstrap_returns(
                [value for _, value in paired],
                block=int(prereg["bootstrap"]["block_days"]),
                paths=int(prereg["bootstrap"]["replications"]),
                seed=int(prereg["bootstrap"]["seeds"][window]) + 100,
            ),
            "monthly": {month: row["return"] for month, row in months.items()},
            "worst_months": [
                {"month": month, "return": row["return"]} for month, row in worst_months
            ],
            "weak_mean_daily": round(float(sum(weak) / len(weak)), 8) if weak else None,
            "nonweak_mean_daily": round(float(sum(nonweak) / len(nonweak)), 8) if nonweak else None,
            "losing_month_leg_ic": leg_rows,
        }

    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )

    lines = [
        "# R500 R499 近一年尾部归因",
        "",
        "本轮不重新选样，只拆解 R499 冻结样本的检验和近一年尾部。",
        "",
        "| 窗口 | 绝对 p05 | 超额 p05 | 弱市日均 | 非弱市日均 | 最差月份 |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for window in ("test", "recent"):
        row = payload["windows"][window]
        worst = row["worst_months"][0]
        lines.append(
            f"| {window} | {row['absolute_bootstrap']['total_p05']:.2%} | "
            f"{row['paired_excess_bootstrap']['total_p05']:.2%} | "
            f"{row['weak_mean_daily']:.4%} | {row['nonweak_mean_daily']:.4%} | "
            f"{worst['month']} {worst['return']:.2%} |"
        )
    lines += ["", "## 月度收益", "", "| 窗口 | 月份 | 收益 |", "| --- | --- | ---: |"]
    for window in ("test", "recent"):
        for month, value in sorted(payload["windows"][window]["monthly"].items()):
            lines.append(f"| {window} | {month} | {value:.2%} |")
    lines += [
        "",
        "## 近一年亏损月因子 IC",
        "",
        "| 因子 | 近一年亏损月 IC | 有效日期 |",
        "| --- | ---: | ---: |",
    ]
    for factor, row in payload["windows"]["recent"]["losing_month_leg_ic"].items():
        ic = row["mean_ic"]
        lines.append(f"| `{factor}` | {ic if ic is not None else '-'} | {row['n_dates']} |")
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
