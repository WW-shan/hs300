"""Rebuild the research caches required by build_alt_factor_data.py.

Each section reuses the original research script's fetch functions so the
cache format matches what the builder expects.  Sections are independent;
failures are recorded and do not stop the run.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
import traceback

import polars as pl
from pathlib import Path

EV = Path(__file__).resolve().parents[1] / "data" / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
LOG = Path(__file__).resolve().parents[1] / "data" / "research" / "rebuild_log.json"

STATE = {"sections": {}}


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _record(section: str, status: str, detail=None):
    STATE["sections"][section] = {"status": status, "detail": detail, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(json.dumps(STATE, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"[{section}] {status}: {detail}", flush=True)


def run_section(name: str, fn):
    started = time.perf_counter()
    try:
        detail = fn()
        _record(name, "ok", {"elapsed": round(time.perf_counter() - started, 1), "result": detail})
    except Exception as exc:  # noqa: BLE001
        _record(name, "failed", {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "trace": traceback.format_exc()[-1500:]})


def main() -> int:
    only = sys.argv[1:] if len(sys.argv) > 1 else None

    def want(name):
        return not only or name in only

    if want("r50_margin"):
        def _r50():
            r50 = _load("r50", "script_r50_margin.py")
            r42 = r50.r42
            r40 = r50.r40
            panel, _reb, _m = r42.build_panel()
            memberships, _ = r40.membership_frame()
            symbols = set(memberships["symbol"].unique().to_list())
            months = r50._month_ends(panel)
            mapping = r50._code_to_symbol(symbols)
            return r50._fetch_cache(months, mapping)
        run_section("r50_margin", _r50)

    if want("r65_pledge"):
        def _r65():
            r65 = _load("r65", "script_r65_pledge.py")
            dates, _profile = r65._month_dates()
            manifest, _dates = r65._fetch(dates)
            return manifest
        run_section("r65_pledge", _r65)

    if want("r74_etf"):
        def _r74():
            r74 = _load("r74", "script_r74_etf_flow.py")
            dates = r74._month_dates()
            manifest, _shares = r74._fetch(dates)
            return manifest
        run_section("r74_etf", _r74)

    if want("r68_supply"):
        def _r68():
            r68 = _load("r68", "script_r68_equity_supply.py")
            r42 = r68.r42
            _panel, _reb, memberships = r42.build_panel()
            symbols = sorted(memberships["symbol"].unique().to_list())
            return r68._fetch(symbols)
        run_section("r68_supply", _r68)

    if want("r89_dividend"):
        def _r89():
            r89 = _load("r89", "script_r89_dividend_growth_probe.py")
            r87_sample = r89.r87_sample
            memberships = pl.read_parquet(r87_sample.DAILY_MEMBERSHIP).select("symbol").unique()
            symbols = sorted(memberships["symbol"].unique().to_list())
            _raw, manifest = r89._ensure_cache(symbols)
            return manifest
        run_section("r89_dividend", _r89)

    if want("r90_insider"):
        def _r90():
            r90 = _load("r90", "script_r90_insider_trade_probe.py")
            _raw, manifest = r90._ensure_cache()
            return manifest
        run_section("r90_insider", _r90)

    if want("r47_analyst"):
        def _r47():
            r47 = _load("r47", "script_r47_analyst.py")
            r42 = r47.r42
            panel, reb, _m = r42.build_panel()
            symbols = set(reb["symbol"].unique().to_list())
            mapping = r47._code_to_symbol(symbols)
            dates = sorted(day for day in panel["date"].unique().to_list() if day >= r47.DISCOVERY[0])
            return r47._fetch_cache(dates, mapping, symbols)
        run_section("r47_analyst", _r47)

    if want("r86_intangible"):
        def _r86():
            r86 = _load("r86", "script_r86_intangible_investment.py")
            r42 = r86.r42
            _panel, _reb, memberships = r42.build_panel()
            universe = sorted(memberships["symbol"].unique().to_list())
            _statements, fetch_audit = r86._ensure_statements(universe)
            return fetch_audit
        run_section("r86_intangible", _r86)

    if want("r39_external"):
        def _r39():
            r39 = _load("r39", "script_r39_external.py")
            audit = r39._ensure_cache()
            return audit
        run_section("r39_external", _r39)

    _record("ALL", "done", None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
