# 输入说明: R66 — 机构持股季度截面的历史可用性审计 (预注册 D1 数据门槛)。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r66_institution_probe.py
# 结果 JSON: 同目录 r66_institution_probe.json。原始截面缓存 data/research/r66_institution/ (不提交)。
from __future__ import annotations

import json
import time
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
DATA = EV.parents[5] / "data"
CACHE = DATA / "research" / "r66_institution"
QUARTERS = [
    "20204", "20211", "20212", "20213", "20214", "20221", "20222", "20223", "20224",
    "20231", "20232", "20233", "20234", "20241", "20242", "20243", "20244",
    "20251", "20252", "20253", "20254", "20261", "20262",
]


def main() -> int:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    records = []
    failures = {}
    for quarter in QUARTERS:
        path = CACHE / f"{quarter}.parquet"
        last_error = None
        if path.exists() and path.stat().st_size > 0:
            frame = pl.read_parquet(path)
        else:
            frame = None
            for attempt in range(3):
                try:
                    raw = ak.stock_institute_hold(symbol=quarter)
                    frame = pl.from_pandas(raw)
                    tmp = path.with_suffix(".tmp")
                    frame.write_parquet(tmp)
                    tmp.replace(path)
                    last_error = ""
                    break
                except Exception as exc:  # noqa: BLE001 - data availability audit records failures
                    last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
                    if attempt < 2:
                        time.sleep(0.8 * (attempt + 1))
        if frame is None:
            failures[quarter] = last_error or "unknown"
            records.append({"quarter": quarter, "rows": None, "error": failures[quarter]})
            continue
        records.append({
            "quarter": quarter,
            "rows": frame.height,
            "unique_codes": int(frame["证券代码"].n_unique()) if frame.height and "证券代码" in frame.columns else 0,
            "institution_count_nonnull": int(frame["机构数"].is_not_null().sum()) if frame.height and "机构数" in frame.columns else 0,
        })
        print("institution probe", quarter, frame.height, flush=True)
    validation_quarters = [r for r in records if r["quarter"] >= "20241"]
    validation_nonempty = sum(1 for r in validation_quarters if (r["rows"] or 0) > 0)
    result = {
        "quarters": len(QUARTERS), "failure_count": len(failures),
        "failure_rate": round(len(failures) / len(QUARTERS), 6),
        "records": records,
        "last_nonempty_quarter": max((r["quarter"] for r in records if (r["rows"] or 0) > 0), default=None),
        "validation_quarters": len(validation_quarters), "validation_nonempty": validation_nonempty,
        "full_validation_coverage": validation_nonempty == len(validation_quarters),
        "D1": bool(len(failures) / len(QUARTERS) <= 0.10 and validation_nonempty == len(validation_quarters)),
        "decision": "data gap; do not compute institutional factors from truncated summary",
        "caveat": "stock_institute_hold_detail returns only partial visible institutions and cannot reconstruct all-market cross-section",
    }
    path = EV / "r66_institution_probe.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("D1", result["D1"], "last_nonempty", result["last_nonempty_quarter"], "validation", validation_nonempty, "/", len(validation_quarters), "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
