#!/usr/bin/env python3
"""R520: verify external industry-index history feasibility."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r520_industry_index_feasibility_preregistration.json"
OUTPUT = E / "r520_industry_index_feasibility_2026-11-22.json"
RESULT = E / "r520_industry_index_feasibility_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _date_text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    if isinstance(parsed, datetime):
        parsed = parsed.date()
    return str(parsed)


def describe_history(
    frame: pd.DataFrame,
    *,
    source: str,
    code: str,
    name: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    if "日期" not in frame.columns or "成交额" not in frame.columns:
        raise ValueError(f"{source}:{code} missing 日期 or 成交额")
    dates = pd.to_datetime(frame["日期"], errors="coerce")
    amounts = pd.to_numeric(frame["成交额"], errors="coerce")
    valid_dates = dates.dropna()
    first = _date_text(valid_dates.min()) if not valid_dates.empty else None
    last = _date_text(valid_dates.max()) if not valid_dates.empty else None
    positive_ratio = (
        float(((amounts > 0) & amounts.notna()).sum() / len(frame)) if len(frame) else 0.0
    )
    return {
        "source": source,
        "code": code,
        "name": name,
        "rows": len(frame),
        "first_date": first,
        "last_date": last,
        "positive_amount_ratio": positive_ratio,
        "start_ok": bool(first is not None and first <= start),
        "end_ok": bool(last is not None and last >= end),
        "amount_ok": bool(len(frame) and positive_ratio == 1.0),
    }


def evaluate_gate(rows: list[dict[str, Any]], *, minimum: int = 20) -> dict[str, Any]:
    long_history = [
        row for row in rows if row["start_ok"] and row["end_ok"] and row["amount_ok"]
    ]
    checks = {
        "shenwan_rows_present": bool(rows),
        "minimum_long_history_industries": len(long_history) >= minimum,
    }
    return {
        "pass": all(checks.values()),
        "checks": checks,
        "long_history_count": len(long_history),
        "evaluated_count": len(rows),
    }


def verify_prereg() -> dict[str, Any]:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    if not sidecar.exists() or sha(PREREG) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R520 preregistration checksum mismatch")
    return json.loads(PREREG.read_text(encoding="utf-8"))


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R520 output exists; refusing overwrite: {path.name}")


def normalize_code(value: Any) -> str:
    return re.sub(r"\.(SI|SW)$", "", str(value).strip())


def collect_shenwan(ak: Any, start: str, end: str) -> tuple[list[dict[str, Any]], list[str]]:
    catalog = ak.sw_index_first_info()
    if not {"行业代码", "行业名称"}.issubset(catalog.columns):
        raise RuntimeError("Shenwan catalog missing 行业代码/行业名称")
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for item in catalog.to_dict("records"):
        code = normalize_code(item["行业代码"])
        name = str(item["行业名称"])
        try:
            history = ak.index_hist_sw(symbol=code, period="day")
            rows.append(
                describe_history(
                    history,
                    source="shenwan",
                    code=code,
                    name=name,
                    start=start,
                    end=end,
                )
            )
        except Exception as exc:  # noqa: BLE001 - isolate one provider member for the inventory
            failures.append(f"shenwan:{code}:{type(exc).__name__}: {exc}")
    return rows, failures


def collect_ths(
    ak: Any, names: list[str], start: str, end: str
) -> tuple[list[dict[str, Any]], list[str]]:
    catalog = ak.stock_board_industry_name_ths()
    if not {"name", "code"}.issubset(catalog.columns):
        raise RuntimeError("THS catalog missing name/code")
    by_name = {str(row["name"]): str(row["code"]) for row in catalog.to_dict("records")}
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for name in names:
        code = by_name.get(name)
        if code is None:
            failures.append(f"ths:{name}:missing from current catalog")
            continue
        try:
            history = ak.stock_board_industry_index_ths(
                symbol=name,
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
            )
            rows.append(
                describe_history(
                    history,
                    source="ths",
                    code=code,
                    name=name,
                    start=start,
                    end=end,
                )
            )
        except Exception as exc:  # noqa: BLE001 - isolate one provider member for the inventory
            failures.append(f"ths:{name}:{type(exc).__name__}: {exc}")
    return rows, failures


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# R520 行业指数数据可行性审计",
        "",
        f"- 申万行业：{payload['shenwan']['catalog_count']} 个，长历史 {payload['gate']['long_history_count']} 个",
        f"- 同花顺预注册样本：{len(payload['ths']['history'])}/{len(payload['ths']['requested'])} 个可取",
        f"- 数据门槛：{'通过' if payload['gate']['pass'] else '未通过'}",
        "",
        "## 申万行业指数",
        "",
        "| 代码 | 名称 | 行数 | 首日 | 末日 | 正成交额比例 | 通过 |",
        "| --- | --- | ---: | --- | --- | ---: | --- |",
    ]
    for row in payload["shenwan"]["history"]:
        passed = row["start_ok"] and row["end_ok"] and row["amount_ok"]
        lines.append(
            f"| {row['code']} | {row['name']} | {row['rows']} | {row['first_date']} | "
            f"{row['last_date']} | {row['positive_amount_ratio']:.3f} | "
            f"{'是' if passed else '否'} |"
        )
    lines += [
        "",
        "## 同花顺样本",
        "",
        "| 代码 | 名称 | 行数 | 首日 | 末日 | 正成交额比例 | 通过 |",
        "| --- | --- | ---: | --- | --- | ---: | --- |",
    ]
    for row in payload["ths"]["history"]:
        passed = row["start_ok"] and row["end_ok"] and row["amount_ok"]
        lines.append(
            f"| {row['code']} | {row['name']} | {row['rows']} | {row['first_date']} | "
            f"{row['last_date']} | {row['positive_amount_ratio']:.3f} | "
            f"{'是' if passed else '否'} |"
        )
    lines += [
        "",
        "## 门槛",
        "",
        f"- 最短历史起点不晚于 {payload['thresholds']['start']}",
        f"- 最新历史不早于 {payload['thresholds']['end']}",
        f"- 至少 {payload['thresholds']['minimum_long_history_industries']} 个申万行业全部通过",
        "- 每条历史记录成交额必须为正",
        "",
        "## 结论",
        "",
    ]
    if payload["gate"]["pass"]:
        lines.append(
            "行业指数历史数据足以进入下一轮只读轮动研究；但指数本身不可直接交易，"
            "在基金映射和成本验证完成前不得生成订单或激活生产策略。"
        )
    else:
        lines.append("可用行业指数历史不足，停止依赖该数据的行业轮动研究。")
    if payload["failures"]:
        lines += ["", "## 抓取失败", ""] + [f"- {item}" for item in payload["failures"]]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in payload["known_limits"]] + [""]
    return "\n".join(lines)


def main() -> int:
    ensure_outputs_absent()
    prereg = verify_prereg()
    import akshare as ak

    start = str(prereg["start"])
    end = str(prereg["end"])
    shenwan_rows, shenwan_failures = collect_shenwan(ak, start, end)
    ths_rows, ths_failures = collect_ths(
        ak, list(prereg["sample_industries"]["ths"]), start, end
    )
    gate = evaluate_gate(
        shenwan_rows,
        minimum=int(prereg["gates"]["minimum_long_history_industries"]),
    )
    failure_rows = shenwan_failures + ths_failures
    payload: dict[str, Any] = {
        "round": "R520",
        "research_type": "external_data_feasibility",
        "classification": prereg["classification"],
        "created_at": str(date.today()),
        "preregistration_sha256": sha(PREREG),
        "thresholds": {
            "start": start,
            "end": end,
            "minimum_long_history_industries": prereg["gates"][
                "minimum_long_history_industries"
            ],
            "require_positive_amount": prereg["gates"]["require_positive_amount"],
        },
        "shenwan": {
            "catalog_count": len(shenwan_rows) + len(shenwan_failures),
            "history": shenwan_rows,
        },
        "ths": {
            "requested": prereg["sample_industries"]["ths"],
            "history": ths_rows,
        },
        "gate": gate,
        "failures": failure_rows,
        "decision": {**prereg["output_policy"]},
        "known_limits": prereg["known_limits"],
    }
    raw = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    ).encode()
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


if __name__ == "__main__":
    raise SystemExit(main())
