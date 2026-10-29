#!/usr/bin/env python3
"""Attribute R275 candidate fills to R288 term-gate-release signal dates."""
from __future__ import annotations

import hashlib
import json
import statistics
from datetime import date
from pathlib import Path

import polars as pl

EVIDENCE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[6]
PRE = EVIDENCE / "r289_preregistration.json"
PANEL = EVIDENCE / "r289_panel_registration.json"
OUT = EVIDENCE / "r289_trade_attribution.json"
REPORT = EVIDENCE / "r289_result.md"


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_once(path: Path, data: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(data)
    digest = sha_file(path)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def summarize(rows: list[dict]) -> dict:
    values = [float(row["pnl_pct"]) for row in rows]
    amounts = [float(row["pnl_amount"]) for row in rows if row.get("pnl_amount") is not None]
    return {
        "trade_count": len(rows),
        "wins": sum(value > 0 for value in values),
        "losses": sum(value < 0 for value in values),
        "break_even": sum(value == 0 for value in values),
        "win_rate": sum(value > 0 for value in values) / len(values) if values else None,
        "mean_trade_return": statistics.mean(values) if values else None,
        "median_trade_return": statistics.median(values) if values else None,
        "sum_trade_pnl_amount": sum(amounts) if amounts else None,
        "mean_trade_pnl_amount": statistics.mean(amounts) if amounts else None,
    }


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def money(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}"


def main() -> int:
    if OUT.exists() or REPORT.exists() or OUT.with_name(OUT.name + ".sha256").exists() or REPORT.with_name(REPORT.name + ".sha256").exists():
        raise RuntimeError("R289 result already exists; refusing overwrite")
    prereg_hash = sha_file(PRE)
    if PRE.with_name(PRE.name + ".sha256").read_text(encoding="ascii").split()[0] != prereg_hash:
        raise RuntimeError("R289 preregistration checksum mismatch")
    prereg = json.loads(PRE.read_text(encoding="utf-8"))
    if prereg.get("round") != "R289" or prereg.get("status") != "frozen_before_outcome_read":
        raise RuntimeError("R289 preregistration identity/status mismatch")
    panel = json.loads(PANEL.read_text(encoding="utf-8"))
    if panel.get("registration_pass") is not True or panel.get("candidate", {}).get("status") != "pending":
        raise RuntimeError("R289 panel registration is not proven pending")
    if panel.get("r288_candidate", {}).get("status") != "pending":
        raise RuntimeError("R288 candidate changed status")

    for relative, expected in prereg["input_sha256"].items():
        path = ROOT / relative
        if not path.is_file() or sha_file(path) != expected:
            raise RuntimeError(f"Frozen input missing or changed: {relative}")
    for relative, expected in prereg["code_sha256"].items():
        path = ROOT / relative
        if not path.is_file() or sha_file(path) != expected:
            raise RuntimeError(f"Frozen analysis code changed: {relative}")

    audit_path = ROOT / prereg["paths"]["r288_audit"]
    candidate_trades_path = ROOT / prereg["paths"]["r275_candidate_trades"]
    control_trades_path = ROOT / prereg["paths"]["r266_control_trades"]
    curve_path = ROOT / prereg["paths"]["r275_candidate_curve"]
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("round") != "R288" or audit.get("status") != "complete":
        raise RuntimeError("Unexpected R288 audit status")

    released: set[tuple[date, str]] = set()
    digest = hashlib.sha256()
    for item in audit["daily_results"]:
        signal_date = date.fromisoformat(item["date"])
        symbols = item["released_symbols"]
        if symbols != sorted(set(symbols)):
            raise RuntimeError(f"R288 released symbols are not unique/sorted on {signal_date}")
        for symbol in symbols:
            key = (signal_date, symbol)
            if key in released:
                raise RuntimeError(f"Duplicate released member-date: {key}")
            released.add(key)
            digest.update(f"{signal_date.isoformat()}\t{symbol}\n".encode("utf-8"))
    summary = audit["gate_release_summary"]
    if len(released) != summary["released_rows"] or digest.hexdigest() != summary["released_member_date_keys_sha256"]:
        raise RuntimeError("R288 released-set count/hash mismatch")

    calendar_frame = pl.read_parquet(curve_path).select(pl.col("date").cast(pl.Date)).unique().sort("date")
    calendar = calendar_frame.get_column("date").to_list()
    if len(calendar) < 2 or len(calendar) != len(set(calendar)):
        raise RuntimeError("R275 curve has an invalid trading calendar")
    previous_day = {calendar[index]: calendar[index - 1] for index in range(1, len(calendar))}

    candidate_trades = json.loads(candidate_trades_path.read_text(encoding="utf-8"))
    control_trades = json.loads(control_trades_path.read_text(encoding="utf-8"))
    for label, rows in (("R275 candidate", candidate_trades), ("R266 control", control_trades)):
        keys = [(row["symbol"], date.fromisoformat(row["entry_date"][:10])) for row in rows]
        if len(keys) != len(set(keys)):
            raise RuntimeError(f"Duplicate symbol/entry-date trades in {label}")
        if any(row.get("pnl_pct") is None for row in rows):
            raise RuntimeError(f"Missing trade return in {label}")

    candidate_by_key = {(row["symbol"], date.fromisoformat(row["entry_date"][:10])): row for row in candidate_trades}
    control_keys = {(row["symbol"], date.fromisoformat(row["entry_date"][:10])) for row in control_trades}
    matched: list[dict] = []
    outside_audit = []
    for row in candidate_trades:
        entry = date.fromisoformat(row["entry_date"][:10])
        if entry not in previous_day:
            raise RuntimeError(f"No prior trading session for open_t+1 fill date {entry}")
        signal = previous_day[entry]
        if not (date.fromisoformat(prereg["window"]["start"]) <= signal <= date.fromisoformat(prereg["window"]["end"])):
            outside_audit.append({"symbol": row["symbol"], "entry_date": entry.isoformat(), "signal_date": signal.isoformat()})
            continue
        if (signal, row["symbol"]) in released:
            matched.append({**row, "signal_date": signal.isoformat()})
    matched_keys = {(row["symbol"], date.fromisoformat(row["entry_date"][:10])) for row in matched}
    if not matched_keys.issubset(candidate_by_key):
        raise RuntimeError("Matched trade key absent from R275 candidate cache")
    if matched_keys & control_keys:
        raise RuntimeError("A gate-release attributed fill also appears as the same R266 control trade")

    other = [row for row in candidate_trades if (row["symbol"], date.fromisoformat(row["entry_date"][:10])) not in matched_keys]
    matched_dates = sorted({row["signal_date"] for row in matched})
    matched_symbols = sorted({row["symbol"] for row in matched})
    released_dates = sorted({day.isoformat() for day, _ in released})
    result = {
        "round": "R289",
        "classification": "exploratory_historical_trade_attribution_after_outcome_review",
        "status": "completed_no_alpha_confirmation",
        "preregistration_sha256": prereg_hash,
        "panel_registration_sha256": sha_file(PANEL),
        "input_sha256": prereg["input_sha256"],
        "released_signal_set": {
            "member_date_rows": len(released),
            "active_dates": len(released_dates),
            "distinct_symbols": len({symbol for _, symbol in released}),
            "sha256": digest.hexdigest(),
        },
        "execution_mapping": {
            "rule": "entry_date is matched to the immediately preceding date in the frozen R275 backtest curve calendar; open_t+1 fills occur on entry_date",
            "calendar_first": calendar[0].isoformat(),
            "calendar_last": calendar[-1].isoformat(),
            "candidate_trade_rows": len(candidate_trades),
            "control_trade_rows": len(control_trades),
            "candidate_trade_rows_outside_r288_window": len(outside_audit),
            "matched_trade_rows": len(matched),
            "matched_fraction_of_candidate_trades": len(matched) / len(candidate_trades) if candidate_trades else None,
            "matched_fraction_of_released_member_date_rows": len(matched) / len(released) if released else None,
            "matched_signal_dates": len(matched_dates),
            "matched_symbols": len(matched_symbols),
            "matched_control_same_symbol_entry_date_rows": len(matched_keys & control_keys),
        },
        "trade_outcomes": {
            "gate_release_attributed": summarize(matched),
            "other_r275_candidate_trades": summarize(other),
            "all_r275_candidate_trades": summarize(candidate_trades),
            "r266_control_trades": summarize(control_trades),
        },
        "matched_trade_details": [
            {key: row.get(key) for key in ("symbol", "name", "signal_date", "entry_date", "exit_date", "pnl_pct", "pnl_amount", "exit_reason")}
            for row in sorted(matched, key=lambda item: (item["entry_date"], item["symbol"]))
        ],
        "limitations": prereg["limitations"],
    }
    result_hash = save_once(OUT, (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))

    attributed = result["trade_outcomes"]["gate_release_attributed"]
    comparison = result["trade_outcomes"]["other_r275_candidate_trades"]
    report = f"""# R289 门禁释放信号交易归因

## 问题与口径

本轮只检查 R288 因移除期限利差条件而新增的 `(信号日, 股票)`，是否对应 R275 移除期限门禁策略的实际成交。成交日使用 R275 冻结回测曲线中的正式交易日历，按 `open_t+1` 将成交日回退一个交易日映射为信号日。输入和方法在读取成交收益前已冻结。

R288 放行集合共 {len(released):,} 个成分股日、覆盖 {len(released_dates):,} 个交易日和 {len({symbol for _, symbol in released}):,} 只股票。R275 候选共 {len(candidate_trades)} 笔成交，其中 {len(matched)} 笔的前一交易日与新增放行集合精确匹配（{pct(result['execution_mapping']['matched_fraction_of_candidate_trades'])}）；占新增成分股日 {pct(result['execution_mapping']['matched_fraction_of_released_member_date_rows'])}。匹配成交覆盖 {len(matched_dates)} 个信号日、{len(matched_symbols)} 只股票。

## 成交表现

| 成交组 | 笔数 | 胜率 | 单笔平均收益 | 单笔收益中位数 | PnL 金额合计* |
| --- | ---: | ---: | ---: | ---: | ---: |
| R288 新放行信号对应的 R275 成交 | {attributed['trade_count']} | {pct(attributed['win_rate'])} | {pct(attributed['mean_trade_return'])} | {pct(attributed['median_trade_return'])} | {money(attributed['sum_trade_pnl_amount'])} |
| R275 其他成交 | {comparison['trade_count']} | {pct(comparison['win_rate'])} | {pct(comparison['mean_trade_return'])} | {pct(comparison['median_trade_return'])} | {money(comparison['sum_trade_pnl_amount'])} |

* 交易记录里的 `pnl_amount` 逐笔相加，表示这些已成交交易的归因金额，不是组合净值增量；持仓并发、资金占用和路径效应无法从逐笔金额加总中恢复。收益率沿用缓存中的小数口径，报告以百分比显示。

## 解释边界

- 精确匹配并证明了这些交易发生在门禁新增放行的前一信号日，并进入 R275 十持仓组合成交；它不表示全部 56,786 个放行股票日都可买入，也不等同于组合层面的净收益增量。
- 匹配交易在 R266 对照中相同 `(股票, 成交日)` 的数量为 {len(matched_keys & control_keys)}。
- 这是已查看多个历史区间后的描述性归因，不是独立验证；容量竞争、历史成分与宏观修订时点、每日 OHLC 对成交可行性的近似仍会影响结论。
- 本轮没有重跑回测、行情同步或 paper 交易；R289 面板候选及 R288 候选均保持 `pending`。不据此确认 alpha 或改变生产状态。

预注册 SHA-256：`{prereg_hash}`。

分析 JSON SHA-256：`{result_hash}`。

"""
    report_hash = save_once(REPORT, report.encode("utf-8"))
    print(json.dumps({
        "result_sha256": result_hash,
        "report_sha256": report_hash,
        "released_member_dates": len(released),
        "matched_trades": len(matched),
        "attributed_win_rate": attributed["win_rate"],
        "attributed_mean_return": attributed["mean_trade_return"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
