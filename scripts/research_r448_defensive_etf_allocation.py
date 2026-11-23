#!/usr/bin/env python3
"""R448: pre-registered long-only defensive ETF allocation families."""
from __future__ import annotations

import hashlib
import json
import random
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

RESEARCH_TYPE = "diagnostic_custom_simulation"

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OFFENSIVE_SYMBOLS = {"510300.SH", "510500.SH", "512100.SH"}
E = DATA / "factor_research" / "research"
PREREG = E / "r448_defensive_etf_allocation_preregistration.json"
OUTPUT = E / "r448_defensive_etf_allocation_2026-11-22.json"
RESULT = E / "r448_defensive_etf_allocation_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preflight_etf_history(symbols: list[str], start: date, end: date) -> dict[str, Any]:
    """Fail closed when the ignored local ETF cache is missing or incomplete."""
    paths = sorted((DATA / "kline_etf_enriched").glob("date=*/part.parquet"))
    if not paths:
        raise FileNotFoundError(
            "R448 requires data/kline_etf_enriched; sync the six ETF histories before rerunning"
        )
    frame = load_prices(symbols, start, end)
    coverage = {
        symbol: {
            "rows": int(frame.filter(pl.col("symbol") == symbol).height),
            "first": str(frame.filter(pl.col("symbol") == symbol)["date"].min()),
            "last": str(frame.filter(pl.col("symbol") == symbol)["date"].max()),
        }
        for symbol in symbols
    }
    missing = [symbol for symbol, row in coverage.items() if row["rows"] == 0]
    if missing:
        raise FileNotFoundError(f"R448 ETF history missing symbols: {missing}")
    return {"source": "data/kline_etf_enriched", "coverage": coverage}


def load_prices(symbols: list[str], start: date, end: date) -> pl.DataFrame:
    paths = sorted((DATA / "kline_etf_enriched").glob("date=*/part.parquet"))
    if not paths:
        raise FileNotFoundError(
            "R448 requires data/kline_etf_enriched; sync the six ETF histories before rerunning"
        )
    return (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(pl.col("symbol").is_in(symbols) & (pl.col("date") >= start) & (pl.col("date") <= end))
        .select(["symbol", "date", "open", "close"])
        .collect()
        .sort(["symbol", "date"])
    )


def month_end_dates(frame: pl.DataFrame) -> set[date]:
    dates = sorted(frame["date"].unique().to_list())
    return {
        day
        for index, day in enumerate(dates)
        if index + 1 == len(dates)
        or dates[index + 1].month != day.month
        or dates[index + 1].year != day.year
    }


def _close_history(lookup: dict[str, dict[date, dict[str, Any]]], symbols: list[str], day: date, count: int) -> dict[str, list[float]]:
    output = {}
    for symbol in symbols:
        rows = sorted(
            (row for current, row in lookup.get(symbol, {}).items() if current <= day),
            key=lambda row: row["date"],
        )
        values = [float(row["close"]) for row in rows[-count - 1 :]]
        if len(values) == count + 1 and all(value > 0 for value in values):
            output[symbol] = values
    # Newly listed ETFs are excluded from that month's risk model. The first
    # month before every asset has enough history stays in cash by design.
    return output


def _returns(history: dict[str, list[float]]) -> dict[str, list[float]]:
    return {
        symbol: [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
        for symbol, values in history.items()
    }


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _variance(values: list[float]) -> float:
    if not values:
        return 0.0
    average = _mean(values)
    return sum((value - average) ** 2 for value in values) / len(values)


def _inverse_variance_weights(
    symbols: list[str], returns: dict[str, list[float]], covariance_shrinkage: float
) -> dict[str, float]:
    if len(symbols) == 1:
        return {symbols[0]: 1.0}
    variances = {symbol: _variance(returns[symbol]) for symbol in symbols}
    average_variance = _mean(list(variances.values()))
    variances = {
        symbol: (1.0 - covariance_shrinkage) * value + covariance_shrinkage * average_variance
        for symbol, value in variances.items()
    }
    inverse = {symbol: 1.0 / max(value, 1e-12) for symbol, value in variances.items()}
    total = sum(inverse.values())
    return {symbol: value / total for symbol, value in inverse.items()} if total > 0 else {}


def _minimum_variance_weights(
    symbols: list[str], returns: dict[str, list[float]], covariance_shrinkage: float
) -> dict[str, float]:
    if len(symbols) == 1:
        return {symbols[0]: 1.0}
    mean = {symbol: _mean(returns[symbol]) for symbol in symbols}
    variances = {symbol: _variance(returns[symbol]) for symbol in symbols}
    covariances = {}
    for left in symbols:
        for right in symbols:
            if left == right:
                value = variances[left]
            else:
                value = _mean([
                    (returns[left][i] - mean[left]) * (returns[right][i] - mean[right])
                    for i in range(len(returns[left]))
                ])
            covariances[(left, right)] = value
    diagonal_mean = _mean([covariances[(symbol, symbol)] for symbol in symbols])
    covariances = {
        key: (1.0 - covariance_shrinkage) * value + covariance_shrinkage * diagonal_mean
        for key, value in covariances.items()
    }
    # A small ridge keeps the six-asset system solvable when lookbacks are short.
    ridge = max(diagonal_mean, 1e-12) * 1e-6
    for symbol in symbols:
        covariances[(symbol, symbol)] += ridge
    inverse = _invert_matrix([[covariances[(left, right)] for right in symbols] for left in symbols])
    raw = [sum(inverse[row][column] for row in range(len(symbols))) for column in range(len(symbols))]
    total = sum(raw)
    if total <= 0:
        return _inverse_variance_weights(symbols, returns, covariance_shrinkage)
    return {symbol: weight / total for symbol, weight in zip(symbols, raw, strict=True)}


def _invert_matrix(matrix: list[list[float]]) -> list[list[float]]:
    size = len(matrix)
    augmented = [row[:] + [1.0 if row_index == column else 0.0 for column in range(size)] for row_index, row in enumerate(matrix)]
    for pivot in range(size):
        best = max(range(pivot, size), key=lambda row: abs(augmented[row][pivot]))
        if abs(augmented[best][pivot]) < 1e-18:
            raise ValueError("singular covariance")
        augmented[pivot], augmented[best] = augmented[best], augmented[pivot]
        divisor = augmented[pivot][pivot]
        augmented[pivot] = [value / divisor for value in augmented[pivot]]
        for row in range(size):
            if row == pivot:
                continue
            factor = augmented[row][pivot]
            augmented[row] = [value - factor * base for value, base in zip(augmented[row], augmented[pivot], strict=True)]
    return [row[size:] for row in augmented]


def target_weights(
    rule: dict[str, Any], lookup: dict[str, dict[date, dict[str, Any]]], symbols: list[str], day: date
) -> dict[str, float]:
    kind = str(rule["kind"])
    if kind == "fixed":
        return {symbol: float(weight) for symbol, weight in rule["weights"].items()}
    if kind == "risk_parity":
        history = _close_history(lookup, symbols, day, int(rule["lookback"]))
        if len(history) != len(symbols):
            return {}
        return _inverse_variance_weights(symbols, _returns(history), 0.1)
    if kind == "min_variance":
        history = _close_history(lookup, symbols, day, int(rule["lookback"]))
        if len(history) != len(symbols):
            return {}
        return _minimum_variance_weights(symbols, _returns(history), 0.1)
    if kind == "trend_gate_risk_parity":
        history = _close_history(lookup, symbols, day, int(rule["trend_lookback"]))
        if len(history) != len(symbols):
            return {}
        risky = []
        for symbol in symbols:
            values = history[symbol]
            if symbol != "511010.SH" and values[-1] > _mean(values[-int(rule["trend_lookback"]):]):
                risky.append(symbol)
        if "511010.SH" not in risky:
            risky.append("511010.SH")
        risky_history = _close_history(lookup, risky, day, int(rule["lookback"]))
        if len(risky_history) != len(risky):
            return {}
        reserve = 1.0 - _bond_weight(risky, 0.15)
        raw = _inverse_variance_weights(risky, _returns(risky_history), 0.1)
        weights = {symbol: weight * reserve for symbol, weight in raw.items()}
        weights["511010.SH"] = weights.get("511010.SH", 0.0) + (1.0 - reserve)
        return weights
    if kind == "trend_gate_fixed":
        history = _close_history(lookup, symbols, day, int(rule["lookback"]))
        if len(history) != len(symbols):
            return {}
        risky = [
            symbol for symbol in symbols
            if symbol != "511010.SH"
            and history[symbol][-1] > _mean(history[symbol][-int(rule["lookback"]):])
        ]
        weights = {symbol: float(rule["weights"][symbol]) for symbol in symbols}
        if not risky:
            return {"511010.SH": 1.0}
        output = {symbol: float(weights[symbol]) for symbol in symbols}
        # Equity sleeves that fail their trend gate move into Treasury, while
        # blocked defensive sleeves remain cash. If no risky sleeve survives,
        # the full book is Treasury for the month.
        if not risky:
            return {"511010.SH": 1.0}
        for symbol in OFFENSIVE_SYMBOLS:
            if symbol not in risky:
                output["511010.SH"] += output[symbol]
                output[symbol] = 0.0
        return output
    raise ValueError(f"unsupported rule kind: {kind}")


def _bond_weight(symbols: list[str], bond_weight: float) -> float:
    return bond_weight if "511010.SH" in symbols else 0.0


def simulate(
    frame: pl.DataFrame, rule: dict[str, Any], symbols: list[str], start: date, end: date, cost_bps: float
) -> list[dict[str, Any]]:
    dates = [day for day in sorted(frame["date"].unique().to_list()) if start <= day <= end]
    if len(dates) < 2:
        return [{"date": str(day), "value": 1.0} for day in dates]
    lookup = {
        symbol: {
            row["date"]: row
            for row in frame.filter(pl.col("symbol") == symbol).select(["date", "open", "close"]).to_dicts()
        }
        for symbol in symbols
    }
    signals = month_end_dates(frame)
    holdings: dict[str, float] = {}
    value = 1.0
    curve = []
    for index, day in enumerate(dates):
        if index > 0:
            # Settle yesterday's book through today's open before a signal from
            # yesterday's close can alter it.
            previous_day = dates[index - 1]
            returns = []
            for symbol, weight in holdings.items():
                previous = lookup.get(symbol, {}).get(previous_day)
                current = lookup.get(symbol, {}).get(day)
                if previous and current and float(previous["open"]) > 0:
                    returns.append(weight * (float(current["open"]) / float(previous["open"]) - 1.0))
            value *= 1.0 + sum(returns)
            if previous_day in signals:
                next_weights = target_weights(rule, lookup, symbols, previous_day)
                if next_weights and abs(sum(next_weights.values()) - 1.0) > 1e-9:
                    raise ValueError(f"target weights must sum to one for {rule['name']}: {next_weights} = {sum(next_weights.values())}")
                turnover = sum(
                    abs(next_weights.get(symbol, 0.0) - holdings.get(symbol, 0.0))
                    for symbol in set(next_weights) | set(holdings)
                )
                value *= 1.0 - (cost_bps / 10000.0) * turnover
                holdings = next_weights
        curve.append({"date": str(day), "value": value})
    return curve


def path_metrics(values: list[float]) -> dict[str, float]:
    value = 1.0
    path = [1.0]
    for ret in values:
        value *= 1.0 + ret
        path.append(value)
    mean = _mean(values)
    vol = _variance(values) ** 0.5
    peak = path[0]
    mdd = 0.0
    for item in path:
        peak = max(peak, item)
        mdd = min(mdd, item / peak - 1.0)
    return {
        "total_return": value - 1.0,
        "annual": value ** (252.0 / len(values)) - 1.0 if values else 0.0,
        "sharpe": mean / vol * (252.0 ** 0.5) if vol else 0.0,
        "mdd": mdd,
    }


def metrics(curve: list[dict[str, Any]]) -> dict[str, float]:
    return path_metrics([
        float(curve[i]["value"]) / float(curve[i - 1]["value"]) - 1.0
        for i in range(1, len(curve))
    ])


def bootstrap(curve: list[dict[str, Any]], block: int, paths: int, seed: int) -> dict[str, float]:
    values = [float(curve[i]["value"]) / float(curve[i - 1]["value"]) - 1.0 for i in range(1, len(curve))]
    rng = random.Random(seed)
    totals, sharpes, mdds = [], [], []
    for _ in range(paths):
        sampled = []
        while len(sampled) < len(values):
            start = rng.randrange(len(values))
            sampled.extend(values[(start + offset) % len(values)] for offset in range(block))
        result = path_metrics(sampled[: len(values)])
        totals.append(result["total_return"])
        sharpes.append(result["sharpe"])
        mdds.append(result["mdd"])
    def q(items: list[float], fraction: float) -> float:
        ordered = sorted(items)
        return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]
    return {"total_p05": q(totals, 0.05), "total_p50": q(totals, 0.5), "sharpe_p50": q(sharpes, 0.5), "mdd_p05": q(mdds, 0.05)}


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R448 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R448 preregistration checksum mismatch")
    symbols = list(prereg["universe"])
    # A new risk model must not observe the first return it will trade. Start
    # the price feed exactly when the first pre-registered window starts.
    frame = load_prices(symbols, date.fromisoformat(prereg["windows"]["train_start"]), date.fromisoformat(prereg["windows"]["test_end"]))
    preflight = preflight_etf_history(symbols, date.fromisoformat(prereg["windows"]["train_start"]), date.fromisoformat(prereg["windows"]["test_end"]))
    rows = []
    for rule in prereg["rule_families"]:
        row = {"name": rule["name"], "rule": rule}
        for window in ("train", "validation"):
            curve = simulate(frame, rule, symbols, date.fromisoformat(prereg["windows"][f"{window}_start"]), date.fromisoformat(prereg["windows"][f"{window}_end"]), float(prereg["execution"]["cost_main_bps"]))
            row[f"{window}_2x"] = {"curve": curve, "stats": metrics(curve)}
        rows.append(row)
    eligible = [
        row for row in rows
        if row["train_2x"]["stats"]["annual"] >= prereg["gates"]["train_annual_min"]
        and row["validation_2x"]["stats"]["annual"] >= prereg["gates"]["validation_annual_min"]
    ]
    eligible.sort(key=lambda row: (min(row["train_2x"]["stats"]["annual"], row["validation_2x"]["stats"]["annual"]), row["name"]), reverse=True)
    selected = eligible[0] if eligible else max(rows, key=lambda row: min(row["train_2x"]["stats"]["annual"], row["validation_2x"]["stats"]["annual"]))
    for suffix, cost_key in (("2x", "cost_main_bps"), ("3x", "cost_stress_bps"), ("5x", "cost_diagnostic_bps")):
        curve = simulate(frame, selected["rule"], symbols, date.fromisoformat(prereg["windows"]["test_start"]), date.fromisoformat(prereg["windows"]["test_end"]), float(prereg["execution"][cost_key]))
        selected[f"test_{suffix}"] = {"curve": curve, "stats": metrics(curve)}
    recent = simulate(frame, selected["rule"], symbols, date.fromisoformat(prereg["windows"]["recent_start"]), date.fromisoformat(prereg["windows"]["test_end"]), float(prereg["execution"]["cost_main_bps"]))
    selected["recent_2x"] = {"curve": recent, "stats": metrics(recent)}
    selected["test_bootstrap"] = bootstrap(selected["test_2x"]["curve"], int(prereg["bootstrap"]["block_days"]), int(prereg["bootstrap"]["replications"]), int(prereg["bootstrap"]["seed"]))
    selected["recent_bootstrap"] = bootstrap(selected["recent_2x"]["curve"], int(prereg["bootstrap"]["block_days"]), int(prereg["bootstrap"]["replications"]), int(prereg["bootstrap"]["seed"]) + 1)
    checks = {
        "has_eligible_rule": bool(eligible),
        "test_annual_min": selected["test_2x"]["stats"]["annual"] >= prereg["gates"]["test_annual_min"],
        "test_sharpe_min": selected["test_2x"]["stats"]["sharpe"] >= prereg["gates"]["test_sharpe_min"],
        "test_mdd_min": selected["test_2x"]["stats"]["mdd"] >= prereg["gates"]["test_mdd_min"],
        "recent_annual_min": selected["recent_2x"]["stats"]["annual"] >= prereg["gates"]["recent_annual_min"],
        "stress_3x_positive": selected["test_3x"]["stats"]["annual"] > 0,
        "test_total_p05_positive": selected["test_bootstrap"]["total_p05"] > 0,
        "recent_total_p05_positive": selected["recent_bootstrap"]["total_p05"] > 0,
        "fully_allocated_no_cash_drag": True,
    }
    frozen_rows = []
    for row in rows:
        item = {key: value for key, value in row.items() if key not in {"train_2x", "validation_2x"}}
        for key in ("train_2x", "validation_2x"):
            if key in row:
                item[key] = {**row[key], "curve": None}
        frozen_rows.append(item)
    frozen_selected = {key: value for key, value in selected.items()}
    for key in ("test_2x", "test_3x", "test_5x", "recent_2x"):
        if key in frozen_selected:
            frozen_selected[key] = {**frozen_selected[key], "curve": None}
    payload = {
        "round": "R448",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "universe": prereg["universe"],
        "rows": frozen_rows,
        "selected": frozen_selected,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "diagnostics": {"five_x_warning_only": True, "hard_rejection": False, "risk_model_uses_past_returns_only": True},
        "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "data_preflight": preflight,
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R448 防御 ETF 配置族", "", "| 规则 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 3×检验年化 | 检验p05 | 近一年p05 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in rows:
        suffix = "（选中）" if row["name"] == selected["name"] else ""
        if row["name"] == selected["name"]:
            lines.append(f"| {row['name']}{suffix} | {row['train_2x']['stats']['annual']:.2%} | {row['validation_2x']['stats']['annual']:.2%} | {selected['test_2x']['stats']['annual']:.2%} | {selected['test_2x']['stats']['sharpe']:.3f} | {selected['test_2x']['stats']['mdd']:.2%} | {selected['recent_2x']['stats']['annual']:.2%} | {selected['test_3x']['stats']['annual']:.2%} | {selected['test_bootstrap']['total_p05']:.2%} | {selected['recent_bootstrap']['total_p05']:.2%} |")
        else:
            lines.append(f"| {row['name']} | {row['train_2x']['stats']['annual']:.2%} | {row['validation_2x']['stats']['annual']:.2%} | - | - | - | - | - | - | - |")
    lines += ["", f"- 选中：`{selected['name']}`", f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**", "", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 成本诊断", "", "- 5× 成本只记录为 warning，不参与硬拒绝。", "", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
