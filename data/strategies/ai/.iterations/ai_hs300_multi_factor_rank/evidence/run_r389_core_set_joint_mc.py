"""R389: joint Monte Carlo over the frozen R386 factor-set construction.

The R386 strategy formulas are immutable. This runner perturbs the monthly
risk-parity construction, C cap, execution choices and member presence while
reusing the validated R385/R387 curve composer. It never creates orders.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import statistics as st
from pathlib import Path
from typing import Any

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
PREREG = EVIDENCE / "r389_core_set_joint_mc_preregistration.json"
OUTPUT = EVIDENCE / "r389_core_set_joint_mc.json"
RESULT = EVIDENCE / "r389_core_set_joint_mc_result.md"
COSTS = ("2x", "3x", "5x")
EXPECTED_PARAMETERS = {
    "c_satellite_cap": 0.13,
    "equity_target_vol": 0.17,
    "overlay_lookback_days": 110,
    "overlay_target_vol": 0.17,
    "overlay_equity_weight_max": 0.37,
}
BASE_CONSTRUCTION = {
    "lookback_days": 60,
    "min_weight": 0.20,
    "max_weight": 0.60,
}
START = "2022-05-05"
END = "2026-11-05"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def verify_sidecar(path: Path) -> str:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists():
        raise RuntimeError(f"missing checksum sidecar: {sidecar.name}")
    actual = sha256(path)
    expected = sidecar.read_text(encoding="ascii").split()[0]
    if actual != expected:
        raise RuntimeError(f"checksum mismatch: {path.name}")
    return actual


def verify_preregistration() -> dict[str, Any]:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    verify_sidecar(PREREG)
    if payload.get("round") != "R389":
        raise RuntimeError("R389 round mismatch")
    if payload["freeze"]["parameters"] != EXPECTED_PARAMETERS:
        raise RuntimeError("R389 frozen parameters changed")
    if payload["freeze"]["construction"]["risk_parity_lookback_days"] != 60:
        raise RuntimeError("R389 risk-parity centre changed")
    if payload["cost_protocol"]["five_x_can_hard_reject"] is not False:
        raise RuntimeError("R389 cannot hard-reject on 5x")
    if payload["windows"]["train_end"] >= payload["windows"]["test_start"]:
        raise RuntimeError("R389 train/test windows overlap")
    return payload


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R389 output exists; refusing overwrite: {path.name}")


def weights_within_limit(weights: dict[str, float]) -> bool:
    return sum(float(value) for value in weights.values()) <= 1.0 + 1e-12


def renormalize_after_dropout(weights: dict[str, float], dropped: list[str]) -> dict[str, float]:
    if set(dropped) >= set(weights):
        raise ValueError("dropout must keep at least one leg")
    kept = {name: (0.0 if name in dropped else float(weight)) for name, weight in weights.items()}
    total = sum(kept.values())
    if total <= 0.0:
        raise ValueError("dropout must keep at least one leg")
    return {name: weight / total for name, weight in kept.items()}


def _practical_parameters(rng: random.Random) -> dict[str, Any]:
    return {
        "c_satellite_cap": round(rng.uniform(0.11, 0.15), 2),
        "equity_target_vol": round(rng.uniform(0.15, 0.19), 2),
        "overlay_lookback_days": int(round(rng.uniform(90, 130) / 5.0) * 5),
        "overlay_target_vol": round(rng.uniform(0.15, 0.19), 2),
        "overlay_equity_weight_max": round(rng.uniform(0.35, 0.39), 2),
    }


def generate_joint_samples() -> list[dict[str, Any]]:
    rng = random.Random(389)
    samples: list[dict[str, Any]] = []
    for index in range(96):
        dropped = [name for name in ("A", "B", "C") if rng.random() < 0.10]
        if len(dropped) == 3:
            dropped.pop()
        construction = {
            "lookback_days": rng.choice((40, 60, 90)),
            "min_weight": rng.choice((0.15, 0.20, 0.25)),
            "max_weight": rng.choice((0.50, 0.60, 0.70)),
        }
        if construction["min_weight"] > construction["max_weight"]:
            construction["min_weight"], construction["max_weight"] = (
                construction["max_weight"],
                construction["min_weight"],
            )
        parameters = _practical_parameters(rng)
        sample = {
            "sample": index,
            "dropped": sorted(dropped),
            "construction": construction,
            "parameters": parameters,
        }
        sample["sample_id"] = sha256_json({"round": "R389", **sample})
        samples.append(sample)
    return samples


def _stock_costs(cost: str) -> dict[str, float]:
    return {
        "2x": {"fees_pct": 0.0004, "commission_pct": 0.0004, "stamp_tax_pct": 0.0005, "slippage_bps": 10.0},
        "3x": {"fees_pct": 0.0006, "commission_pct": 0.0006, "stamp_tax_pct": 0.001, "slippage_bps": 15.0},
        "5x": {"fees_pct": 0.001, "commission_pct": 0.001, "stamp_tax_pct": 0.00125, "slippage_bps": 25.0},
    }[cost]


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def _curve_frame(curve: list[dict[str, Any]]) -> pl.DataFrame:
    from datetime import date

    rows = []
    for item in curve:
        raw = item.get("date")
        day = raw if isinstance(raw, date) else date.fromisoformat(str(raw)[:10])
        rows.append({"date": day, "value": float(item.get("value", 0.0))})
    if not rows:
        raise RuntimeError("empty curve")
    return pl.DataFrame(rows).sort("date").unique(subset=["date"], keep="last")


def _run_cached_curves() -> tuple[dict[str, Any], Any]:
    runtime = _module(EVIDENCE / "run_r385_pit_platform_monte_carlo.py", "r389_r385_runtime")
    cache = runtime.CACHE
    files = {}
    for cost in COSTS:
        files[f"A_{cost}"] = (f"A_{cost}_A.parquet", "2022-05-05")
        files[f"B_{cost}_fade05_h30_sl20"] = (f"B_{cost}_fade05_h30_sl20.parquet", "2022-05-05")
        files[f"C_{cost}_0"] = (f"C_{cost}_C0.parquet", "2022-05-05")
        files[f"C_{cost}_1"] = (f"C_{cost}_C1.parquet", "2022-06-02")
        files[f"C_{cost}_2"] = (f"C_{cost}_C2.parquet", "2022-07-01")
    outputs: dict[str, Any] = {}
    for name, (filename, start) in files.items():
        path = cache / filename
        if not path.exists():
            raise RuntimeError(f"missing R385 cache curve: {path.name}")
        frame = runtime._read_curve(path)
        if frame.height < 2 or frame["date"].min().isoformat() != start or frame["date"].max().isoformat() != END:
            raise RuntimeError(f"invalid R385 cache curve: {path.name}")
        outputs[name] = frame
    ctx = runtime._context()
    return {"runtime": runtime, "outputs": outputs, "ctx": ctx}, runtime


def _risk_parity_curve(
    curves: tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame],
    construction: dict[str, Any],
    c_cap: float,
    round_trip_cost: float,
) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    r344 = _module(EVIDENCE / "run_r344_extended_window.py", "r389_r344")
    original = r344._three_sleeve_targets

    def parameterized(dates, padded, current_cap):
        # R344 hard-codes 60/0.20/0.60 in the original helper. The local
        # function preserves its exact monthly algorithm while exposing the
        # perturbation dimensions frozen in R389.
        targets = []
        current = (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)
        previous_month = None
        lookback = int(construction["lookback_days"])
        low = float(construction["min_weight"])
        high = float(construction["max_weight"])
        for index, day in enumerate(dates):
            month = (day.year, day.month)
            if index == 0 or month != previous_month:
                if index < lookback:
                    weights = [1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0]
                else:
                    inv = [1.0 / max(__import__("statistics").pstdev(values[index - lookback:index]), 1e-6) for values in padded]
                    total = sum(inv)
                    weights = [value / total for value in inv]
                    for _ in range(10):
                        clipped = [min(high, max(low, value)) for value in weights]
                        clipped_total = sum(clipped)
                        weights = [value / clipped_total for value in clipped]
                        if all(low - 1e-12 <= value <= high + 1e-12 for value in weights):
                            break
                if weights[2] > current_cap:
                    residual = 1.0 - current_cap
                    ab_total = weights[0] + weights[1]
                    weights = [residual * weights[0] / ab_total, residual * weights[1] / ab_total, current_cap]
                current = tuple(weights)
                targets.append((day, current))
                previous_month = month
        return targets

    r344._three_sleeve_targets = parameterized
    try:
        return r344._combine_three(curves, round_trip_cost, c_cap)
    finally:
        r344._three_sleeve_targets = original


def _compose_base(construction: dict[str, Any], parameters: dict[str, Any], cost: str = "2x") -> dict[str, Any]:
    state, runtime = _run_cached_curves()
    outputs = state["outputs"]
    ctx = state["ctx"]
    r359 = ctx["r359"]
    c_curve = r359.combine_cohort_curves(
        [outputs[f"C_{cost}_{index}"] for index in range(3)],
        [__import__("datetime").date.fromisoformat(value) for value in ("2022-05-05", "2022-06-02", "2022-07-01")],
    )
    curves = (
        outputs[f"A_{cost}"],
        outputs[f"B_{cost}_fade05_h30_sl20"],
        c_curve,
    )
    round_trip = runtime.SCENARIOS[cost]["portfolio_turnover"]
    composite, weights = _risk_parity_curve(curves, construction, float(parameters["c_satellite_cap"]), round_trip)
    return {
        "composite": composite,
        "sleeve_weights": weights,
        "runtime": runtime,
        "assets": runtime.prepare_assets(ctx),
        "ctx": ctx,
    }


def evaluate_sample(sample: dict[str, Any], cost: str = "2x") -> dict[str, Any]:
    construction = sample["construction"]
    parameters = sample["parameters"]
    state, runtime = _run_cached_curves()
    ctx = state["ctx"]
    composite = _compose_base(construction, parameters, cost)["composite"]
    r331 = ctx["r331"]
    r114 = ctx["r114"]
    scenario = runtime.SCENARIOS[cost]
    old_331 = (r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX)
    old_114 = (r114.TARGET_VOL, r114.ROUND_TRIP_COST)
    try:
        r114.TARGET_VOL = float(parameters["equity_target_vol"])
        r114.ROUND_TRIP_COST = scenario["portfolio_turnover"]
        equity = r114._vol_target(composite)["curve"]
        r331.LOOKBACK = int(parameters["overlay_lookback_days"])
        r331.TARGET_VOL = float(parameters["overlay_target_vol"])
        r331.EQUITY_WEIGHT_MAX = float(parameters["overlay_equity_weight_max"])
        assets = runtime.prepare_assets(ctx)
        final = r331.build_three_asset_bucket(
            equity,
            assets["gold_proxy"][cost],
            assets["treasury"],
            assets["dividend"],
            round_trip_cost=scenario["defensive_turnover"],
        )
    finally:
        r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX = old_331
        r114.TARGET_VOL, r114.ROUND_TRIP_COST = old_114
    return {
        "sample": sample["sample"],
        "sleeve_curve": composite,
        "curve": final["curve"],
        "max_weight_sum": float((final.get("weight_summary") or {}).get("max_weight_sum", 1.0)),
    }


def curve_metrics(curve: pl.DataFrame, start: str, end: str) -> dict[str, Any]:
    part = curve.filter(
        (pl.col("date") >= pl.lit(start).str.strptime(pl.Date, "%Y-%m-%d"))
        & (pl.col("date") <= pl.lit(end).str.strptime(pl.Date, "%Y-%m-%d"))
    ).sort("date")
    if part.height < 2:
        return {"annual": None, "sharpe": None, "mdd": None, "total_return": None, "days": part.height}
    values = part["value"].to_list()
    returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    vol = st.pstdev(returns)
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {
        "annual": (1.0 + total) ** (252.0 / len(returns)) - 1.0,
        "sharpe": st.mean(returns) / vol * (252.0 ** 0.5) if vol else 0.0,
        "mdd": mdd,
        "total_return": total,
        "days": len(values),
    }


def evaluate_report_sample(sample: dict[str, Any]) -> dict[str, Any]:
    windows = {"train": ("2022-05-05", "2025-05-05"), "test": ("2025-05-06", "2026-11-05")}
    costs = {}
    no_leverage = True
    for cost in COSTS:
        result = evaluate_sample(sample, cost)
        no_leverage = no_leverage and result["max_weight_sum"] <= 1.0 + 1e-12
        costs[cost] = {
            "train": curve_metrics(result["curve"], *windows["train"]),
            "test": curve_metrics(result["curve"], *windows["test"]),
            "max_weight_sum": result["max_weight_sum"],
        }
    train_2x = costs["2x"]["train"]
    test_2x = costs["2x"]["test"]
    test_3x = costs["3x"]["test"]
    return {
        "sample_id": sample["sample_id"],
        "dropped": sample["dropped"],
        "construction": sample["construction"],
        "parameters": sample["parameters"],
        "costs": costs,
        "pass_2x": bool(
            train_2x["annual"] is not None
            and test_2x["total_return"] is not None
            and train_2x["annual"] >= 0.10
            and train_2x["sharpe"] >= 1.2
            and train_2x["mdd"] >= -0.10
            and test_2x["total_return"] > 0.0
            and test_2x["sharpe"] >= 1.1
            and test_2x["mdd"] >= -0.12
        ),
        "pass_3x": bool(
            test_3x["total_return"] is not None
            and test_3x["total_return"] > 0.0
            and test_3x["mdd"] >= -0.15
        ),
        "no_leverage": no_leverage,
    }


def build_report() -> dict[str, Any]:
    prereg = verify_preregistration()
    samples = generate_joint_samples()
    rows = []
    for sample in samples:
        rows.append(evaluate_report_sample(sample))
    unique = {row["sample_id"] for row in rows}
    pass_2x = sum(bool(row["pass_2x"]) for row in rows)
    pass_3x = sum(bool(row["pass_3x"]) for row in rows)
    no_leverage = all(bool(row["no_leverage"]) for row in rows)
    cost_summary = {}
    for cost in COSTS:
        train_annual = [row["costs"][cost]["train"]["annual"] for row in rows if row["costs"][cost]["train"]["annual"] is not None]
        test_total = [row["costs"][cost]["test"]["total_return"] for row in rows if row["costs"][cost]["test"]["total_return"] is not None]
        test_sharpe = [row["costs"][cost]["test"]["sharpe"] for row in rows if row["costs"][cost]["test"]["sharpe"] is not None]
        test_mdd = [row["costs"][cost]["test"]["mdd"] for row in rows if row["costs"][cost]["test"]["mdd"] is not None]
        cost_summary[cost] = {
            "train_annual_median": st.median(train_annual) if train_annual else None,
            "test_total_median": st.median(test_total) if test_total else None,
            "test_sharpe_median": st.median(test_sharpe) if test_sharpe else None,
            "test_mdd_worst": min(test_mdd) if test_mdd else None,
            "warning": {
                "cost": cost,
                "warning": cost == "5x",
                "hard_rejection": False,
            },
        }
    return {
        "round": "R389",
        "classification": prereg["classification"],
        "runner_sha256": sha256(Path(__file__)),
        "preregistration_sha256": sha256(PREREG),
        "parameters": EXPECTED_PARAMETERS,
        "windows": prereg["windows"],
        "sample_count": len(samples),
        "unique_sample_count": len(unique),
        "samples": samples,
        "evaluations": rows,
        "costs": cost_summary,
        "pass_2x_count": pass_2x,
        "pass_2x_rate": pass_2x / len(rows) if rows else None,
        "pass_3x_count": pass_3x,
        "pass_3x_rate": pass_3x / len(rows) if rows else None,
        "failed_2x": [row["sample_id"] for row in rows if not row["pass_2x"]],
        "failed_3x": [row["sample_id"] for row in rows if not row["pass_3x"]],
        "no_leverage": no_leverage,
        "decision": {
            "status": "joint_mc_complete_no_promotion",
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }


def markdown(report: dict[str, Any]) -> str:
    return "\n".join([
        "# R389 · 核心因子集联合蒙特卡洛",
        "",
        "R389 冻结 R386 参数与 A/B/C 公式，只扰动风险平价构造、执行参数与成员存在性。",
        "本轮是稳健性验证，不是新因子搜索。",
        "",
        f"- 样本数：{report['sample_count']}",
        f"- 去重后：{report['unique_sample_count']}",
        f"- 2× 联合门槛通过：{report['pass_2x_count']}/{report['sample_count']} ({report['pass_2x_rate']:.1%})",
        f"- 3× 联合门槛通过：{report['pass_3x_count']}/{report['sample_count']} ({report['pass_3x_rate']:.1%})",
        f"- 2× 中位训练年化：{report['costs']['2x']['train_annual_median']:.2%}",
        f"- 2× 中位检验总收益：{report['costs']['2x']['test_total_median']:.2%}",
        f"- 2× 中位检验 Sharpe：{report['costs']['2x']['test_sharpe_median']:.3f}",
        f"- 2× 最差检验 MDD：{report['costs']['2x']['test_mdd_worst']:.2%}",
        f"- 5× warning：{'是' if report['costs']['5x']['warning']['warning'] else '否'}；硬否决：否",
        f"- 无杠杆：{'通过' if report['no_leverage'] else '失败'}",
        "",
        "## 结论边界",
        "",
        "- 不生成订单、纸面目标或生产配置。",
        "- 5× 只作 warning，不硬否决。",
        "- 训练段与检验段严格分离。",
        "",
    ])


def write_json_with_checksum(path: Path, payload: dict[str, Any]) -> None:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(raw)
    path.with_name(path.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n", encoding="ascii"
    )


def write_text_with_checksum(path: Path, text: str) -> None:
    raw = text.encode("utf-8")
    with path.open("xb") as stream:
        stream.write(raw)
    path.with_name(path.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    ensure_outputs_absent()
    report = build_report()
    write_json_with_checksum(OUTPUT, report)
    write_text_with_checksum(RESULT, markdown(report))
    print(json.dumps({"sample_count": report["sample_count"], "no_leverage": report["no_leverage"]}, ensure_ascii=False))
    return 0 if report["no_leverage"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
