# 输入说明: R73 — 分钟级微观结构数据可行性审计。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r73_minute_probe.py
# 结果 JSON: 同目录 r73_minute_probe.json。
from __future__ import annotations

import json
from pathlib import Path

EV = Path(__file__).resolve().parent
DATA = EV.parents[5] / "data"
SAMPLES = ["600519", "000001", "300750"]


def main() -> int:
    import akshare as ak

    stock_files = len(list((DATA / "kline_minute").glob("**/*.parquet")))
    etf_files = len(list((DATA / "kline_etf_minute").glob("**/*.parquet")))
    records = []
    for symbol in SAMPLES:
        for period in ("1", "5"):
            try:
                frame = ak.stock_zh_a_hist_min_em(
                    symbol=symbol, period=period,
                    start_date="2021-01-01 09:30:00", end_date="2021-01-31 15:00:00", adjust="",
                )
                records.append({"symbol": symbol, "period": period, "status": "ok", "rows": len(frame)})
            except Exception:  # noqa: BLE001 - capability audit records provider failure
                records.append({"symbol": symbol, "period": period, "status": "error"})
    successful_rows = sum(r.get("rows", 0) for r in records if r["status"] == "ok")
    result = {
        "local_stock_minute_files": stock_files, "local_etf_minute_files": etf_files,
        "external_probes": records, "successful_probe_rows": successful_rows,
        "required_history": "2021-06~2026-10 minute coverage",
        "D1": bool(stock_files > 0 or successful_rows > 0),
        "decision": "data gap; no local minute history and external minute endpoint unavailable in this environment",
        "caveat": "even a successful short-window probe would need full date coverage before factor research",
    }
    path = EV / "r73_minute_probe.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("D1", result["D1"], "local", stock_files, "probe_rows", successful_rows, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
