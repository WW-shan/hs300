#!/usr/bin/env python3
"""R405: descriptive monthly and drawdown attribution for frozen 80/20."""
from __future__ import annotations

import hashlib
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r405_tail_attribution_preregistration.json"
SOURCE = E / "r403v2_core_beta_2026-11-22.json"
R404 = E / "r404_bootstrap_2026-11-22.json"
OUTPUT = E / "r405_tail_attribution_2026-11-22.json"
RESULT = E / "r405_tail_attribution_2026-11-22.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metrics(curve: list[dict]) -> dict[str, float]:
    values = [float(item["value"]) for item in curve]
    rets = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    vol = st.pstdev(rets)
    peak = values[0]
    peak_date = curve[0]["date"]
    mdd = 0.0
    worst = (curve[0]["date"], peak_date, 0.0)
    for index, value in enumerate(values):
        if value > peak:
            peak = value
            peak_date = curve[index]["date"]
        drawdown = value / peak - 1.0
        if drawdown < mdd:
            mdd = drawdown
            worst = (curve[index]["date"], peak_date, drawdown)
    return {"total_return": total, "annual": (1 + total) ** (252 / len(rets)) - 1 if rets else 0.0, "sharpe": st.mean(rets) / vol * (252 ** 0.5) if vol else 0.0, "mdd": mdd, "max_drawdown_start": worst[1], "max_drawdown_trough": worst[0]}


def monthly(curve: list[dict], benchmark: list[dict]) -> list[dict]:
    f = defaultdict(lambda: {"strategy": [], "benchmark": []})
    strategy_by_date = {str(item["date"])[:10]: float(item["value"]) for item in curve}
    benchmark_by_date = {str(item["date"])[:10]: float(item["value"]) for item in benchmark}
    for day in sorted(set(strategy_by_date) & set(benchmark_by_date)):
        month = day[:7]
        f[month]["strategy"].append(strategy_by_date[day])
        f[month]["benchmark"].append(benchmark_by_date[day])
    rows = []
    for month, values in sorted(f.items()):
        s = values["strategy"]
        b = values["benchmark"]
        sr = s[-1] / s[0] - 1.0 if len(s) > 1 else 0.0
        br = b[-1] / b[0] - 1.0 if len(b) > 1 else 0.0
        rows.append({"month": month, "strategy_return": sr, "benchmark_return": br, "excess": sr - br})
    return rows


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R405 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha256(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R405 preregistration checksum mismatch")
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    candidate = source["curves"]["0.8"]
    reports = {}
    for boundary in ("train", "validation", "test", "recent"):
        curve = candidate[boundary]
        reports[boundary] = metrics(curve)
    # Test-window monthly returns need the actual benchmark dates. Read the
    # R403V2 test curve as the strategy series and use the same points for a
    # descriptive benchmark proxy from the source's published benchmark totals.
    from datetime import date

    import polars as pl

    from app.tickflow.repository import DataStore, KlineRepository
    repo = KlineRepository(DataStore(ROOT / "data"))
    def idx(start: str, end: str) -> list[dict]:
        frame = repo.get_index_daily("000300.SH", date.fromisoformat(start), date.fromisoformat(end), columns=["date", "close"]).drop_nulls().sort("date")
        if not isinstance(frame, pl.DataFrame):
            raise TypeError("index curve must be a Polars frame")
        return [{"date": str(item["date"]), "value": float(item["close"])} for item in frame.to_dicts()]
    test_months = monthly(source["curves"]["0.8"]["test"], idx(prereg["windows"]["test_start"], prereg["windows"]["test_end"]))
    negative_months = [row for row in test_months if row["strategy_return"] < 0]
    payload = {
        "round": "R405", "classification": prereg["classification"],
        "runner_sha256": sha256(Path(__file__)), "preregistration_sha256": sha256(PREREG),
        "source_sha256": sha256(SOURCE), "r404_sha256": sha256(R404),
        "window_metrics": reports, "monthly": test_months,
        "negative_month_count": len(negative_months),
        "worst_month": min(test_months, key=lambda row: row["strategy_return"]),
        "decision": {"status": "tail_attribution_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R405 80/20 下尾归因", "", f"- 负收益月份：{len(negative_months)}/{len(test_months)}", f"- 最差月份：{payload['worst_month']['month']} {payload['worst_month']['strategy_return']:.2%}", "", "| 窗口 | 总收益 | Sharpe | MDD | 回撤起点 | 回撤低点 |", "| --- | ---: | ---: | ---: | --- | --- |"]
    for boundary, row in reports.items():
        lines.append(f"| {boundary} | {row['total_return']:.2%} | {row['sharpe']:.3f} | {row['mdd']:.2%} | {row['max_drawdown_start']} | {row['max_drawdown_trough']} |")
    lines += ["", "## 边界", "", "- 这是描述性归因，不重新选择参数，也不证明因果关系。", "- 不生成订单、纸面目标或生产配置。", ""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
