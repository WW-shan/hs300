from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

# ruff: noqa: E402

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
PREREG = EVIDENCE / "r328_paper_preflight_preregistration.json"
AMENDMENT = EVIDENCE / "r328_preregistration_amendment_A1.json"
OUTPUT = EVIDENCE / "r328_paper_preflight.json"
A_STRATEGY = DATA / "strategies" / "ai" / "ai_hs300_macro_gate_quality.py"
B_STRATEGY = DATA / "strategies" / "ai" / "ai_hs300_industry_relative_strength_quality_h20_sl20.py"

sys.path.insert(0, str(ROOT / "backend"))
from app.factors.registry import get_factor
from app.factors.store import load_into_registry
from app.indicators.pipeline import INDICATOR_COLUMNS


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def latest_partition_date(root: Path) -> date | None:
    paths = sorted(root.glob("date=*"))
    if not paths:
        return None
    return date.fromisoformat(paths[-1].name.split("=")[1])


def latest_partition(root: Path) -> Path | None:
    paths = sorted(root.glob("date=*/part.parquet"))
    return paths[-1] if paths else None


def weekday_lag(latest: date | None, as_of: date) -> int | None:
    if latest is None:
        return None
    current = latest + timedelta(days=1)
    count = 0
    while current <= as_of:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R328 preflight exists; refusing overwrite")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R328 preregistration checksum mismatch")
    if sha(AMENDMENT) != AMENDMENT.with_name(AMENDMENT.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R328 amendment checksum mismatch")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    as_of = date.fromisoformat(prereg["as_of"])

    equity_raw = latest_partition_date(DATA / "kline_daily")
    equity_enriched = latest_partition_date(DATA / "kline_daily_enriched")
    alt_factors = latest_partition_date(DATA / "ext_data" / "alt_hs300" / "timeseries")
    industry_path = DATA / "ext_data" / "ext_hy_ths" / "part.parquet"
    industry_mtime = datetime.fromtimestamp(industry_path.stat().st_mtime).date() if industry_path.exists() else None
    membership_path = DATA / "hs300" / "current_members.json"
    membership = json.loads(membership_path.read_text(encoding="utf-8"))
    membership_fetched = datetime.fromisoformat(membership["fetched_at"]).date()
    financials = {}
    for name in ("metrics", "income", "balance_sheet", "cash_flow"):
        path = DATA / "financials" / name / "part.parquet"
        frame = pl.read_parquet(path, columns=["period_end", "announce_date"])
        financials[name] = {
            "rows": frame.height,
            "max_period_end": str(frame["period_end"].max()),
            "max_announce_date": str(frame["announce_date"].max()),
        }
    gold_path = DATA / "research" / "r325_gold_assets" / "gold_etf_qfq.parquet"
    gold = pl.read_parquet(gold_path, columns=["symbol", "date", "close"])
    gold_last = gold["date"].max()
    latest_enriched_path = latest_partition(DATA / "kline_daily_enriched")
    latest_alt_path = latest_partition(DATA / "ext_data" / "alt_hs300" / "timeseries")
    enriched_columns = set(pl.read_parquet(latest_enriched_path).columns) if latest_enriched_path else set()
    alt_columns = set(pl.read_parquet(latest_alt_path).columns) if latest_alt_path else set()
    a_module = module(A_STRATEGY, "a_r328")
    b_module = module(B_STRATEGY, "b_r328")
    required_features = set(a_module.REQUIRED_FEATURES) | set(b_module.REQUIRED_FEATURES)
    scoring_features = set(a_module.META["scoring"]) | set(b_module.META["scoring"])
    required_ext = sorted(
        feature.removeprefix("ext_alt_hs300_")
        for feature in required_features | scoring_features
        if feature.startswith("ext_alt_hs300_")
    )
    required_custom = sorted(
        feature
        for feature in required_features | scoring_features
        if feature.startswith(("uf_", "cf_"))
    )
    load_into_registry(DATA)
    missing_custom = [feature for feature in required_custom if get_factor(feature) is None]
    runtime_indicators = sorted(
        feature
        for feature in required_features
        if not feature.startswith("ext_alt_hs300_")
        and feature not in enriched_columns
        and feature in INDICATOR_COLUMNS
    )
    missing_enriched = sorted(
        feature
        for feature in required_features
        if not feature.startswith("ext_alt_hs300_")
        and feature not in enriched_columns
        and feature not in INDICATOR_COLUMNS
    )
    missing_alt = [feature for feature in required_ext if feature not in alt_columns]
    stale_membership = membership.get("as_of") != str(as_of) or membership_fetched != as_of or int(membership.get("count", 0)) != 300
    financial_fresh = all(
        item["max_period_end"] >= "2026-09-30" and item["max_announce_date"] >= "2026-10-25"
        for item in financials.values()
    )
    gates = {
        "D1_membership": not stale_membership,
        "D2_equity_daily": equity_raw == as_of and equity_enriched == as_of,
        "D3_alt_factors": alt_factors == as_of,
        "D4_industry_snapshot": industry_mtime == as_of,
        "D5_financials": financial_fresh,
        "D6_gold_etf": gold_last == as_of,
        "D7_factor_registry": not missing_custom,
        "D8_feature_schema": not missing_enriched and not missing_alt,
        "D9_strategy_files": A_STRATEGY.exists() and B_STRATEGY.exists(),
        "D10_fail_closed": True,
    }
    all_pass = all(gates.values())
    payload = {
        "round": "R328",
        "classification": "preregistered paper-execution input freshness and dependency preflight; no performance test",
        "as_of": str(as_of),
        "preregistration_sha256": sha(PREREG),
        "amendment_sha256": sha(AMENDMENT),
        "runner_sha256": sha(Path(__file__).resolve()),
        "inputs": {
            "equity_raw_latest": str(equity_raw) if equity_raw else None,
            "equity_raw_weekday_lag": weekday_lag(equity_raw, as_of),
            "equity_enriched_latest": str(equity_enriched) if equity_enriched else None,
            "equity_enriched_weekday_lag": weekday_lag(equity_enriched, as_of),
            "alt_factors_latest": str(alt_factors) if alt_factors else None,
            "alt_factors_weekday_lag": weekday_lag(alt_factors, as_of),
            "industry_snapshot_mtime": str(industry_mtime) if industry_mtime else None,
            "industry_snapshot_weekday_lag": weekday_lag(industry_mtime, as_of),
            "membership": {
                "as_of": membership.get("as_of"),
                "fetched_at": membership.get("fetched_at"),
                "source": membership.get("source"),
                "count": membership.get("count"),
            },
            "financials": financials,
            "gold_cache_last_date": str(gold_last),
            "gold_cache_weekday_lag": weekday_lag(gold_last, as_of),
            "gold_cache_fetched_at": json.loads((EVIDENCE / "r325_asset_probe.json").read_text(encoding="utf-8"))["fetched_at_utc"],
        },
        "dependencies": {
            "required_features": sorted(required_features),
            "required_ext_columns": required_ext,
            "required_custom_factors": required_custom,
            "missing_custom_factors": missing_custom,
            "missing_enriched_features": missing_enriched,
            "missing_alt_columns": missing_alt,
            "runtime_indicators": runtime_indicators,
            "strategy_files": {
                "A": {"path": str(A_STRATEGY.relative_to(ROOT)), "sha256": sha(A_STRATEGY)},
                "B": {"path": str(B_STRATEGY.relative_to(ROOT)), "sha256": sha(B_STRATEGY)},
            },
        },
        "gates": gates,
        "all_pass": all_pass,
        "decision": {
            "status": "ready_for_paper_signal_generation" if all_pass else "no_go_stale_inputs",
            "paper_target_generated": False,
            "order_file_generated": False,
            "production_write": False,
            "provider_call": False,
        },
        "blocking_reasons": [name for name, passed in gates.items() if not passed],
        "limits": prereg["known_limitations"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "gates": gates,
        "status": payload["decision"]["status"],
        "blocking_reasons": payload["blocking_reasons"],
        "latest": {
            "equity_raw": payload["inputs"]["equity_raw_latest"],
            "equity_enriched": payload["inputs"]["equity_enriched_latest"],
            "alt_factors": payload["inputs"]["alt_factors_latest"],
            "industry_snapshot": payload["inputs"]["industry_snapshot_mtime"],
            "gold_cache": payload["inputs"]["gold_cache_last_date"],
            "financial_max_period": financials["metrics"]["max_period_end"],
            "financial_max_announce": financials["metrics"]["max_announce_date"],
        },
        "dependencies": {
            "missing_custom": missing_custom,
            "missing_enriched": missing_enriched,
            "missing_alt": missing_alt,
        },
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
