# 输入说明: 进程内运行 (无需后端)。运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r37_execution_gate.py
# 作用: R37 — 复用 R36 T+1 执行结果, 按尺度化保真门槛终审 C6_IND 执行可行性。
# 结果 JSON: 同目录 r37_execution_gate.json。
import json
from pathlib import Path

EV = Path(__file__).resolve().parent
r36 = json.loads((EV / "r36_execution.json").read_text(encoding="utf-8"))


def leg_fidelity(name: str) -> dict:
    rec = r36["close_vs_t1"][name]
    checks = {
        "corr": rec["corr"] is not None and rec["corr"] >= 0.98,
        "median": rec["median_abs_diff"] is not None and rec["median_abs_diff"] <= 0.006,
        "p90": rec["p90_abs_diff"] is not None and rec["p90_abs_diff"] <= 0.020,
        "relative": rec["relative_diff"] is not None and rec["relative_diff"] <= 0.20,
        "max": rec["max_abs_diff"] is not None and rec["max_abs_diff"] <= 0.06,
    }
    return {"metrics": rec, "checks": checks, "pass": all(checks.values())}


def main() -> int:
    f1 = {name: leg_fidelity(name) for name in ("D1_IND", "D4")}
    three = r36["t1"]["3x"]
    metrics = three["metrics"]
    rolling = three["rolling"]
    d1 = r36["d1_stats"]
    d4 = r36["d4_stats"]
    decision = {
        "F1": all(item["pass"] for item in f1.values()),
        "F2": r36["corr_t1_vs_close"] is not None and r36["corr_t1_vs_close"] >= 0.98,
        "F3": metrics["sharpe_mean"] >= 1.10 and metrics["annual_mean"] >= 0.22 and metrics["mdd_mean"] >= -0.14,
        "F4": rolling["positive_rate"] >= 0.90 and rolling["worst"] >= -0.08,
        "F5": bool(
            15 <= d1["members_mean"] <= 35
            and 15 <= d4["members_mean"] <= 35
            and d1["top_industry_share_mean"] <= 0.40
            and d4["top_industry_share_mean"] <= 0.40
            and max(d1["turnover_mean"], d4["turnover_mean"]) <= 0.25
        ),
    }
    decision["all_pass"] = all(decision.values())
    result = {
        "source": "r36_execution.json",
        "fidelity": f1,
        "combo_corr_t1_vs_close": r36["corr_t1_vs_close"],
        "t1_3x_metrics": metrics,
        "t1_3x_rolling": rolling,
        "d1_stats": d1,
        "d4_stats": d4,
        "decision": decision,
        "final_recommendation": "C6_IND execution-feasible" if decision["all_pass"] else None,
        "note": "F1/F2 为 R36 E1 的尺度化修订; 不改变执行模型与策略参数",
    }
    path = EV / "r37_execution_gate.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("fidelity:", {k: v["pass"] for k, v in f1.items()})
    print("decision:", decision)
    print("saved", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
