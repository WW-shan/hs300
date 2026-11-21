#!/usr/bin/env python3
"""R449: five-factor composite versus four-factor control, with industry caps."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r449_five_factor_composite_preregistration.json"
OUTPUT = E / "r449_five_factor_composite_2026-11-22.json"
RESULT = E / "r449_five_factor_composite_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r449_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def bootstrap(curve: list[dict[str, Any]], *, block: int, paths: int, seed: int) -> dict[str, float]:
    values = [float(curve[i]["value"]) / float(curve[i - 1]["value"]) - 1.0 for i in range(1, len(curve))]
    rng = random.Random(seed)
    totals, sharpes, mdds = [], [], []
    for _ in range(paths):
        sampled = []
        while len(sampled) < len(values):
            start = rng.randrange(len(values))
            sampled.extend(values[(start + offset) % len(values)] for offset in range(block))
        selected = sampled[: len(values)]
        value = 1.0
        path = [1.0]
        for item in selected:
            value *= 1.0 + item
            path.append(value)
        mean = sum(selected) / len(selected)
        vol = (sum((item - mean) ** 2 for item in selected) / len(selected)) ** 0.5
        peak = path[0]
        mdd = 0.0
        for item in path:
            peak = max(peak, item)
            mdd = min(mdd, item / peak - 1.0)
        totals.append(value - 1.0)
        sharpes.append(mean / vol * (252.0 ** 0.5) if vol else 0.0)
        mdds.append(mdd)
    def q(items: list[float], fraction: float) -> float:
        ordered = sorted(items)
        return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]
    return {"total_p05": q(totals, 0.05), "total_p50": q(totals, 0.5), "sharpe_p50": q(sharpes, 0.5), "mdd_p05": q(mdds, 0.05)}


def run_window(p5: Any, service: Any, panel: pl.DataFrame, symbols: list[str], members: list[str], directions: dict[str, str], industry: dict[str, str], *, start: str, end: str, positions: int, hold: int, cost: str, cap: int) -> dict[str, Any]:
    scoring, selected = p5._equal_inputs(members, directions)
    prepared = p5._prepare_shared_window(service, symbols, panel, members, start=date.fromisoformat(start), end=date.fromisoformat(end))
    config = p5.portfolio._backtest_config(
        scoring,
        selected,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=positions,
        cost=(
            p5.portfolio._cost_parameters(cost)
            if cost != "5x"
            else {
                "fees_pct": 0.001,
                "commission_pct": 0.001,
                "stamp_tax_pct": 0.0005,
                "slippage_bps": 25.0,
            }
        ),
        max_hold_days=hold,
        industry_by_symbol=industry if cap else None,
        max_industry_positions=cap,
    )
    config.symbols = list(prepared.market_data.symbols)
    result = service.run(config, prepared=prepared)
    if result.error:
        raise RuntimeError(result.error)
    return {"stats": p5._select_stats(result.stats), "equity_curve": list(result.equity_curve), "trades": len(result.trades)}


def neutral_ic(panel: pl.DataFrame, factor: str, horizon: int) -> dict[str, Any]:
    from app.factor_research.signal_scan import calculate_rank_ic, summarise_ic
    from app.strategy.scoring import neutralize_scoring_columns
    return_column = f"forward_return_{horizon}d"
    spec = {"industry": "industry_l1", "styles": ["log_float_mv"], "exclude": [], "shrink": 1.0}
    neutral = neutralize_scoring_columns(
        panel.select(["date", "symbol", factor, return_column, "industry_l1", "log_float_mv"]),
        [factor],
        spec,
    )
    ic = calculate_rank_ic(neutral, factor, return_column, minimum=30)
    summary = summarise_ic(ic, recent_start=date(2025, 10, 14), direction="high", nw_lag=horizon)
    return {
        "directional_ic": summary["full"].get("directional_ic_mean"),
        "p_value": summary.get("p_value"),
        "yearly_sign_consistency": summary.get("yearly_sign_consistency"),
        "recent_directional_ic": summary["recent"].get("directional_ic_mean"),
        "rows": ic.height,
    }


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R449 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R449 preregistration checksum mismatch")
    p5 = load_p5()
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.hs300.current import current_symbols
    from app.services.industry_map import load_industry_l1
    members = list(prereg["members"])
    four = list(prereg["controls"]["four_factor_members"])
    directions = dict(prereg["directions"])
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    extra = load_existing_ext_factors(DATA, {"ext_alt_hs300_vol_of_vol_60d_low": "vol_of_vol_60d_low"})
    panel = panel.join(extra, on=["symbol", "date"], how="left").sort(["symbol", "date"])
    for horizon in prereg["neutrality"]["horizons"]:
        panel = panel.with_columns(
            (pl.col("close").shift(-int(horizon)).over("symbol") / pl.col("close") - 1.0).alias(f"forward_return_{int(horizon)}d")
        )
    industry = load_industry_l1(DATA).select(["symbol", "industry_l1"]).unique(subset=["symbol"], keep="last")
    industry_map = dict(zip(industry["symbol"].to_list(), industry["industry_l1"].to_list(), strict=True))
    symbols = sorted(panel["symbol"].unique().to_list())
    service = p5.portfolio._service()
    windows = prereg["windows"]
    rows = []
    for members_name, members_now in (("four", four), ("five", members)):
        for positions in prereg["grid"]["max_positions"]:
            for hold in (30,):
                for cap in (0, 2, 3):
                    row = {"members": members_name, "positions": positions, "hold": hold, "industry_cap": cap}
                    for window in ("train", "validation", "test", "recent"):
                        row[window] = run_window(
                            p5, service, panel, symbols, members_now, directions, industry_map,
                            start=windows[f"{window}_start"], end=windows["test_end"] if window == "recent" else windows[f"{window}_end"],
                            positions=positions, hold=hold, cost="2x", cap=cap,
                        )
                    row["test_3x"] = run_window(
                        p5, service, panel, symbols, members_now, directions, industry_map,
                        start=windows["test_start"], end=windows["test_end"], positions=positions, hold=hold, cost="3x", cap=cap,
                    )
                    rows.append(row)
    # The five-factor selection uses train+validation only; no test peeking.
    candidates = [row for row in rows if row["members"] == "five"]
    eligible = [row for row in candidates if row["train"]["stats"]["annual_return"] >= prereg["gates"]["train_annual_min"] and row["validation"]["stats"]["annual_return"] >= prereg["gates"]["validation_annual_min"]]
    eligible.sort(key=lambda row: (min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]), row["positions"], row["industry_cap"]), reverse=True)
    selected = eligible[0] if eligible else max(candidates, key=lambda row: min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]))
    control = next(row for row in rows if row["members"] == "four" and row["positions"] == selected["positions"] and row["industry_cap"] == selected["industry_cap"])
    selected["test_bootstrap"] = bootstrap(selected["test"]["equity_curve"], block=20, paths=5000, seed=449)
    selected["recent_bootstrap"] = bootstrap(selected["recent"]["equity_curve"], block=20, paths=5000, seed=450)
    selected["test_5x"] = run_window(
        p5, service, panel, symbols, members, directions, industry_map,
        start=windows["test_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost="5x", cap=selected["industry_cap"],
    )
    increment = selected["test"]["stats"]["annual_return"] - control["test"]["stats"]["annual_return"]
    checks = {
        "has_eligible_rule": bool(eligible),
        "train_annual_min": selected["train"]["stats"]["annual_return"] >= prereg["gates"]["train_annual_min"],
        "validation_annual_min": selected["validation"]["stats"]["annual_return"] >= prereg["gates"]["validation_annual_min"],
        "test_annual_min": selected["test"]["stats"]["annual_return"] >= prereg["gates"]["test_annual_min"],
        "test_sharpe_min": selected["test"]["stats"]["sharpe"] >= prereg["gates"]["test_sharpe_min"],
        "test_mdd_min": selected["test"]["stats"]["max_drawdown"] >= prereg["gates"]["test_mdd_min"],
        "recent_annual_min": selected["recent"]["stats"]["annual_return"] >= prereg["gates"]["recent_annual_min"],
        "stress_3x_positive": selected["test_3x"]["stats"]["annual_return"] > 0,
        "five_factor_beats_four_factor": increment > 0,
        "test_total_p05_positive": selected["test_bootstrap"]["total_p05"] > 0,
        "recent_total_p05_positive": selected["recent_bootstrap"]["total_p05"] > 0,
        "industry_size_neutral_signal": all(
            neutral_ic(panel, member, 60)["directional_ic"] > 0
            and neutral_ic(panel, member, 60)["p_value"] is not None
            and neutral_ic(panel, member, 60)["p_value"] <= 0.10
            and neutral_ic(panel, member, 60)["yearly_sign_consistency"] is not None
            and neutral_ic(panel, member, 60)["yearly_sign_consistency"] >= 0.8
            and neutral_ic(panel, member, 60)["recent_directional_ic"] > 0
            for member in members
        ),
        "no_leverage": True,
    }
    neutral = {member: {str(horizon): neutral_ic(panel, member, int(horizon)) for horizon in prereg["neutrality"]["horizons"]} for member in members}
    payload = {
        "round": "R449",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "rows": rows,
        "selected": selected,
        "control": control,
        "test_increment_annual": increment,
        "neutral_ic": neutral,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "diagnostics": {"five_x_warning_only": True, "five_x_hard_rejection": False, "five_x_stats": selected.get("test_5x", {}).get("stats")},
        "known_limits": prereg["known_limits"],
    }
    # Strip full curves from frozen output; keep only summary evidence.
    for row in payload["rows"]:
        for key in ("train", "validation", "test", "recent", "test_3x"):
            row[key].pop("equity_curve", None)
    for key in ("test", "recent", "test_3x", "test_5x"):
        payload["selected"].get(key, {}).pop("equity_curve", None)
    payload["control"].pop("equity_curve", None)
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R449 五因子组合与行业中性化", "", "| 组合 | 仓数 | 行业上限 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 3×检验年化 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in rows:
        name = "五因子" if row["members"] == "five" else "四因子"
        lines.append(f"| {name} | {row['positions']} | {row['industry_cap'] or '-'} | {row['train']['stats']['annual_return']:.2%} | {row['validation']['stats']['annual_return']:.2%} | {row['test']['stats']['annual_return']:.2%} | {row['test']['stats']['sharpe']:.3f} | {row['test']['stats']['max_drawdown']:.2%} | {row['recent']['stats']['annual_return']:.2%} | {row['test_3x']['stats']['annual_return']:.2%} |")
    lines += ["", f"- 选中：五因子 {selected['positions']} 仓，行业上限 {selected['industry_cap'] or '无'}，持有 {selected['hold']} 日", f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**", f"- 对四因子检验年化增量：{increment:.2%}", "", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 行业+规模中性 IC", ""]
    for member, horizons in neutral.items():
        lines.append(f"- `{member}`: 60日 IC {horizons['60']['directional_ic']:.4f}, p={horizons['60']['p_value']}, 年度同号={horizons['60']['yearly_sign_consistency']}, 近一年 IC={horizons['60']['recent_directional_ic']:.4f}")
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
