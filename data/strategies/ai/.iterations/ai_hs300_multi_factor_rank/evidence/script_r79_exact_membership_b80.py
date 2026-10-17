# 输入说明: R79 — 逐日精确 HS300 成员下复跑 B80/P2/V20。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r79_exact_membership_b80.py
# 结果 JSON: 同目录 r79_exact_membership_b80.json。逐日成员原始缓存复用 data/research/r78_daily_membership/ (不入库)。
from __future__ import annotations

import bisect
import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

EV = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r40 = _load("r40", "script_r40_survivorship.py")
r41 = _load("r41", "script_r41_b80_survivorship.py")
DATA = r40.DATA
DAILY_CACHE = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
R78_JSON = EV / "r78_daily_membership_deletions.json"
R41_JSON = EV / "r41_b80_survivorship.json"


def _exact_membership_frame() -> tuple[pl.DataFrame, dict]:
    snapshots = pl.read_parquet(DAILY_CACHE).with_columns([
        pl.col("query_date").cast(pl.Date),
        pl.col("symbol").cast(pl.String),
    ])
    rows = []
    previous: set[str] | None = None
    transition_dates = []
    for keys, group in snapshots.group_by("query_date", maintain_order=True):
        day = keys[0] if isinstance(keys, tuple) else keys
        current = set(group["symbol"].to_list())
        if previous is None or current != previous:
            rows.extend({"effective_date": day, "symbol": symbol} for symbol in sorted(current))
            transition_dates.append({"date": str(day), "members": len(current)})
        previous = current
    frame = pl.DataFrame(rows, schema={"effective_date": pl.Date, "symbol": pl.String}).sort(
        ["effective_date", "symbol"]
    )
    audit = {
        "snapshots": snapshots["query_date"].n_unique(),
        "rows": snapshots.height,
        "symbols": snapshots["symbol"].n_unique(),
        "transition_dates": len(transition_dates),
        "transition_sizes": transition_dates,
        "date_min": str(snapshots["query_date"].min()),
        "date_max": str(snapshots["query_date"].max()),
        "all_days_300": bool(
            snapshots.group_by("query_date")
            .agg([
                pl.len().alias("rows"),
                pl.col("symbol").n_unique().alias("unique"),
            ])
            .filter((pl.col("rows") != 300) | (pl.col("unique") != 300))
            .is_empty()
        ),
    }
    return frame, audit


def _phase_membership(membership: pl.DataFrame, phases: list[date]) -> dict[str, dict]:
    effective_dates = sorted(membership["effective_date"].unique().to_list())
    by_date = {
        keys[0] if isinstance(keys, tuple) else keys: set(group["symbol"].to_list())
        for keys, group in membership.group_by("effective_date", maintain_order=True)
    }
    out = {}
    for phase in phases:
        index = bisect.bisect_right(effective_dates, phase) - 1
        symbols = by_date[effective_dates[index]] if index >= 0 else set()
        out[str(phase)] = {"effective_date": str(effective_dates[index]) if index >= 0 else None, "members": len(symbols)}
    return out


def _metric_delta(current: dict, baseline: dict) -> dict:
    out = {}
    for key in ("sharpe_mean", "annual_mean", "mdd_mean"):
        left = current.get(key)
        right = baseline.get(key)
        out[key] = round(left - right, 6) if left is not None and right is not None else None
    for key in ("positive_rate", "worst"):
        left = current.get(key)
        right = baseline.get(key)
        out[key] = round(left - right, 6) if left is not None and right is not None else None
    return out


def _compact_stats(stats: dict[str, dict]) -> dict:
    return r41.compact_stats(stats)


def main() -> int:
    r78 = json.loads(R78_JSON.read_text(encoding="utf-8"))
    r41_evidence = json.loads(R41_JSON.read_text(encoding="utf-8"))
    if not r78["decision"]["D1"]:
        raise RuntimeError("R78 逐日成员质量门槛未通过, 不能启动 R79")
    membership, membership_audit = _exact_membership_frame()
    phases = r40.r33.phase_dates(r40.PHASE_TARGETS)
    phase_membership = _phase_membership(membership, phases)
    print("membership", membership_audit, flush=True)
    print("phase membership", phase_membership, flush=True)

    v20_exact = r40.run_v20_phases(phases, membership, "r79_exact_v20")
    p2_exact, p2_stats = r41.run_p2_phases(phases, membership, "r79_exact_p2")
    b80_exact = r41.summary(r41.combo_frames(phases, p2_exact, v20_exact))
    p2_summary = r41.summary([p2_exact[str(phase)] for phase in phases])
    v20_summary = r41.summary([v20_exact[str(phase)] for phase in phases])

    e1 = bool(
        b80_exact["metrics"]["sharpe_mean"] >= 1.05
        and b80_exact["metrics"]["annual_mean"] >= 0.20
        and b80_exact["metrics"]["mdd_mean"] >= -0.15
        and b80_exact["rolling"]["positive_rate"] >= 0.90
        and b80_exact["rolling"]["worst"] >= -0.08
    )
    e2 = bool(
        b80_exact["metrics"]["sharpe_mean"] >= v20_summary["metrics"]["sharpe_mean"] + 0.20
        and b80_exact["metrics"]["annual_mean"] >= v20_summary["metrics"]["annual_mean"] + 0.05
    )
    e3 = bool(
        p2_summary["metrics"]["sharpe_mean"] >= 0.90
        and p2_summary["metrics"]["annual_mean"] >= 0.10
        and p2_summary["metrics"]["mdd_mean"] >= -0.15
        and p2_summary["rolling"]["positive_rate"] >= 0.85
        and p2_summary["rolling"]["worst"] >= -0.10
    )
    d1 = bool(
        membership_audit["all_days_300"]
        and membership_audit["snapshots"] >= 1300
        and all(row["members"] == 300 for row in phase_membership.values())
    )

    if e1 and e2:
        recommendation = "B80 remains fallback; exact-membership robust"
        production_state = "deploy_candidate"
    elif e3:
        recommendation = "P2 defensive candidate; B80 not robust"
        production_state = "defensive_candidate"
    else:
        recommendation = "No existing long-only portfolio is production-ready"
        production_state = "pause_production"

    monthly_hist = r41_evidence["historical"]
    result: dict[str, Any] = {
        "window": {"phases": [str(phase) for phase in phases], "end": str(r40.END)},
        "membership": {"audit": membership_audit, "phase_membership": phase_membership},
        "historical_exact": {
            "P2": p2_summary,
            "V20": v20_summary,
            "B80": b80_exact,
        },
        "historical_monthly_r41": monthly_hist,
        "delta_exact_minus_monthly": {
            "P2": _metric_delta(p2_summary["metrics"], monthly_hist["P2"]["metrics"]),
            "V20": _metric_delta(v20_summary["metrics"], monthly_hist["V20"]["metrics"]),
            "B80": _metric_delta(b80_exact["metrics"], monthly_hist["B80"]["metrics"]),
            "rolling_P2": _metric_delta(p2_summary["rolling"], monthly_hist["P2"]["rolling"]),
            "rolling_V20": _metric_delta(v20_summary["rolling"], monthly_hist["V20"]["rolling"]),
            "rolling_B80": _metric_delta(b80_exact["rolling"], monthly_hist["B80"]["rolling"]),
        },
        "phase_year_detail": {
            "exact_B80": b80_exact.get("yearly"),
            "monthly_B80": monthly_hist["B80"].get("yearly"),
            "exact_P2": p2_summary.get("yearly"),
            "monthly_P2": monthly_hist["P2"].get("yearly"),
            "exact_V20": v20_summary.get("yearly"),
            "monthly_V20": monthly_hist["V20"].get("yearly"),
        },
        "p2_stats_exact": _compact_stats(p2_stats),
        "p2_stats_monthly_r41": r41_evidence.get("p2_stats_historical"),
        "decision": {
            "D1_exact_membership": d1,
            "E1_B80_historical": e1,
            "E2_B80_incremental": e2,
            "E3_P2_fallback": e3,
        },
        "adopted": bool(e1 and e2),
        "production_state": production_state,
        "final_recommendation": recommendation,
        "caveats": [
            "逐日成员来自 BaoStock 查询, 不是中证指数原始调样公告",
            "策略参数/成本/相位与 R41 完全一致, 但逐日成员会改变 V20/P2 的盘中候选与交易",
            "财务字段仍是本地当前快照 PIT, 存在报表重述风险",
            "P2 行业中性化仍使用当前行业快照",
        ],
    }
    path = EV / "r79_exact_membership_b80.json"
    path.write_text(json.dumps(r40.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("B80 exact:", b80_exact, flush=True)
    print("P2 exact:", p2_summary, flush=True)
    print("V20 exact:", v20_summary, flush=True)
    print("decision", result["decision"], "state", production_state, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
