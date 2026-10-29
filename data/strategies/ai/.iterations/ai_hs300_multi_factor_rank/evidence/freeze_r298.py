from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "CONTRIBUTING.md").is_file()
)
EVIDENCE = Path(__file__).resolve().parent
ROUND = "R298"
START = date(2022, 5, 5)
END = date(2023, 4, 28)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    digest = hashlib.sha256(payload).hexdigest()
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def load_script(relative: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def input_paths() -> list[Path]:
    fixed = [
        "CONTRIBUTING.md",
        "docs/secondary-development.md",
        "backend/app/api/backtest.py",
        "backend/app/backtest/engine.py",
        "backend/app/backtest/strategy.py",
        "backend/app/backtest/fundamentals.py",
        "backend/app/backtest/matrix.py",
        "backend/app/factors/ext_factors.py",
        "backend/app/factors/registry.py",
        "backend/app/factors/store.py",
        "backend/app/services/industry_map.py",
        "backend/app/strategy/config.py",
        "backend/app/strategy/engine.py",
        "backend/app/tickflow/repository.py",
        "scripts/backtest_r112_dual_sleeve_risk_parity.py",
        "scripts/backtest_r166_weak_regime_factor_scan.py",
        "scripts/backtest_r221_sga_nonfin_quality.py",
        "scripts/backtest_r229_ocf_margin.py",
        "scripts/backtest_r239_concentration_audit.py",
        "scripts/backtest_r253_min_rank.py",
        "scripts/backtest_r257_quality_relative_strength.py",
        "scripts/build_r257_quality_relative_strength_factor.py",
        "scripts/build_alt_factor_data.py",
        "data/research/r78_daily_membership/daily_snapshots.parquet",
        "data/research/r157_membership/monthly_snapshots.parquet",
        "data/strategies/ai/ai_hs300_quality_relative_strength_h20_sl20.py",
        "data/strategies/ai/ai_hs300_quality_relative_strength_control.py",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r257_preregistration.json",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r257_data_feasibility.json",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r257_quality_relative_strength.json",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r298_pre_registration_feasibility.json",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/freeze_r298.py",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r298_temporal_holdout.py",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/register_r298_panel_once.py",
        "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r298_manifest_execution_addendum.json",
    ]
    paths = {ROOT / relative for relative in fixed}
    if not all(path.is_file() for path in paths):
        missing = sorted(str(path.relative_to(ROOT)) for path in paths if not path.is_file())
        raise FileNotFoundError(f"missing frozen inputs: {missing}")

    patterns = [
        "data/kline_daily_enriched/date=*/part.parquet",
        "data/kline_index_daily/date=*/part.parquet",
        "data/ext_data/alt_hs300/timeseries/date=*/part.parquet",
        "data/ext_data/alt_hs300_minrank/timeseries/date=*/part.parquet",
        "data/ext_data/alt_hs300_qrs/timeseries/date=*/part.parquet",
        "data/ext_data/ext_hy_ths/**/*",
        "data/financials/**/*",
        "data/instruments/**/*",
    ]
    for pattern in patterns:
        matches = list(ROOT.glob(pattern))
        if pattern.endswith("part.parquet") and not matches:
            raise FileNotFoundError(f"no files match {pattern}")
        paths.update(path for path in matches if path.is_file())
    return sorted(paths)


def main() -> int:
    sys.path.insert(0, str(ROOT / "backend"))
    import polars as pl

    from datetime import date as date_type

    r221 = load_script("scripts/backtest_r221_sga_nonfin_quality.py", "r221_r298_freeze")
    r229 = load_script("scripts/backtest_r229_ocf_margin.py", "r229_r298_freeze")
    r257 = load_script("scripts/backtest_r257_quality_relative_strength.py", "r257_r298_freeze")

    start = date_type(2022, 5, 5)
    end = date_type(2023, 4, 28)
    calendar = r229._benchmark_dates(start, end)
    members = r221._nonfin_membership(r221._membership_with_industry())
    factor = r257._factor_rows().filter(
        (pl.col("date") >= start) & (pl.col("date") <= end)
    )
    eligible = r257._member_daily(calendar, members).join(
        factor, on=["date", "symbol"], how="left"
    )
    valid = eligible.filter(pl.col("quality_relative_strength_min_rank").is_not_null())
    if not calendar or eligible.is_empty():
        raise RuntimeError("R298 feasibility window or member panel is empty")

    feasibility = {
        "round": ROUND,
        "classification": "pre_registration_feasibility_only_no_forward_returns_or_strategy_results_read",
        "window": [str(start), str(end)],
        "system_trading_days": len(calendar),
        "first_system_trading_day": str(calendar[0]),
        "last_system_trading_day": str(calendar[-1]),
        "historical_nonfinancial_member_rows": eligible.height,
        "valid_r257_factor_rows": valid.height,
        "coverage": round(valid.height / eligible.height, 8),
        "valid_factor_days": valid["date"].n_unique(),
        "factor_first_valid_date": str(valid["date"].min()),
        "factor_last_valid_date": str(valid["date"].max()),
        "factor_id": "ext_alt_hs300_qrs_quality_relative_strength_min_rank",
        "candidate_id": "ai_hs300_quality_relative_strength_h20_sl20",
        "control_id": "ai_hs300_quality_relative_strength_control",
    }
    feasibility_path = EVIDENCE / "r298_pre_registration_feasibility.json"
    if feasibility_path.exists():
        raise FileExistsError(f"refusing to overwrite {feasibility_path.name}")
    feasibility_sha = write_once(
        feasibility_path,
        (json.dumps(feasibility, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )

    manifest_path = EVIDENCE / "r298_input_manifest.jsonl"
    if manifest_path.exists():
        raise FileExistsError(f"refusing to overwrite {manifest_path.name}")
    manifest_lines = []
    for path in input_paths():
        stat = path.stat()
        manifest_lines.append(
            json.dumps(
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "bytes": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "sha256": sha256_file(path),
                },
                sort_keys=True,
            )
        )
    manifest_sha = write_once(
        manifest_path, ("\n".join(manifest_lines) + "\n").encode("utf-8")
    )
    prereg_path = EVIDENCE / "r298_preregistration.json"
    if prereg_path.exists():
        raise FileExistsError(f"refusing to overwrite {prereg_path.name}")

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    candidate_config = {
        "strategy_id": "ai_hs300_quality_relative_strength_h20_sl20",
        "start": str(start),
        "end": str(end),
        "matching": "open_t+1",
        "fees_pct": 0.0006,
        "commission_pct": 0.0006,
        "stamp_tax_pct": 0.001,
        "slippage_bps": 15.0,
        "max_positions": 10,
        "max_exposure_pct": 1.0,
        "holding_days": 20,
        "asset_type": "stock",
        "position_sizing": "equal",
        "mode": "position",
        "minute_fill": False,
        "initial_capital": 1000000.0,
        "params": {},
        "overrides": {},
    }
    preregistration = {
        "round": ROUND,
        "registered_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pre_registered_after_feasibility_and_before_system_backtest",
        "research_question": "Does the frozen R257 quality-relative-strength candidate beat its matched quality/cash control in the 12-month period before R257's original discovery window?",
        "classification": "historical_pre_discovery_temporal_holdout_supplement_not_independent_confirmation",
        "selection_note": "The exact R257 strategy has no earlier backtest listed in the research ledger/evidence scan. Its factor family, source datasets, and related sleeves were researched elsewhere; multiple-comparison risk remains.",
        "window": {
            "requested_start": str(start),
            "requested_end": str(end),
            "calendar_source": "system 000300.SH daily index trading calendar",
            "observed_first_last": [
                feasibility["first_system_trading_day"],
                feasibility["last_system_trading_day"],
            ],
            "trading_days": len(calendar),
        },
        "universe_and_data": {
            "membership": "R157 monthly plus R78 daily historical HS300 snapshots using existing system research loader and backward as-of membership; current industry snapshot excludes banks and non-bank finance, matching R257.",
            "factor": "Existing R257 factor only; no new factor or builder run.",
            "matched_control": "Existing R257 quality/cash control with the same nonfinancial membership and factor availability requirement.",
            "limitations": [
                "Current industry classification is not historical PIT.",
                "System market_cap_min uses current instrument shares rather than historical PIT shares.",
                "Financial values may include revisions; original provider publication and vintage history are unavailable.",
                "This window predates R257's registered discovery interval but the factor/data family and related sleeves have been researched, so it is not an untouched independent study.",
            ],
        },
        "execution": {
            "engine": "StrategyBacktestService via existing R253 segment runner",
            "matching": "open_t+1",
            "score_limit": 30,
            "max_positions": 10,
            "position_sizing": "equal",
            "holding_days": 20,
            "stop_loss": -0.20,
            "cost_3x": {
                "fees_pct": 0.0006,
                "commission_pct": 0.0006,
                "stamp_tax_pct": 0.001,
                "slippage_bps": 15.0,
            },
            "stress": {
                "4x": "multiply each 3x cost component by 4/3",
                "5x": "multiply each 3x cost component by 5/3",
            },
            "cache_dir": "data/research/r298_cache",
        },
        "decision_gates": {
            "F1": "factor coverage >=99% with at least one valid name on >=95% of calendar days; determined before registration from feasibility evidence",
            "G1": "candidate 3x compounded return > 0",
            "G2": "candidate 3x compounded return >= control 3x return + 5 percentage points",
            "G3": "candidate 3x maximum drawdown >= -40%",
            "G4": "candidate compounded return > 0 under both 4x and 5x costs",
            "C1": "at least 30 completed candidate trades and 20 distinct candidate symbols; otherwise concentration review is underpowered and fails",
            "C2": "largest symbol net-P&L share <=25%",
            "C3": "top three symbol net-P&L share <=55%",
            "C4": "top five symbol net-P&L share <=75%",
        },
        "decision_rule": "All F/G/C gates must pass to call this temporal holdout supportive. Do not change factor, score weights, universe, costs, holding period, or gates after seeing the result. A pass remains exploratory and does not clear paper trading or production.",
        "side_effect_boundary": "Direct system engine calls write only the dedicated R298 research cache and R298 evidence; they do not call providers, strategy/backtest write APIs, or paper/order flows.",
        "feasibility_artifact": "evidence/r298_pre_registration_feasibility.json",
        "feasibility_sha256": feasibility_sha,
        "input_manifest": "evidence/r298_input_manifest.jsonl",
        "input_manifest_sha256": manifest_sha,
        "input_manifest_file_count": len(manifest_lines),
        "source_commit": head,
        "panel_candidate": {
            "kind": "strategy",
            "name": "R298 R257 发现期前一年补充回测",
            "source_id": candidate_config["strategy_id"],
            "config": candidate_config,
            "metrics": {},
            "data_as_of": str(end),
            "status": "pending",
        },
    }
    prereg_sha = write_once(
        prereg_path,
        (json.dumps(preregistration, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )

    print(
        json.dumps(
            {
                "round": ROUND,
                "window": [str(start), str(end)],
                "trading_days": len(calendar),
                "eligible_rows": eligible.height,
                "valid_rows": valid.height,
                "coverage": feasibility["coverage"],
                "feasibility_sha256": feasibility_sha,
                "preregistration_sha256": prereg_sha,
                "manifest_sha256": manifest_sha,
                "manifest_files": len(manifest_lines),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
