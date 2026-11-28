"""Standard research validation receipts, constraints, and chart artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

SUITE_VERSION = "v1"


def _directional_png_path(path: Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _draw_axes(draw: ImageDraw.ImageDraw, width: int, height: int, title: str) -> tuple[int, int, int, int]:
    draw.rectangle((40, 40, width - 30, height - 50), outline=(40, 40, 40), width=2)
    draw.text((40, 15), title, fill=(20, 20, 20))
    return 60, 60, width - 50, height - 70


def _text(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, fill=(30, 30, 30)) -> None:
    draw.text((x, y), text, fill=fill)


def _save(image: Image.Image, path: Path) -> Path:
    target = _directional_png_path(path)
    image.save(target, format="PNG")
    return target


def _checksum(path: Path) -> dict[str, str]:
    return {
        "path": path.as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def build_standard_validation_receipt(
    gbm: dict[str, Any],
    parameter_mc: dict[str, Any],
    *,
    artifacts: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "suite_version": SUITE_VERSION,
        "gbm": dict(gbm),
        "parameter_mc": dict(parameter_mc),
        "artifacts": [dict(item) for item in artifacts],
    }


def check_standard_validation_constraints(
    receipt: dict[str, Any],
    *,
    research_type: str = "project_formal",
) -> dict[str, Any]:
    errors: list[str] = []
    gbm = receipt.get("gbm") or {}
    parameter = receipt.get("parameter_mc") or {}
    artifacts = receipt.get("artifacts") or []

    if int(receipt.get("schema_version") or 0) != 1:
        errors.append("schema_version_1")
    if receipt.get("suite_version") != SUITE_VERSION:
        errors.append("suite_version_v1")

    if research_type in {"project_formal", "synthetic_market_stress"}:
        if gbm.get("status") != "complete":
            errors.append("gbm_status_complete")
        if int(gbm.get("path_count") or 0) < 100:
            errors.append("gbm_path_count_ge_100")
        if int(gbm.get("scenarios") or 0) < 4:
            errors.append("gbm_scenarios_ge_4")
        if sorted(int(value) for value in gbm.get("cost_multipliers") or []) != [2, 3, 5]:
            errors.append("gbm_costs_2_3_5")
        if gbm.get("same_path_benchmark") is not True:
            errors.append("gbm_same_path_benchmark")
        if gbm.get("deterministic_replay") is not True:
            errors.append("gbm_deterministic_replay")
        if gbm.get("no_leverage") is not True:
            errors.append("gbm_no_leverage")
        if not isinstance(gbm.get("max_exposure"), (int, float)) or float(gbm["max_exposure"]) > 1.0:
            errors.append("gbm_max_exposure_le_1")
        if len(artifacts) < 2:
            errors.append("gbm_artifacts_min_2")

    if parameter.get("status") == "complete":
        if parameter.get("selection_policy") != "train_select_validation_confirm":
            errors.append("parameter_selection_policy")
        if parameter.get("recent_used_for_selection") is not False:
            errors.append("parameter_recent_not_used_for_selection")
        if int(parameter.get("grid_size") or 0) < 1:
            errors.append("parameter_grid_nonempty")
        if not isinstance(parameter.get("selected"), dict):
            errors.append("parameter_selected")
        if (parameter.get("constraints") or {}).get("validation_confirmed") is not True:
            errors.append("parameter_validation_confirmed")
        if not isinstance(parameter.get("bootstrap"), dict) or not parameter.get("bootstrap"):
            errors.append("parameter_bootstrap_present")
        if not any(str(item.get("kind", "")).startswith("parameter_") for item in artifacts):
            errors.append("parameter_artifacts_present")
    elif parameter.get("status") in {"fixed_frozen", "not_applicable"}:
        if not parameter.get("reason"):
            errors.append("parameter_inapplicability_reason")
    else:
        errors.append("parameter_status_allowed")

    return {
        "pass": not errors,
        "errors": errors,
        "research_type": research_type,
    }


def _quantiles(values: Sequence[float]) -> dict[str, float]:
    array = np.asarray(list(values), dtype=np.float64)
    return {
        "p05": round(float(np.quantile(array, 0.05)), 6),
        "p50": round(float(np.quantile(array, 0.50)), 6),
        "p95": round(float(np.quantile(array, 0.95)), 6),
    }


def summarize_gbm_paths(
    paths: Sequence[dict[str, Any]],
    *,
    path_count: int,
    scenarios: set[str],
    costs: Sequence[int],
    deterministic_replay: bool,
) -> dict[str, Any]:
    observations = [
        row
        for path in paths
        for row in path.get("observations", [])
    ]
    errors: list[str] = []
    expected = int(path_count) * len(scenarios) * 2 * len(costs)
    if len(observations) != expected:
        errors.append(f"observation_count:{len(observations)}!={expected}")
    for scenario in sorted(scenarios):
        scenario_rows = [row for row in observations if row.get("scenario_id") == scenario]
        if len(scenario_rows) != int(path_count) * 2 * len(costs):
            errors.append(f"scenario_incomplete:{scenario}")
    for cost in costs:
        returns = [
            float(row.get("strategy_annual_return") or 0.0)
            for row in observations
            if int(row.get("cost_multiplier") or 0) == int(cost)
        ]
        if not returns:
            errors.append(f"cost_missing:{cost}")
    max_exposure = max(
        (float(row.get("strategy_max_exposure") or 0.0) for row in observations),
        default=0.0,
    )
    if max_exposure > 1.000001:
        errors.append("max_exposure_gt_1")
    if not deterministic_replay:
        errors.append("deterministic_replay")

    scenario_summary: dict[str, dict[str, float]] = {}
    for scenario in sorted(scenarios):
        rows = [row for row in observations if row.get("scenario_id") == scenario]
        excess = [float(row.get("excess_return") or 0.0) for row in rows]
        mdd = [float(row.get("strategy_max_drawdown") or 0.0) for row in rows]
        sharpe = [float(row.get("strategy_sharpe") or 0.0) for row in rows]
        scenario_summary[scenario] = {
            "excess_p05": _quantiles(excess)["p05"],
            "excess_p50": _quantiles(excess)["p50"],
            "excess_p95": _quantiles(excess)["p95"],
            "positive_excess_ratio": round(sum(value > 0 for value in excess) / len(excess), 6)
            if excess else 0.0,
            "mdd_p50": round(float(np.quantile(mdd, 0.50)), 6) if mdd else 0.0,
            "mdd_p95": round(float(np.quantile(mdd, 0.95)), 6) if mdd else 0.0,
            "sharpe_p50": round(float(np.quantile(sharpe, 0.50)), 6) if sharpe else 0.0,
        }
    cost_summary: dict[str, dict[str, float]] = {}
    for cost in costs:
        rows = [row for row in observations if int(row.get("cost_multiplier") or 0) == int(cost)]
        annual = [float(row.get("strategy_annual_return") or 0.0) for row in rows]
        mdd = [float(row.get("strategy_max_drawdown") or 0.0) for row in rows]
        excess = [float(row.get("excess_return") or 0.0) for row in rows]
        cost_summary[str(cost)] = {
            "annual_return_p50": round(float(np.quantile(annual, 0.50)), 6) if annual else 0.0,
            "mdd_p50": round(float(np.quantile(mdd, 0.50)), 6) if mdd else 0.0,
            "excess_p50": round(float(np.quantile(excess, 0.50)), 6) if excess else 0.0,
        }
    return {
        "status": "complete" if not errors else "failed",
        "path_count": int(path_count),
        "scenarios": len(scenarios),
        "cost_multipliers": list(costs),
        "same_path_benchmark": True,
        "deterministic_replay": bool(deterministic_replay),
        "no_leverage": max_exposure <= 1.000001,
        "max_exposure": round(max_exposure, 6),
        "summary": scenario_summary,
        "cost_summary": cost_summary,
        "constraints": {"complete": not errors, "errors": errors},
        "observation_count": len(observations),
    }


def plot_gbm_distribution(summary: dict[str, Any], path: Path) -> Path:
    image = Image.new("RGB", (1200, 720), "white")
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = _draw_axes(draw, 1200, 720, "GBM scenario excess-return p05 / p50 / p95")
    scenario_names = list(summary)
    if not scenario_names:
        _text(draw, left, top, "no data")
        return _save(image, path)
    width = (right - left) / max(len(scenario_names), 1)
    all_values = [
        float(values.get(key) or 0.0)
        for values in summary.values()
        for key in ("excess_p05", "excess_p50", "excess_p95")
    ]
    low = min(all_values)
    high = max(all_values)
    span = max(0.01, high - low)
    for index, name in enumerate(scenario_names):
        values = summary[name]
        base_x = left + index * width + 18
        for offset, (key, color) in enumerate(
            (("excess_p05", (210, 70, 70)), ("excess_p50", (60, 110, 210)), ("excess_p95", (50, 160, 100)))
        ):
            value = float(values.get(key) or 0.0)
            x = int(base_x + offset * 18)
            y = int(bottom - (value - low) / span * (bottom - top))
            draw.line((x, bottom, x, y), fill=color, width=8)
        _text(draw, int(base_x), bottom + 10, str(name)[:14])
    return _save(image, path)


def plot_gbm_constraints(cost_summary: dict[str, Any], path: Path) -> Path:
    image = Image.new("RGB", (900, 520), "white")
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = _draw_axes(draw, 900, 520, "GBM cost stress: p50 annual return")
    costs = list(cost_summary)
    values = [float((cost_summary.get(str(cost)) or cost_summary.get(cost) or {}).get("annual_return_p50") or 0.0) for cost in costs]
    span = max(0.01, max(values, default=0.0) - min([*values, 0.0], default=0.0))
    zero_y = int(bottom - (0.0 - min([*values, 0.0], default=0.0)) / span * (bottom - top))
    draw.line((left, zero_y, right, zero_y), fill=(100, 100, 100), width=1)
    bar_width = (right - left) / max(len(costs), 1) * 0.55
    for index, (cost, value) in enumerate(zip(costs, values, strict=True)):
        x = int(left + (index + 0.5) * (right - left) / max(len(costs), 1) - bar_width / 2)
        y = int(bottom - (value - min([*values, 0.0], default=0.0)) / span * (bottom - top))
        draw.rectangle((x, min(y, zero_y), int(x + bar_width), max(y, zero_y)), fill=(70, 120, 190))
        _text(draw, x, bottom + 10, f"{cost}x")
        _text(draw, x, min(y, zero_y) - 18, f"{value:.2%}")
    return _save(image, path)


def plot_parameter_paths(points: list[dict[str, Any]], path: Path) -> Path:
    image = Image.new("RGB", (1100, 700), "white")
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = _draw_axes(draw, 1100, 700, "Parameter MC: train vs validation return")
    if not points:
        _text(draw, left, top, "no data")
        return _save(image, path)
    xs = list(range(len(points)))
    all_values = [float(point.get(key) or 0.0) for point in points for key in ("train_return", "validation_return")]
    low = min([*all_values, 0.0])
    high = max([*all_values, 0.0])
    span = max(0.01, high - low)
    for key, color in (("train_return", (50, 110, 200)), ("validation_return", (190, 80, 70))):
        previous = None
        for x, point in zip(xs, points, strict=True):
            px = int(left + (right - left) * (x / max(len(points) - 1, 1)))
            py = int(bottom - (float(point.get(key) or 0.0) - low) / span * (bottom - top))
            if previous is not None:
                draw.line((previous[0], previous[1], px, py), fill=color, width=3)
            previous = (px, py)
    zero_y = int(bottom - (0.0 - low) / span * (bottom - top))
    draw.line((left, zero_y, right, zero_y), fill=(100, 100, 100), width=1)
    return _save(image, path)


def plot_parameter_pareto(points: list[dict[str, Any]], path: Path) -> Path:
    image = Image.new("RGB", (1100, 700), "white")
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = _draw_axes(draw, 1100, 700, "Parameter MC: train return vs MDD")
    returns = [float(point.get("train_return") or 0.0) for point in points]
    mdds = [float(point.get("mdd") or 0.0) for point in points]
    low_r, high_r = min([*returns, 0.0]), max([*returns, 0.0])
    low_m, high_m = min([*mdds, 0.0]), max([*mdds, 0.0])
    for point in points:
        r = float(point.get("train_return") or 0.0)
        m = float(point.get("mdd") or 0.0)
        x = int(left + (r - low_r) / max(0.01, high_r - low_r) * (right - left))
        y = int(bottom - (m - low_m) / max(0.01, high_m - low_m) * (bottom - top))
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=(70, 120, 190))
    return _save(image, path)


def plot_parameter_bootstrap(distributions: dict[str, Any], path: Path) -> Path:
    image = Image.new("RGB", (1100, 620), "white")
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = _draw_axes(draw, 1100, 620, "Parameter MC bootstrap distribution")
    items = list(distributions.items())
    if not items:
        _text(draw, left, top, "no data")
        return _save(image, path)
    values = []
    for _, dist in items:
        if isinstance(dist, dict):
            values.extend(float(dist.get(key) or 0.0) for key in ("p05", "p50", "p95"))
    low, high = min([*values, 0.0]), max([*values, 0.0])
    span = max(0.01, high - low)
    width = (right - left) / max(len(items), 1)
    for index, (name, dist) in enumerate(items):
        d = dist if isinstance(dist, dict) else {}
        x = int(left + (index + 0.5) * width)
        for key, color in (("p05", (200, 70, 70)), ("p50", (60, 110, 210)), ("p95", (50, 160, 100))):
            value = float(d.get(key) or 0.0)
            y = int(bottom - (value - low) / span * (bottom - top))
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=color)
        _text(draw, int(x - 20), bottom + 10, str(name)[:12])
    return _save(image, path)


def plot_parameter_artifacts(
    points: list[dict[str, Any]],
    bootstrap: dict[str, Any],
    output_directory: Path,
    *,
    prefix: str,
) -> tuple[Path, Path, Path]:
    root = Path(output_directory)
    paths = (
        plot_parameter_paths(points, root / f"{prefix}_parameter_paths.png"),
        plot_parameter_pareto(points, root / f"{prefix}_parameter_pareto.png"),
        plot_parameter_bootstrap(bootstrap, root / f"{prefix}_parameter_bootstrap.png"),
    )
    return paths


def artifact_manifest(paths: Sequence[Path], *, kind_prefix: str) -> list[dict[str, str]]:
    return [
        {"kind": f"{kind_prefix}_{index}", **_checksum(path)}
        for index, path in enumerate(paths)
    ]
