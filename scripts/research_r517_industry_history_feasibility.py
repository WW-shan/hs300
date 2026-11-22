#!/usr/bin/env python3
"""R517: local industry-history feasibility audit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r517_industry_history_feasibility_preregistration.json"
OUTPUT = E / "r517_industry_history_feasibility_2026-11-22.json"
RESULT = E / "r517_industry_history_feasibility_2026-11-22.md"

INDUSTRY_KEYWORDS = (
    "银行",
    "证券",
    "非银",
    "医药",
    "医疗",
    "消费",
    "食品",
    "饮料",
    "半导体",
    "芯片",
    "军工",
    "新能源",
    "光伏",
    "有色",
    "煤炭",
    "钢铁",
    "地产",
    "房地产",
    "化工",
    "机械",
    "传媒",
    "通信",
    "计算机",
    "农业",
    "养殖",
    "电力",
    "公用",
    "环保",
    "建材",
    "汽车",
    "煤炭",
    "旅游",
    "家电",
    "电子",
    "纺织",
    "轻工",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def coverage(root: Path, instruments: pl.DataFrame, *, amount: bool) -> pl.DataFrame:
    paths = sorted(root.glob("date=*/part.parquet"))
    if not paths:
        raise FileNotFoundError(root)
    cols = ["symbol", "date", "close"]
    if amount:
        cols.append("amount")
    frame = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .select(cols)
        .collect()
    )
    stats = frame.group_by("symbol").agg(
        pl.len().alias("days"),
        pl.col("date").min().alias("start"),
        pl.col("date").max().alias("end"),
        *([pl.col("amount").median().alias("median_amount")] if amount else []),
    )
    return stats.join(instruments, on="symbol", how="left")


def industry_label(name: str) -> str | None:
    for keyword in INDUSTRY_KEYWORDS:
        if keyword in name:
            return keyword
    return None


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R517 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    etf_inst = pl.read_parquet(DATA / "instruments_etf" / "instruments_etf.parquet")
    index_inst = pl.read_parquet(DATA / "instruments_index" / "instruments_index.parquet")
    etf_cov = coverage(DATA / "kline_etf_daily", etf_inst, amount=True)
    index_cov = coverage(DATA / "kline_index_daily", index_inst, amount=False)
    minimum_days = int(prereg["requirements"]["minimum_long_history_days"])
    etf_long = etf_cov.filter(pl.col("days") >= minimum_days).sort("days", descending=True)
    index_long = index_cov.filter(pl.col("days") >= minimum_days).sort("days", descending=True)
    etf_long = etf_long.with_columns(
        pl.col("name").map_elements(industry_label, return_dtype=pl.String).alias("industry_label")
    )
    index_long = index_long.with_columns(
        pl.col("name").map_elements(industry_label, return_dtype=pl.String).alias("industry_label")
    )
    etf_industries = sorted({value for value in etf_long["industry_label"].to_list() if value})
    index_industries = sorted({value for value in index_long["industry_label"].to_list() if value})
    checks = {
        "etf_long_history_count": len(etf_long)
        >= int(prereg["requirements"]["minimum_industry_proxies"]),
        "index_long_history_count": len(index_long)
        >= int(prereg["requirements"]["minimum_industry_proxies"]),
        "etf_distinct_industries": len(etf_industries)
        >= int(prereg["requirements"]["minimum_industry_proxies"]),
        "index_distinct_industries": len(index_industries)
        >= int(prereg["requirements"]["minimum_industry_proxies"]),
        "etf_liquidity_positive": bool(
            etf_long.height
            and etf_long.filter(pl.col("median_amount") > 0).height == etf_long.height
        ),
    }
    payload: dict[str, Any] = {
        "round": "R517",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "etf": {
            "instrument_count": len(etf_inst),
            "long_history_count": len(etf_long),
            "long_history": etf_long.select(
                ["symbol", "name", "days", "start", "end", "median_amount", "industry_label"]
            ).to_dicts(),
            "distinct_industry_labels": etf_industries,
        },
        "index": {
            "instrument_count": len(index_inst),
            "long_history_count": len(index_long),
            "long_history": index_long.select(
                ["symbol", "name", "days", "start", "end", "industry_label"]
            ).to_dicts(),
            "distinct_industry_labels": index_industries,
        },
        "gate": {"pass": all(checks.values()), "checks": checks},
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
    lines = [
        "# R517 行业历史数据可行性审计",
        "",
        f"- ETF 标的数：{len(etf_inst)}",
        f"- ETF 长历史数：{len(etf_long)}",
        f"- 指数标的数：{len(index_inst)}",
        f"- 指数长历史数：{len(index_long)}",
        f"- ETF 可识别行业标签：{len(etf_industries)}",
        f"- 指数可识别行业标签：{len(index_industries)}",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        "",
        "## ETF 长历史",
        "",
        "| 代码 | 名称 | 天数 | 起止 | 中位成交额 |",
        "| --- | --- | ---: | --- | ---: |",
    ]
    for row in etf_long.select(
        ["symbol", "name", "days", "start", "end", "median_amount"]
    ).to_dicts():
        lines.append(
            f"| {row['symbol']} | {row['name']} | {row['days']} | {row['start']} → {row['end']} | {row['median_amount']:.0f} |"
        )
    lines += [
        "",
        "## 指数长历史",
        "",
        "| 代码 | 名称 | 天数 | 起止 |",
        "| --- | --- | ---: | --- |",
    ]
    for row in index_long.select(["symbol", "name", "days", "start", "end"]).to_dicts():
        lines.append(
            f"| {row['symbol']} | {row['name']} | {row['days']} | {row['start']} → {row['end']} |"
        )
    lines += ["", "## 门槛", ""] + [
        f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()
    ]
    lines += (
        [
            "",
            "## 结论",
            "",
            "本地行业 ETF/指数历史不足以支持 2018 年起的严格行业轮动。行业 ETF 大多只有约 250 天，长期标的主要是宽基、红利、债券和黄金。",
            "",
            "## 边界",
            "",
        ]
        + [f"- {item}" for item in prereg["known_limits"]]
        + [""]
    )
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii"
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
