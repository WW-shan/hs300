#!/usr/bin/env python3
"""R486: drawdown-cluster diagnosis for the frozen R484 cap=1 sleeve."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r486_drawdown_tail_diagnosis_preregistration.json"
R484 = E / "r484_daily_entry_batch_cap_2026-11-22.json"
OUTPUT = E / "r486_drawdown_tail_diagnosis_2026-11-22.json"
RESULT = E / "r486_drawdown_tail_diagnosis_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R486 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def drawdown_episodes(curve: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not curve:
        return []
    peak = float(curve[0]["value"])
    peak_date = str(curve[0]["date"])[:10]
    in_drawdown = False
    trough = peak
    trough_date = peak_date
    episodes = []
    for item in curve[1:]:
        value = float(item["value"])
        day = str(item["date"])[:10]
        if value >= peak:
            if in_drawdown:
                episodes.append(
                    {
                        "start": peak_date,
                        "trough": trough_date,
                        "end": day,
                        "max_drawdown": round(trough / peak - 1.0, 6),
                        "recovery_days": len(
                            [x for x in curve if peak_date <= str(x["date"])[:10] <= day]
                        ),
                    }
                )
                in_drawdown = False
            peak = value
            peak_date = day
            trough = peak
            trough_date = day
        else:
            in_drawdown = True
            if value < trough:
                trough = value
                trough_date = day
    if in_drawdown:
        episodes.append(
            {
                "start": peak_date,
                "trough": trough_date,
                "end": str(curve[-1]["date"])[:10],
                "max_drawdown": round(trough / peak - 1.0, 6),
                "recovery_days": None,
            }
        )
    return sorted(episodes, key=lambda item: item["max_drawdown"])


def quantiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    def q(fraction: float) -> float:
        return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]
    return {
        "min": ordered[0],
        "p01": q(0.01),
        "p05": q(0.05),
        "p50": q(0.50),
        "p95": q(0.95),
        "max": ordered[-1],
    }


def rolling_returns(curve: list[dict[str, Any]], window: int) -> list[dict[str, Any]]:
    values = [(str(item["date"])[:10], float(item["value"])) for item in curve]
    return [
        {"date": values[index][0], "return": round(values[index][1] / values[index - window][1] - 1.0, 6)}
        for index in range(window, len(values))
    ]


def recover_curves() -> dict[str, list[dict[str, Any]]]:
    r484 = load_module(ROOT / "scripts" / "research_r484_daily_entry_batch_cap.py", "r486_r484")
    r468 = load_module(ROOT / "scripts" / "research_r468_operating_efficiency_sleeve.py", "r486_r468")
    r464 = r468.load_module(ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r486_r464")
    p5 = r464.load_p5()
    panel, _ignored = r464.build_panel(p5)
    from app.hs300.current import current_symbols
    price_panel = p5.load_candidate_panel(ROOT / "data", current_symbols(), [])[0]
    price_panel = price_panel.with_columns((pl.col("close") / pl.col("close").shift(60).over("symbol") - 1.0).alias("momentum_60d"))
    panel = panel.join(price_panel.select(["symbol", "date", "momentum_60d"]), on=["symbol", "date"], how="left").sort(["symbol", "date"])
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    snapshot = load_fundamental_snapshot(ROOT / "data")
    selected = json.loads(R484.read_text(encoding="utf-8"))["selected"]
    missing = [factor for factor in selected["weights"] if factor not in panel.columns]
    if missing:
        panel = attach_fundamental_factors(panel, snapshot, missing)
    symbols = sorted(panel["symbol"].unique().to_list())
    service = p5.portfolio._service()
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    windows = prereg["windows"]
    curves = {}
    for window in ("test", "recent"):
        result = r484.run_sample(p5, service, panel, symbols, selected, start=windows[f"{window}_start"], end=windows["end"], cost_label="2x")
        curves[window] = result["curve"]
    return curves


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    verify_sidecar(R484)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    curves = recover_curves()
    payload_windows = {}
    for window, curve in curves.items():
        daily = [float(curve[i]["value"]) / float(curve[i - 1]["value"]) - 1.0 for i in range(1, len(curve))]
        rolling = rolling_returns(curve, 20)
        payload_windows[window] = {
            "daily_return_quantiles": quantiles(daily),
            "drawdown_episodes": drawdown_episodes(curve),
            "worst_rolling_20d": sorted(rolling, key=lambda item: item["return"])[: int(prereg["diagnostics"]["rolling_20d_worst"])],
        }
    payload = {
        "round": "R486",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_r484_sha256": sha(R484),
        "windows": payload_windows,
        "decision": prereg["output_policy"],
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R486 R484 回撤簇尾部诊断",
        "",
        "本轮只诊断 R484 cap=1 的日收益、回撤区间和 20 日滚动窗口，不重新选样。",
        "",
        "## 日收益分位",
        "",
        "| 窗口 | min | p01 | p05 | p50 | p95 | max |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for window, item in payload_windows.items():
        q = item["daily_return_quantiles"]
        lines.append(f"| {window} | {q['min']:.2%} | {q['p01']:.2%} | {q['p05']:.2%} | {q['p50']:.2%} | {q['p95']:.2%} | {q['max']:.2%} |")
    lines += ["", "## 最深回撤区间", ""]
    for window, item in payload_windows.items():
        lines.append(f"### {window}")
        for episode in item["drawdown_episodes"][:5]:
            lines.append(f"- {episode['start']} → {episode['end']}，最大回撤 {episode['max_drawdown']:.2%}")
    lines += ["", "## 最差 20 日滚动窗口", ""]
    for window, item in payload_windows.items():
        lines.append(f"### {window}")
        for row in item["worst_rolling_20d"][:10]:
            lines.append(f"- {row['date']}：{row['return']:.2%}")
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
