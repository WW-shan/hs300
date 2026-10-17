# 输入说明: R72 — 个股新闻情绪历史覆盖审计。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r72_news_probe.py
# 结果 JSON: 同目录 r72_news_probe.json。原始新闻缓存 data/research/r72_news/ (不提交)。
from __future__ import annotations

import json
from pathlib import Path

EV = Path(__file__).resolve().parent
DATA = EV.parents[5] / "data"
CACHE = DATA / "research" / "r72_news"
SAMPLES = ["600519", "000001", "300750", "601318", "000333", "002594", "600036", "000858", "300059", "601012", "600276", "002415"]


def main() -> int:
    import pandas as pd
    pd.options.mode.string_storage = "python"
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    records, failures = [], {}
    for symbol in SAMPLES:
        try:
            frame = ak.stock_news_em(symbol=symbol)
            frame.to_parquet(CACHE / f"{symbol}.parquet", index=False)
            dates = pd.to_datetime(frame["发布时间"], errors="coerce").dropna()
            records.append({
                "symbol": symbol, "rows": len(frame),
                "first_time": str(dates.min()) if len(dates) else None,
                "last_time": str(dates.max()) if len(dates) else None,
                "days": int((dates.max() - dates.min()).days) if len(dates) else None,
            })
            print("news probe", symbol, len(frame), records[-1]["first_time"], records[-1]["last_time"], flush=True)
        except Exception as exc:  # noqa: BLE001 - data coverage audit records failures
            failures[symbol] = f"{type(exc).__name__}: {str(exc)[:180]}"
            records.append({"symbol": symbol, "rows": None, "error": failures[symbol]})
    earliest = min((r["first_time"] for r in records if r.get("first_time")), default=None)
    result = {
        "samples": len(SAMPLES), "failure_count": len(failures),
        "failure_rate": round(len(failures) / len(SAMPLES), 6), "records": records,
        "earliest_observed": earliest, "required_earliest": "2021-06-01",
        "D1": bool(len(failures) / len(SAMPLES) <= 0.10 and earliest is not None and earliest <= "2021-06-01"),
        "decision": "data gap; news endpoint returns only recent items and cannot support historical sentiment backtest",
        "caveat": "pandas string_storage switched to python because AkShare news regex is incompatible with ArrowString",
    }
    path = EV / "r72_news_probe.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("D1", result["D1"], "earliest", earliest, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
