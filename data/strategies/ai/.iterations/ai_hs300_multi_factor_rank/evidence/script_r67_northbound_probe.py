# 输入说明: R67 — 北向个股持股历史覆盖审计。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r67_northbound_probe.py
# 结果 JSON: 同目录 r67_northbound_probe.json。样本缓存 data/research/r67_northbound/ (不提交)。
from __future__ import annotations

import json
import time
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
DATA = EV.parents[5] / "data"
CACHE = DATA / "research" / "r67_northbound"
SAMPLES = ["600519", "000001", "300750", "601318", "000333", "002594", "600036", "000858", "300059", "601012", "600276", "002415"]


def main() -> int:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    records = []
    failures = {}
    for symbol in SAMPLES:
        path = CACHE / f"{symbol}.parquet"
        last_error = None
        if path.exists() and path.stat().st_size > 0:
            frame = pl.read_parquet(path)
        else:
            frame = None
            for attempt in range(3):
                try:
                    raw = ak.stock_hsgt_individual_em(symbol=symbol)
                    frame = pl.from_pandas(raw)
                    tmp = path.with_suffix(".tmp")
                    frame.write_parquet(tmp)
                    tmp.replace(path)
                    last_error = ""
                    break
                except Exception as exc:  # noqa: BLE001 - coverage audit records failures
                    last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
                    if attempt < 2:
                        time.sleep(0.8 * (attempt + 1))
        if frame is None:
            failures[symbol] = last_error or "unknown"
            records.append({"symbol": symbol, "rows": None, "error": failures[symbol]})
            continue
        dates = frame["持股日期"].drop_nulls() if frame.height and "持股日期" in frame.columns else []
        records.append({
            "symbol": symbol, "rows": frame.height,
            "first_date": str(min(dates)) if len(dates) else None,
            "last_date": str(max(dates)) if len(dates) else None,
        })
        print("northbound probe", symbol, frame.height, records[-1]["last_date"], flush=True)
    valid_last = [r["last_date"] for r in records if r.get("last_date")]
    result = {
        "samples": len(SAMPLES), "failure_count": len(failures), "failure_rate": round(len(failures) / len(SAMPLES), 6),
        "records": records, "latest_observed": max(valid_last) if valid_last else None,
        "required_last_date": "2026-09-30",
        "D1": bool(len(failures) / len(SAMPLES) <= 0.10 and valid_last and max(valid_last) >= "2026-09-30"),
        "decision": "data gap; northbound individual holdings cannot cover 2025/2026 validation",
        "caveat": "stock_hsgt_individual_detail_em also returned no records for 2025/2026 samples",
    }
    path = EV / "r67_northbound_probe.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("D1", result["D1"], "latest", result["latest_observed"], "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
