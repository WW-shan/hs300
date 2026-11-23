#!/usr/bin/env python3
"""R521: Shenwan industry-index momentum with parameter Monte Carlo gates."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r521_industry_index_momentum_preregistration.json"
OUTPUT = E / "r521_industry_index_momentum_2026-11-23.json"
RESULT = E / "r521_industry_index_momentum_2026-11-23.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_prereg() -> dict[str, Any]:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    if not sidecar.exists() or sha(PREREG) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R521 preregistration checksum mismatch")
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R521":
        raise RuntimeError("R521 round mismatch")
    return payload


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R521 output exists; refusing overwrite: {path.name}")


def window_bounds(windows: dict[str, str], name: str) -> tuple[date, date]:
    end_key = f"{name}_end" if f"{name}_end" in windows else "test_end"
    return (
        date.fromisoformat(windows[f"{name}_start"]),
        date.fromisoformat(windows[end_key]),
    )


def is_month_end_signal(dates: list[date], index: int) -> bool:
    if index < 0 or index >= len(dates):
        return False
    if index + 1 >= len(dates):
        return False
    current = dates[index]
    following = dates[index + 1]
    return (following.year, following.month) != (current.year, current.month)


def clip_history(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
    return frame.loc[(dates >= start) & (dates <= end)].copy()


def warmup_start(start: date, *, max_lookback: int) -> date:
    return start - pd.Timedelta(days=int(max_lookback) + 30)


def max_gross_exposure(weights: dict[str, float]) -> float:
    return float(sum(abs(value) for value in weights.values()))


def turnover_cost(
    *, previous: dict[str, float], current: dict[str, float], cost_bps: float
) -> float:
    symbols = set(previous) | set(current)
    turnover = sum(abs(current.get(symbol, 0.0) - previous.get(symbol, 0.0)) for symbol in symbols)
    return float(turnover) * float(cost_bps) / 10000.0


def select_from_fit_windows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [
        row
        for row in rows
        if row["train"]["annual"] >= 0.0 and row["validation"]["annual"] >= 0.0
    ]
    pool = eligible or rows
    return max(
        pool,
        key=lambda row: (
            min(row["train"]["annual"], row["validation"]["annual"]),
            row.get("validation", {}).get("sharpe", float("-inf")),
            -row.get("lookback", 0),
            -row.get("top_n", 0),
        ),
    )



def _stats_from_values(values: list[float]) -> dict[str, float | None]:
    if len(values) < 2:
        return {"annual": None, "sharpe": None, "mdd": None, "total": 0.0, "days": len(values)}
    curve = np.cumprod(1.0 + np.asarray(values, dtype=float))
    days = len(values)
    annual = float(curve[-1] ** (252.0 / days) - 1.0) if curve[-1] > 0 else -1.0
    mean = float(np.mean(values))
    vol = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
    sharpe = float(mean / vol * math.sqrt(252.0)) if vol > 0 else None
    peak = np.maximum.accumulate(np.r_[1.0, curve])
    mdd = float(np.min(np.r_[1.0, curve] / peak - 1.0))
    return {
        "annual": round(annual, 8),
        "sharpe": round(sharpe, 8) if sharpe is not None else None,
        "mdd": round(mdd, 8),
        "total": round(float(curve[-1] - 1.0), 8),
        "days": days,
    }


def simulate(
    prices: pd.DataFrame,
    *,
    start: date,
    end: date,
    lookback: int,
    score: str,
    top_n: int,
    cost_bps: float,
) -> dict[str, Any]:
    """Simulate monthly top-N industry momentum with next-open execution.

    Prices before ``start`` are warm-up only, never counted as strategy returns.
    Signals use closes through the completed month end; entries occur at the
    next trading day's open.
    """
    frame = prices.sort_values(["date", "code"]).reset_index(drop=True)
    if frame.empty:
        return _empty_simulation()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    open_matrix = frame.pivot(index="date", columns="code", values="open").sort_index()
    close_matrix = frame.pivot(index="date", columns="code", values="close").sort_index()
    dates = [timestamp.date() for timestamp in open_matrix.index]
    open_values = open_matrix.to_numpy(dtype=float)
    close_values = close_matrix.to_numpy(dtype=float)
    returns = np.zeros_like(open_values, dtype=float)
    returns[1:] = open_values[1:] / open_values[:-1] - 1.0
    returns[~np.isfinite(returns)] = 0.0

    # Precompute every signal date once. A month-end is a completed day whose
    # next available trading day belongs to a different month.
    signal_indices = [
        index
        for index in range(len(dates) - 1)
        if (dates[index + 1].year, dates[index + 1].month)
        != (dates[index].year, dates[index].month)
    ]
    momentum_values = np.full_like(close_values, np.nan, dtype=float)
    if lookback < len(dates):
        momentum_values[lookback:] = close_values[lookback:] / close_values[:-lookback] - 1.0
    score_matrix = momentum_values.copy()
    if score == "risk_adjusted_momentum":
        daily = np.zeros_like(close_values, dtype=float)
        daily[1:] = close_values[1:] / close_values[:-1] - 1.0
        vol = np.full_like(close_values, np.nan, dtype=float)
        if lookback > 1:
            rolling = pd.DataFrame(daily).rolling(lookback, min_periods=lookback).std(ddof=1)
            vol = rolling.to_numpy(dtype=float)
        score_matrix = np.divide(
            score_matrix,
            vol,
            out=np.full_like(score_matrix, -np.inf),
            where=np.isfinite(vol) & (vol > 0),
        )

    holdings = np.zeros(close_values.shape[1], dtype=float)
    value = 1.0
    daily: list[float] = []
    points: list[dict[str, float | str]] = []
    turnover_total = 0.0
    started = False
    entry_day: date | None = None
    pending_signal: int | None = None
    for index, day in enumerate(dates):
        # Price existing holdings from the prior open to this open.
        if started and index > 0 and day >= start:
            gross = float(np.dot(holdings, returns[index]))
            value *= 1.0 + gross
            daily.append(gross)
            points.append({"date": str(day), "value": round(value, 8)})

        # Execute a signal generated at the previous close at today's open.
        if pending_signal is not None:
            row = score_matrix[pending_signal]
            valid = np.isfinite(row)
            chosen = np.argsort(-np.where(valid, row, -np.inf), kind="stable")[:top_n]
            chosen = chosen[valid[chosen]]
            next_weights = np.zeros_like(holdings)
            if len(chosen) == top_n:
                next_weights[chosen] = 1.0 / top_n
            turnover = float(np.abs(next_weights - holdings).sum())
            turnover_total += turnover
            cost = turnover * float(cost_bps) / 10000.0
            value *= 1.0 - cost
            holdings = next_weights
            if not started and holdings.sum() > 0:
                started = True
                entry_day = day
                points.append({"date": str(day), "value": round(value, 8)})
            if day >= start:
                if daily:
                    daily[-1] -= cost
                elif started:
                    daily.append(-cost)
                    points.append({"date": str(day), "value": round(value, 8)})
            pending_signal = None

        if index in signal_indices:
            pending_signal = index

    if not started or entry_day is None:
        return _empty_simulation()
    points = [
        point for point in points if date.fromisoformat(str(point["date"])) >= entry_day
    ]
    if not points:
        return _empty_simulation()
    return {
        "curve": points,
        "window_returns": daily,
        "stats": _stats_from_values(daily),
        "turnover": round(turnover_total, 8),
        "first_entry": str(entry_day),
    }


def _empty_simulation() -> dict[str, Any]:
    return {
        "curve": [],
        "window_returns": [],
        "stats": _stats_from_values([]),
        "turnover": 0.0,
        "first_entry": None,
    }


def block_bootstrap_total(
    values: list[float], *, block: int, paths: int, seed: int
) -> dict[str, float | int]:
    array = np.asarray([value for value in values if value is not None and np.isfinite(value)], dtype=float)
    if array.size == 0:
        raise ValueError("bootstrap values are empty")
    block = min(int(block), int(array.size))
    blocks = int(np.ceil(array.size / block))
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, array.size - block + 1, size=(paths, blocks))
    offsets = np.arange(block)
    samples = array[starts[..., None] + offsets].reshape(paths, -1)[:, : array.size]
    totals = np.prod(1.0 + samples, axis=1) - 1.0
    return {
        "total_p05": round(float(np.quantile(totals, 0.05)), 8),
        "total_p50": round(float(np.quantile(totals, 0.50)), 8),
        "total_p95": round(float(np.quantile(totals, 0.95)), 8),
        "n": int(array.size),
        "block": int(block),
        "paths": int(paths),
    }



def assign_q_values(rows: list[dict[str, Any]], *, key: str) -> None:
    from app.backtest import stats_v2
    from app.factor_research.signal_scan import block_bootstrap_mean_ci

    pvalues = []
    for row in rows:
        returns = row["full_returns"]
        boot = block_bootstrap_mean_ci(
            returns,
            block_size=20,
            n_bootstrap=1000,
            seed=521,
        )
        se = max(1e-12, (boot["ci_high"] - boot["ci_low"]) / 3.92)
        pvalues.append(stats_v2.normal_two_sided_p(float(boot["mean"] / se)))
    qvalues = stats_v2.bh_fdr_qvalues(pvalues)
    for row, q in zip(rows, qvalues, strict=True):
        row[key] = round(float(q), 8) if q is not None else None


def parameter_plateau(rows: list[dict[str, Any]]) -> dict[str, bool]:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["score"], row["lookback"]), []).append(row)
    status: dict[str, bool] = {}
    for group in grouped.values():
        ordered = sorted(group, key=lambda row: row["top_n"])
        for index, row in enumerate(ordered):
            neighbours = ordered[max(0, index - 1) : index] + ordered[index + 1 : index + 2]
            pass_neighbour = all(
                neighbour["train"]["annual"] > 0 and neighbour["validation"]["annual"] > 0
                for neighbour in neighbours
            )
            status[row["name"]] = bool(
                len(neighbours) >= 2
                and pass_neighbour
                and row["train"]["annual"] > 0
                and row["validation"]["annual"] > 0
            )
    return status


def run_grid(prices: pd.DataFrame, prereg: dict[str, Any]) -> list[dict[str, Any]]:
    windows = prereg["windows"]
    max_lookback = max(int(value) for value in prereg["signal"]["lookbacks"])
    rows: list[dict[str, Any]] = []
    for score in prereg["signal"]["scores"]:
        for lookback in prereg["signal"]["lookbacks"]:
            for top_n in prereg["signal"]["top_n"]:
                name = f"{score}_{lookback}_{top_n}"
                row: dict[str, Any] = {
                    "name": name,
                    "score": score,
                    "lookback": lookback,
                    "top_n": top_n,
                }
                for window in ("train", "validation", "test", "recent"):
                    window_start, window_end = window_bounds(windows, window)
                    history_start = warmup_start(window_start, max_lookback=max_lookback)
                    result = simulate(
                        clip_history(prices, history_start, window_end),
                        start=window_start,
                        end=window_end,
                        lookback=lookback,
                        score=score,
                        top_n=top_n,
                        cost_bps=float(prereg["execution"]["main_cost_bps"]),
                    )
                    row[window] = result["stats"]
                    row[f"{window}_curve"] = result["curve"]
                    row[f"{window}_returns"] = result["window_returns"]
                full_start = date.fromisoformat(windows["train_start"])
                full_end = date.fromisoformat(windows["test_end"])
                full_result = simulate(
                    clip_history(
                        prices,
                        warmup_start(full_start, max_lookback=max_lookback),
                        full_end,
                    ),
                    start=full_start,
                    end=full_end,
                    lookback=lookback,
                    score=score,
                    top_n=top_n,
                    cost_bps=float(prereg["execution"]["main_cost_bps"]),
                )
                row["full_curve"] = full_result["curve"]
                row["full_returns"] = full_result["window_returns"]
                test_start = date.fromisoformat(windows["test_start"])
                test_end = date.fromisoformat(windows["test_end"])
                stress_result = simulate(
                    clip_history(
                        prices,
                        warmup_start(test_start, max_lookback=max_lookback),
                        test_end,
                    ),
                    start=test_start,
                    end=test_end,
                    lookback=lookback,
                    score=score,
                    top_n=top_n,
                    cost_bps=float(prereg["execution"]["stress_cost_bps"]),
                )
                warning_result = simulate(
                    clip_history(
                        prices,
                        warmup_start(test_start, max_lookback=max_lookback),
                        test_end,
                    ),
                    start=test_start,
                    end=test_end,
                    lookback=lookback,
                    score=score,
                    top_n=top_n,
                    cost_bps=float(prereg["execution"]["warning_cost_bps"]),
                )
                row["stress_3x"] = stress_result["stats"]
                row["warning_5x"] = warning_result["stats"]
                rows.append(row)
    return rows


def evaluate_selector_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep test/recent out of the eligible pool until a fit-window choice exists."""
    return [
        {
            "name": row["name"],
            "lookback": row["lookback"],
            "top_n": row["top_n"],
            "train": row["train"],
            "validation": row["validation"],
            "test": row["test"],
            "recent": row["recent"],
            "test_returns": row["test_returns"],
            "recent_returns": row["recent_returns"],
        }
        for row in rows
    ]


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# R521 申万行业指数动量与参数蒙特卡洛",
        "",
        f"- 参数样本：{payload['grid_count']}",
        f"- 行业数：{payload['universe_count']}",
        f"- 选中：`{payload['selected']['name']}`",
        f"- 判定：**{'通过' if payload['gate']['pass'] else '拒绝'}**",
        "",
        "## 选中参数",
        "",
        f"- 回看：{payload['selected']['lookback']} 日",
        f"- 打分：{payload['selected']['score']}",
        f"- 持仓：{payload['selected']['top_n']}",
        f"- 训练年化：{payload['selected']['train']['annual']:.2%}",
        f"- 验证年化：{payload['selected']['validation']['annual']:.2%}",
        f"- 检验年化：{payload['selected']['test']['annual']:.2%}",
        f"- 检验 Sharpe：{payload['selected']['test']['sharpe']:.3f}",
        f"- 检验 MDD：{payload['selected']['test']['mdd']:.2%}",
        f"- 近一年年化：{payload['selected']['recent']['annual']:.2%}",
        f"- 检验 p05：{payload['selected']['test_bootstrap']['total_p05']:.2%}",
        f"- 近一年 p05：{payload['selected']['recent_bootstrap']['total_p05']:.2%}",
        "",
        "## 门槛",
        "",
    ]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in payload["gate"]["checks"].items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in payload["known_limits"]] + [""]
    return "\n".join(lines)


def main() -> int:
    ensure_outputs_absent()
    prereg = verify_prereg()
    import akshare as ak

    universe_rows, _ = load_qualified_universe(ak, prereg)
    prices = fetch_prices(ak, universe_rows, prereg)
    rows = run_grid(prices, prereg)
    expected_grid = (
        len(prereg["signal"]["scores"])
        * len(prereg["signal"]["lookbacks"])
        * len(prereg["signal"]["top_n"])
    )
    if len(rows) != expected_grid:
        raise RuntimeError(f"R521 grid incomplete: {len(rows)} != {expected_grid}")
    if any(row["train"]["days"] == 0 for row in rows):
        raise RuntimeError("R521 parameter has no tradable train observations")
    plateau = parameter_plateau(rows)
    for row in rows:
        row["robust_plateau"] = plateau.get(row["name"], False)
    selector_rows = evaluate_selector_rows(rows)
    selected = select_from_fit_windows(selector_rows)
    selected_full = next(row for row in rows if row["name"] == selected["name"])
    selected["score"] = selected_full["score"]
    selected["test_bootstrap"] = block_bootstrap_total(
        selected["test_returns"],
        block=int(prereg["bootstrap"]["block_days"]),
        paths=int(prereg["bootstrap"]["replications"]),
        seed=int(prereg["bootstrap"]["seed"]),
    )
    selected["recent_bootstrap"] = block_bootstrap_total(
        selected["recent_returns"],
        block=int(prereg["bootstrap"]["block_days"]),
        paths=int(prereg["bootstrap"]["replications"]),
        seed=int(prereg["bootstrap"]["seed"]) + 1,
    )
    selected["stress_3x"] = selected_full["stress_3x"]
    selected["warning_5x"] = selected_full["warning_5x"]
    selected["robust_plateau"] = bool(selected_full["robust_plateau"])
    assign_q_values(rows, key="q_value")
    selected["q_value"] = next(row["q_value"] for row in rows if row["name"] == selected["name"])
    gates = prereg["gates"]
    checks = {
        "train_annual_min": selected["train"]["annual"] >= gates["train_annual_min"],
        "validation_annual_min": selected["validation"]["annual"] >= gates["validation_annual_min"],
        "test_annual_min": selected["test"]["annual"] >= gates["test_annual_min"],
        "test_sharpe_min": selected["test"]["sharpe"] is not None
        and selected["test"]["sharpe"] >= gates["test_sharpe_min"],
        "test_mdd_min": selected["test"]["mdd"] >= gates["test_mdd_min"],
        "recent_annual_min": selected["recent"]["annual"] >= gates["recent_annual_min"],
        "recent_total_p05_positive": selected["recent_bootstrap"]["total_p05"] > 0,
        "test_total_p05_positive": selected["test_bootstrap"]["total_p05"] > 0,
        "stress_3x_positive": selected["stress_3x"]["annual"] > 0,
        "parameter_plateau": selected["robust_plateau"],
        "fdr_q_max": selected["q_value"] is not None and selected["q_value"] <= gates["fdr_q_max"],
    }
    payload = {
        "round": "R521",
        "research_type": "external_data_diagnostic_simulation",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "grid_count": len(rows),
        "universe_count": len(universe_rows),
        "grid": [{key: value for key, value in row.items() if not key.endswith("_curve")} for row in rows],
        "selected": selected,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "decision": {
            **prereg["output_policy"],
            "status": "watch_candidate" if all(checks.values()) else "rejected",
        },
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    text = render_markdown(payload)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii"
    )
    print(text)
    return 0


def load_qualified_universe(ak: Any, prereg: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    catalog = ak.sw_index_first_info()
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for item in catalog.to_dict("records"):
        code = str(item["行业代码"]).replace(".SI", "").replace(".SW", "")
        try:
            history = ak.index_hist_sw(symbol=code, period="day")
            frame = pd.DataFrame(history)
            dates = pd.to_datetime(frame["日期"], errors="coerce")
            amounts = pd.to_numeric(frame["成交额"], errors="coerce")
            start = dates.min().date().isoformat() if dates.notna().any() else None
            end = dates.max().date().isoformat() if dates.notna().any() else None
            ok = bool(
                start is not None
                and end is not None
                and start <= prereg["universe_rule"]["require_start_on_or_before"]
                and end >= prereg["universe_rule"]["require_end_on_or_after"]
                and len(frame)
                and ((amounts > 0) & amounts.notna()).all()
            )
            if ok:
                rows.append({"code": code, "name": str(item["行业名称"])})
        except Exception as exc:  # noqa: BLE001 - isolate one provider member
            failures.append(f"{code}:{type(exc).__name__}: {exc}")
    if len(rows) < prereg["universe_rule"]["minimum_long_history_industries"]:
        raise RuntimeError(f"insufficient industry universe: {len(rows)}; failures={failures}")
    return rows, failures


def fetch_prices(ak: Any, universe: list[dict[str, Any]], prereg: dict[str, Any]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for item in universe:
        raw = pd.DataFrame(ak.index_hist_sw(symbol=item["code"], period="day"))
        raw = raw.assign(
            code=item["code"],
            date=pd.to_datetime(raw["日期"], errors="coerce"),
            open=pd.to_numeric(raw["开盘"], errors="coerce"),
            close=pd.to_numeric(raw["收盘"], errors="coerce"),
        ).loc[:, ["code", "date", "open", "close"]]
        frames.append(raw)
    prices = pd.concat(frames, ignore_index=True).dropna(subset=["date", "open", "close"])
    dates = prices["date"].dt.date
    warmup_start = date(2017, 1, 1)
    if dates.min() > warmup_start:
        raise RuntimeError("R521 requires pre-2018 warm-up history")
    return prices.sort_values(["date", "code"]).reset_index(drop=True)


if __name__ == "__main__":
    raise SystemExit(main())
