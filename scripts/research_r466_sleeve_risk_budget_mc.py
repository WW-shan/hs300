#!/usr/bin/env python3
"""R466: sleeve-level risk-budget Monte Carlo on the R464 factor library."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r466_sleeve_risk_budget_mc_preregistration.json"
OUTPUT = E / "r466_sleeve_risk_budget_mc_2026-11-22.json"
RESULT = E / "r466_sleeve_risk_budget_mc_2026-11-22.md"
FULL_START = "2022-05-05"
FULL_END = "2026-11-05"
UNIVERSES = ("core5", "seven_vq", "compact4", "extended9")
METHODS = ("equal", "inverse_vol", "erc", "min_variance")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R466 output exists; refusing overwrite: {path.name}")


def load_prereg() -> dict[str, Any]:
    verify_sidecar(PREREG)
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R466":
        raise RuntimeError("R466 round mismatch")
    if payload["selection"]["basis"] != "train_and_validation_only":
        raise RuntimeError("R466 selection basis changed")
    if payload["execution"]["five_x_hard_rejection"] is not False:
        raise RuntimeError("R466 five-x must remain warning-only")
    return payload


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r466_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_r464() -> Any:
    path = ROOT / "scripts" / "research_r464_joint_factor_library_mc.py"
    spec = importlib.util.spec_from_file_location("r466_r464", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_r453() -> Any:
    path = ROOT / "scripts" / "research_r453_value_quality_portfolio.py"
    spec = importlib.util.spec_from_file_location("r466_r453", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _variant_id(universe: str, method: str, lookback: int, rebalance: int, max_weight: float) -> str:
    return f"{universe}|{method}|lb{lookback}|rb{rebalance}|cap{max_weight:.2f}"


def generate_variants() -> list[dict[str, Any]]:
    prereg = load_prereg()
    rows: list[dict[str, Any]] = []
    for universe, sleeve_names in prereg["universes"].items():
        n = len(sleeve_names)
        for method in METHODS:
            # Equal weight has no volatility estimate. Keep its four variants
            # for rebalance/cap sensitivity; dynamic methods use the full grid.
            lookbacks = (60,) if method == "equal" else tuple(prereg["parameter_space"]["lookback_days"])
            rebalances = tuple(prereg["parameter_space"]["rebalance_days"])
            caps = tuple(prereg["parameter_space"]["max_weight"])
            for lookback in lookbacks:
                for rebalance in rebalances:
                    for max_weight in caps:
                        weights = {name: 1.0 / n for name in sleeve_names}
                        rows.append(
                            {
                                "id": _variant_id(universe, method, int(lookback), int(rebalance), float(max_weight)),
                                "universe": universe,
                                "sleeve_names": list(sleeve_names),
                                "method": method,
                                "lookback": int(lookback),
                                "rebalance": int(rebalance),
                                "max_weight": float(max_weight),
                                "weights": weights,
                            }
                        )
    return rows


def _cap_weights(raw: np.ndarray, *, max_weight: float, min_weight: float) -> np.ndarray:
    values = np.asarray(raw, dtype=float)
    n = values.size
    if n == 0:
        raise ValueError("weights require at least one sleeve")
    if max_weight * n < 1.0 - 1e-12 or min_weight * n > 1.0 + 1e-12:
        raise ValueError("weight bounds cannot sum to one")
    values = np.where(np.isfinite(values), np.maximum(values, 0.0), 0.0)
    if values.sum() <= 1e-15:
        values = np.ones(n, dtype=float)
    values = values / values.sum()
    for _ in range(200):
        below = values < min_weight - 1e-12
        above = values > max_weight + 1e-12
        if not (below.any() or above.any()):
            break
        fixed = below | above
        values[below] = min_weight
        values[above] = max_weight
        free = ~fixed
        remaining = 1.0 - float(values[fixed].sum())
        if not free.any():
            break
        free_sum = float(values[free].sum())
        if free_sum <= 1e-15:
            values[free] = remaining / int(free.sum())
        else:
            values[free] = values[free] / free_sum * remaining
    values = np.clip(values, min_weight, max_weight)
    total = float(values.sum())
    if total <= 0:
        values = np.ones(n, dtype=float) / n
    else:
        values /= total
    # Final correction for numerical clipping.
    for _ in range(50):
        if values.max() <= max_weight + 1e-10 and values.min() >= min_weight - 1e-10:
            break
        values = _cap_weights(values, max_weight=max_weight, min_weight=min_weight) if False else values
        values = np.clip(values, min_weight, max_weight)
        values /= values.sum()
    return values


def estimate_weights(
    returns: np.ndarray,
    method: str,
    *,
    max_weight: float,
    min_weight: float,
) -> list[float]:
    matrix = np.asarray(returns, dtype=float)
    if matrix.ndim == 1:
        matrix = matrix.reshape(-1, 1)
    if matrix.shape[0] < 2 or matrix.shape[1] == 0:
        return [1.0 / matrix.shape[1]] * matrix.shape[1]
    matrix = np.nan_to_num(matrix, nan=0.0, posinf=0.0, neginf=0.0)
    n = matrix.shape[1]
    if method == "equal":
        return [1.0 / n] * n
    if method == "inverse_vol":
        vol = matrix.std(axis=0, ddof=1)
        raw = 1.0 / np.maximum(vol, 1e-8)
    else:
        cov = np.cov(matrix, rowvar=False, ddof=1)
        cov = np.atleast_2d(cov)
        cov = np.nan_to_num(cov, nan=0.0, posinf=0.0, neginf=0.0)
        cov += np.eye(n) * max(1e-10, float(np.trace(cov)) / max(n, 1) * 1e-6)
        if method == "min_variance":
            inv = np.linalg.pinv(cov)
            raw = np.maximum(inv @ np.ones(n), 0.0)
            if raw.sum() <= 1e-12:
                raw = 1.0 / np.maximum(np.diag(cov), 1e-8)
        elif method == "erc":
            raw = np.ones(n, dtype=float)
            for _ in range(300):
                raw = _cap_weights(raw, max_weight=max_weight, min_weight=min_weight)
                marginal = cov @ raw
                contribution = raw * marginal
                target = max(float(contribution.mean()), 1e-12)
                raw = raw * (target / np.maximum(contribution, 1e-12))
                raw = np.maximum(raw, 0.0)
                if raw.sum() <= 1e-12:
                    raw = np.ones(n, dtype=float)
        else:
            raise ValueError(f"unsupported weight method: {method}")
    return [float(value) for value in _cap_weights(np.asarray(raw, dtype=float), max_weight=max_weight, min_weight=min_weight)]


def align_curves(curves: dict[str, list[dict[str, Any]]], names: list[str]) -> tuple[list[str], np.ndarray]:
    date_maps = {
        name: {str(item["date"])[:10]: float(item["value"]) for item in curves[name]}
        for name in names
    }
    dates = sorted(set.intersection(*(set(mapping) for mapping in date_maps.values())))
    if len(dates) < 3:
        raise RuntimeError("sleeve curves have insufficient common dates")
    values = np.array([[date_maps[name][day] for name in names] for day in dates], dtype=float)
    returns = values[1:] / values[:-1] - 1.0
    return dates, returns


def overlay_curve(
    curves: dict[str, list[dict[str, Any]]],
    variant: dict[str, Any],
    *,
    min_weight: float,
    turnover_cost_bps: float,
) -> list[dict[str, Any]]:
    names = list(variant["sleeve_names"])
    dates, returns = align_curves(curves, names)
    weights = np.array([float(variant["weights"][name]) for name in names], dtype=float)
    weights = _cap_weights(weights, max_weight=float(variant["max_weight"]), min_weight=min_weight)
    lookback = int(variant["lookback"])
    rebalance = int(variant["rebalance"])
    cost = float(turnover_cost_bps) / 10_000.0
    value = 1.0
    out = [{"date": dates[0], "value": value}]
    for index in range(1, len(dates)):
        turnover = 0.0
        # Rebalance before earning today's return. History ends at index-1,
        # so today's return is never used to estimate today's weights.
        if index > 1 and (index - 1) % rebalance == 0:
            history = returns[max(0, index - 1 - lookback) : index - 1]
            new_weights = np.array(
                estimate_weights(history, variant["method"], max_weight=float(variant["max_weight"]), min_weight=min_weight),
                dtype=float,
            )
            turnover = 0.5 * float(np.abs(new_weights - weights).sum())
            weights = new_weights
        ret = float(np.dot(weights, returns[index - 1])) - turnover * cost
        value *= 1.0 + ret
        out.append({"date": dates[index], "value": value})
    return out


def path_metrics(curve: list[dict[str, Any]]) -> dict[str, float]:
    if len(curve) < 2:
        return {"total_return": -1.0, "annual": -1.0, "sharpe": -1.0, "mdd": -1.0}
    values = [float(item["value"]) for item in curve]
    returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    mean = sum(returns) / len(returns)
    vol = (sum((item - mean) ** 2 for item in returns) / len(returns)) ** 0.5
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {
        "total_return": total,
        "annual": (1.0 + total) ** (252.0 / len(returns)) - 1.0 if returns else 0.0,
        "sharpe": mean / vol * math.sqrt(252.0) if vol else 0.0,
        "mdd": mdd,
    }


def slice_curve(curve: list[dict[str, Any]], start: str, end: str) -> list[dict[str, Any]]:
    return [item for item in curve if start <= str(item["date"])[:10] <= end]


def _bootstrap(curve: list[dict[str, Any]], prereg: dict[str, Any], r453: Any, seed_offset: int = 0) -> dict[str, float]:
    return r453.bootstrap(
        curve,
        block=int(prereg["bootstrap"]["block_days"]),
        paths=int(prereg["bootstrap"]["replications"]),
        seed=int(prereg["bootstrap"]["seed"]) + seed_offset,
    )


def build_sleeve_curves(
    r464: Any,
    p5: Any,
    service: Any,
    panel: pl.DataFrame,
    symbols: list[str],
    prereg: dict[str, Any],
    *,
    cost_label: str,
    sleeve_names: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    selected = sleeve_names or list(prereg["sleeves"])
    curves: dict[str, list[dict[str, Any]]] = {}
    for name in selected:
        factor = prereg["sleeves"][name]
        sample = {
            "weights": {factor: 1.0},
            "max_positions": int(prereg["base_execution"]["max_positions"]),
            "max_hold_days": int(prereg["base_execution"]["max_hold_days"]),
            "max_exposure_pct": float(prereg["base_execution"]["max_exposure_pct"]),
            "entry_score": float(prereg["base_execution"]["entry_score"]),
            "exit_score": float(prereg["base_execution"]["exit_score"]),
        }
        result = r464._run_config(
            p5,
            service,
            panel,
            symbols,
            sample,
            start=FULL_START,
            end=FULL_END,
            cost_label=cost_label,
        )
        if result.get("error") or len(result.get("curve", [])) < 3:
            raise RuntimeError(f"sleeve curve unavailable: {name}")
        curves[name] = list(result["curve"])
    return curves


def _training_gate(row: dict[str, Any], prereg: dict[str, Any]) -> bool:
    train = row["train"]
    validation = row["validation"]
    gate = prereg["selection"]
    return bool(
        train["annual"] >= gate["train_annual_min"]
        and validation["annual"] >= gate["validation_annual_min"]
        and validation["sharpe"] >= gate["validation_sharpe_min"]
        and validation["mdd"] >= gate["validation_mdd_min"]
    )


def _local_variants(variant: dict[str, Any], all_variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Compare all parameter points within the same universe and method. This
    # is a local parameter platform, not an independent trial set.
    return [
        row for row in all_variants
        if row["universe"] == variant["universe"] and row["method"] == variant["method"]
    ]


def main() -> int:
    ensure_outputs_absent()
    prereg = load_prereg()
    p5 = load_p5()
    r464 = load_r464()
    r453 = load_r453()
    panel, symbols = r464.build_panel(p5)
    service = p5.portfolio._service()
    curves_2x = build_sleeve_curves(r464, p5, service, panel, symbols, prereg, cost_label="2x")
    curves_3x = build_sleeve_curves(r464, p5, service, panel, symbols, prereg, cost_label="3x")
    variants = generate_variants()
    windows = prereg["windows"]
    for variant in variants:
        curve = overlay_curve(
            curves_2x,
            variant,
            min_weight=float(prereg["sleeve_overlay"]["min_weight"]),
            turnover_cost_bps=float(prereg["sleeve_overlay"]["turnover_cost_bps"]),
        )
        variant["train"] = path_metrics(slice_curve(curve, windows["train_start"], windows["train_end"]))
        variant["validation"] = path_metrics(slice_curve(curve, windows["validation_start"], windows["validation_end"]))
        variant["training_gate"] = _training_gate(variant, prereg)
    eligible = [row for row in variants if row["training_gate"]]
    eligible.sort(key=lambda row: (min(row["train"]["annual"], row["validation"]["annual"]), row["validation"]["sharpe"]), reverse=True)
    finalists = eligible[: int(prereg["selection"]["bootstrap_finalists"])]
    for row in finalists:
        curve = overlay_curve(
            curves_2x,
            row,
            min_weight=float(prereg["sleeve_overlay"]["min_weight"]),
            turnover_cost_bps=float(prereg["sleeve_overlay"]["turnover_cost_bps"]),
        )
        row["validation_bootstrap"] = _bootstrap(slice_curve(curve, windows["validation_start"], windows["validation_end"]), prereg, r453, seed_offset=100)
    bootstrap_positive = [row for row in finalists if row["validation_bootstrap"]["total_p05"] > 0.0]
    neighborhood_finalists = bootstrap_positive[: int(prereg["selection"]["neighborhood_finalists"])]
    for row in neighborhood_finalists:
        local = _local_variants(row, variants)
        passed = 0
        for item in local:
            curve = overlay_curve(
                curves_2x,
                item,
                min_weight=float(prereg["sleeve_overlay"]["min_weight"]),
                turnover_cost_bps=float(prereg["sleeve_overlay"]["turnover_cost_bps"]),
            )
            item["train"] = path_metrics(slice_curve(curve, windows["train_start"], windows["train_end"]))
            item["validation"] = path_metrics(slice_curve(curve, windows["validation_start"], windows["validation_end"]))
            item["training_gate"] = _training_gate(item, prereg)
            passed += int(item["training_gate"])
        row["neighborhood"] = {"points": len(local), "pass_count": passed, "pass_rate": passed / len(local) if local else 0.0}
    stable = [row for row in neighborhood_finalists if row["neighborhood"]["pass_rate"] >= prereg["selection"]["neighborhood_pass_rate_min"]]
    stable.sort(key=lambda row: (min(row["train"]["annual"], row["validation"]["annual"]), row["validation_bootstrap"]["total_p05"]), reverse=True)
    selected = stable[0] if stable else (bootstrap_positive[0] if bootstrap_positive else (eligible[0] if eligible else variants[0]))
    test_curve = overlay_curve(curves_2x, selected, min_weight=float(prereg["sleeve_overlay"]["min_weight"]), turnover_cost_bps=float(prereg["sleeve_overlay"]["turnover_cost_bps"]))
    recent_curve = test_curve
    selected["test"] = path_metrics(slice_curve(test_curve, windows["test_start"], windows["test_end"]))
    selected["recent"] = path_metrics(slice_curve(recent_curve, windows["recent_start"], windows["test_end"]))
    stress_curve = overlay_curve(curves_3x, selected, min_weight=float(prereg["sleeve_overlay"]["min_weight"]), turnover_cost_bps=float(prereg["sleeve_overlay"]["turnover_cost_bps"]))
    selected["test_3x"] = path_metrics(slice_curve(stress_curve, windows["test_start"], windows["test_end"]))
    selected["test_bootstrap"] = _bootstrap(slice_curve(test_curve, windows["test_start"], windows["test_end"]), prereg, r453, seed_offset=1)
    selected["recent_bootstrap"] = _bootstrap(slice_curve(recent_curve, windows["recent_start"], windows["test_end"]), prereg, r453, seed_offset=2)
    curves_5x = build_sleeve_curves(r464, p5, service, panel, symbols, prereg, cost_label="5x", sleeve_names=list(selected["sleeve_names"]))
    diagnostic_curve = overlay_curve(curves_5x, selected, min_weight=float(prereg["sleeve_overlay"]["min_weight"]), turnover_cost_bps=float(prereg["sleeve_overlay"]["turnover_cost_bps"]))
    selected["test_5x"] = path_metrics(slice_curve(diagnostic_curve, windows["test_start"], windows["test_end"]))
    checks = {
        "training_platform_exists": bool(eligible),
        "validation_bootstrap_positive": selected.get("validation_bootstrap", {}).get("total_p05", -1.0) > 0.0,
        "neighborhood_platform_pass": bool(selected.get("neighborhood")) and selected["neighborhood"]["pass_rate"] >= prereg["selection"]["neighborhood_pass_rate_min"],
        "test_annual_min": selected["test"]["annual"] >= prereg["confirmation_gates"]["test_annual_min"],
        "test_sharpe_min": selected["test"]["sharpe"] >= prereg["confirmation_gates"]["test_sharpe_min"],
        "test_mdd_min": selected["test"]["mdd"] >= prereg["confirmation_gates"]["test_mdd_min"],
        "recent_annual_min": selected["recent"]["annual"] >= prereg["confirmation_gates"]["recent_annual_min"],
        "stress_3x_positive": selected["test_3x"]["annual"] > 0.0,
        "test_total_p05_positive": selected["test_bootstrap"]["total_p05"] > 0.0,
        "recent_total_p05_positive": selected["recent_bootstrap"]["total_p05"] > 0.0,
        "no_leverage": True,
    }
    payload = {
        "round": "R466",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__)),
        "selection_basis": "train_and_validation_only",
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "sleeve_curve_symbols": len(symbols),
        "sleeve_curve_rows": {name: len(curve) for name, curve in curves_2x.items()},
        "variant_count": len(variants),
        "eligible_count": len(eligible),
        "bootstrap_positive_count": len(bootstrap_positive),
        "neighborhood_stable_count": len(stable),
        "variants": variants,
        "selected": selected,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "diagnostics": {"five_x_warning_only": True, "no_leverage": True, "test_5x": selected["test_5x"]},
        "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R466 因子袖风险预算蒙特卡洛",
        "",
        "本轮在 R464 因子袖和 R465 尾部诊断之后，检验 sleeve 层等权、逆波动、ERC、最小方差风险预算能否改善尾部；训练+验证只选样，检验+近一年只确认。",
        "",
        f"- 单因子袖：{len(curves_2x)} 个",
        f"- 组合样本：{len(variants)}",
        f"- 训练+验证合格：{len(eligible)}",
        f"- 验证 bootstrap 正尾：{len(bootstrap_positive)}",
        f"- 邻域稳定：{len(stable)}",
        f"- 选中：`{selected['id']}`",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        f"- 检验年化：{selected['test']['annual']:.2%}",
        f"- 检验 Sharpe：{selected['test']['sharpe']:.3f}",
        f"- 检验 MDD：{selected['test']['mdd']:.2%}",
        f"- 近一年年化：{selected['recent']['annual']:.2%}",
        f"- 检验 p05：{selected['test_bootstrap']['total_p05']:.2%}",
        f"- 近一年 p05：{selected['recent_bootstrap']['total_p05']:.2%}",
        "",
        "## 门槛",
        "",
    ]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 选中规则", "", f"- universe：{selected['universe']}", f"- method：{selected['method']}", f"- lookback：{selected['lookback']}", f"- rebalance：{selected['rebalance']}", f"- max_weight：{selected['max_weight']:.2f}", f"- 3× 检验年化：{selected['test_3x']['annual']:.2%}", f"- 5× warning 检验年化：{selected['test_5x']['annual']:.2%}", "", "## 边界", ""]
    lines += [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
