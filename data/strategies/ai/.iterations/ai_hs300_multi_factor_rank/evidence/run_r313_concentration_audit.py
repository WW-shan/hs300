from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
R40 = DATA / "research/r40_engine_data"
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
PREREG = EVIDENCE / "r313_concentration_preregistration.json"
OUTPUT = EVIDENCE / "r313_concentration_audit.json"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2021, 6, 1)
END = date(2026, 10, 14)
RECENT_START = date(2025, 10, 14)

sys.path.insert(0, str(ROOT / "backend"))
from app.backtest import strategy as strategy_module
from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.factors.store import load_into_registry
from app.services.industry_map import industry_loader_for, load_industry_l1
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyEngine
from app.tickflow.repository import DataStore, KlineRepository


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(root: Path) -> dict:
    paths = sorted(root.glob("date=*/part.parquet"))
    digest = hashlib.sha256()
    size = 0
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(sha(path).encode())
        digest.update(b"\n")
        size += path.stat().st_size
    return {"files": len(paths), "bytes": size, "sha256": digest.hexdigest()}


def membership() -> pl.DataFrame:
    return (
        pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"])
        .select(pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String))
        .unique()
        .sort(["effective_date", "symbol"])
    )


@contextmanager
def patched_membership(frame: pl.DataFrame):
    original = strategy_module._hs300_membership_frame
    strategy_module._hs300_membership_frame = lambda _start, _end: frame
    try:
        yield
    finally:
        strategy_module._hs300_membership_frame = original


def curve_frame(curve: list[dict]) -> pl.DataFrame:
    rows = []
    for item in curve or []:
        raw = item.get("date")
        day = raw if isinstance(raw, date) else date.fromisoformat(str(raw)[:10])
        rows.append({"date": day, "value": float(item.get("value", 0.0))})
    return pl.DataFrame(rows).sort("date").unique(subset=["date"], keep="last")


def parse_day(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def weight_at(records: list[dict], day: date, key: str) -> float:
    value = float(records[0][key])
    for record in records:
        if record["date"] <= day:
            value = float(record[key])
        else:
            break
    return value


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R313 result exists; refusing overwrite")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R313 preregistration checksum mismatch")

    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r313")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r313")
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r313")
    r114.TARGET_VOL = 0.15
    members = membership()
    before_r40 = tree_hash(R40 / "kline_daily_enriched")
    before_prod = tree_hash(DATA / "kline_daily_enriched")
    work = Path(tempfile.mkdtemp(prefix="r313_"))
    try:
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
        raw_target = engine_root / "kline_daily"
        if raw_target.is_symlink() or raw_target.is_file():
            raw_target.unlink()
        elif raw_target.exists():
            shutil.rmtree(raw_target)
        shutil.copytree(DATA / "kline_daily", raw_target)
        load_into_registry(DATA)
        engine = StrategyEngine(
            strategy_dirs=[DATA / "strategies/ai"],
            override_loader=lambda sid: strategy_config.load_override(DATA, sid),
            industry_loader=industry_loader_for(DATA),
        )
        service = StrategyBacktestService(BacktestEngine(KlineRepository(DataStore(engine_root))), engine)

        def run(strategy_id: str):
            cfg = StrategyBacktestConfig(
                strategy_id=strategy_id,
                symbols=None,
                start=START,
                end=END,
                params={},
                overrides={},
                matching="open_t+1",
                fees_pct=0.0004,
                commission_pct=0.0004,
                stamp_tax_pct=0.0005,
                slippage_bps=10.0,
                max_positions=10,
                max_exposure_pct=1.0,
                position_sizing="equal",
                mode="position",
                asset_type="stock",
                hs300=True,
            )
            with patched_membership(members):
                result = service.run(cfg)
            if result.error:
                raise RuntimeError(f"{strategy_id}: {result.error}")
            trades = [
                {
                    "sleeve": "A" if strategy_id == A_STRATEGY else "B",
                    "symbol": t.get("symbol"),
                    "name": t.get("name"),
                    "entry_date": str(t.get("entry_date")),
                    "exit_date": str(t.get("exit_date")),
                    "pnl_pct": t.get("pnl_pct"),
                    "pnl_amount": float(t.get("pnl_amount") or 0.0),
                    "exit_reason": t.get("exit_reason"),
                }
                for t in result.trades
            ]
            return curve_frame(result.equity_curve), trades

        a_curve, a_trades = run(A_STRATEGY)
        b_curve, b_trades = run(B_STRATEGY)
        risk, rp_records = r112._combine(a_curve, b_curve, "risk")
        overlay = r114._vol_target(risk)
        exposure_records = overlay["weight_records"]
        curve_dates = risk["date"].to_list()

        def holding_scale(trade: dict) -> float:
            entry = parse_day(trade["entry_date"])
            raw_exit = trade.get("exit_date")
            exit_ = parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
            days = [d for d in curve_dates if entry <= d <= exit_]
            if not days:
                days = [entry]
            scales = []
            for day in days:
                exposure = weight_at(exposure_records, day, "weight")
                if trade["sleeve"] == "A":
                    scales.append(weight_at(rp_records, day, "weight_a") * exposure)
                else:
                    scales.append((1.0 - weight_at(rp_records, day, "weight_a")) * exposure)
            return sum(scales) / len(scales)

        scaled = []
        for trade in [*a_trades, *b_trades]:
            scale = holding_scale(trade)
            scaled.append({
                **trade,
                "weight_scale": round(scale, 6),
                "pnl_amount": round(trade["pnl_amount"] * scale, 6),
            })

    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = tree_hash(R40 / "kline_daily_enriched")
    after_prod = tree_hash(DATA / "kline_daily_enriched")
    industry_frame = load_industry_l1(DATA)
    industry = {
        str(row["symbol"]): str(row["industry_l1"])
        for row in industry_frame.iter_rows(named=True)
        if row["industry_l1"]
    }
    full = r239._aggregate(scaled, industry)
    recent_trades = [t for t in scaled if parse_day(t["entry_date"]) >= RECENT_START]
    recent = r239._aggregate(recent_trades, industry)
    recent_positive = sum(1 for row in recent["per_symbol"] if row["pnl_amount"] > 0)
    sleeve_pnl = {
        "A": round(sum(t["pnl_amount"] for t in scaled if t["sleeve"] == "A"), 2),
        "B": round(sum(t["pnl_amount"] for t in scaled if t["sleeve"] == "B"), 2),
    }
    total = full["total_pnl_amount"]
    sleeve_share = {k: round(v / total, 6) if total > 0 else None for k, v in sleeve_pnl.items()}
    gates = {
        "C1_coverage": full["distinct_symbols"] >= 80,
        "C2_top1": total > 0 and full["top1_net_share"] <= 0.25,
        "C3_top3": total > 0 and full["top3_net_share"] <= 0.55,
        "C4_top5": total > 0 and full["top5_net_share"] <= 0.75,
        "C5_recent_breadth": recent_positive >= 8,
        "C6_industry": full["top_industry_gross_share"] is not None and full["top_industry_gross_share"] <= 0.45,
        "C7_loss": full["max_symbol_loss_to_gross"] is not None and full["max_symbol_loss_to_gross"] <= 0.20,
    }
    payload = {
        "round": "R313",
        "classification": "preregistered concentration audit of R312; portfolio weights and strategy rules unchanged",
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__).resolve()),
        "window": {"start": str(START), "end": str(END), "recent_start": str(RECENT_START)},
        "sleeves": {
            "A": {"strategy": A_STRATEGY, "trades": len(a_trades)},
            "B": {"strategy": B_STRATEGY, "trades": len(b_trades)},
        },
        "portfolio": {
            "risk_parity_records": len(rp_records),
            "exposure_records": len(exposure_records),
            "last_weight_a": round(float(rp_records[-1]["weight_a"]), 6),
            "last_exposure": round(float(exposure_records[-1]["weight"]), 6),
        },
        "sleeve_attributed_pnl": {"amounts": sleeve_pnl, "shares": sleeve_share},
        "concentration_full": full,
        "concentration_recent_12m": recent,
        "recent_positive_symbols": recent_positive,
        "gates": gates,
        "selected": all(gates.values()),
        "input_isolation": {
            "r40_before": before_r40, "r40_after": after_r40,
            "production_before": before_prod, "production_after": after_prod,
            "stable": before_r40 == after_r40 and before_prod == after_prod,
        },
        "decision": {
            "status": "candidate_for_paper_trading_review" if all(gates.values()) else "research_only_concentration_failed",
            "production_write": False, "provider_call": False, "parameter_search": False,
        },
        "caveats": [
            "Attribution uses holding-period average monthly weights and separately backtested 1M sleeve P&L; it is a share diagnostic, not compounded portfolio accounting.",
            "Industry classification is a current snapshot, not historical PIT.",
            "R40 is a local research snapshot; canonical data drift remains unresolved.",
            "A concentration pass would not authorize production.",
        ],
        "trades": scaled,
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "full": {k: full.get(k) for k in ("n_trades", "distinct_symbols", "total_pnl_amount", "top1_net_share", "top3_net_share", "top5_net_share", "top_industry_gross_share", "max_symbol_loss_to_gross")},
        "recent": {k: recent.get(k) for k in ("n_trades", "distinct_symbols", "total_pnl_amount")},
        "recent_positive_symbols": recent_positive,
        "sleeve_pnl": sleeve_pnl,
        "gates": gates,
        "selected": payload["selected"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
