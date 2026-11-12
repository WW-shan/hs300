from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import shutil
import statistics as st
import sys
import tempfile
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
DATA = ROOT / "data"
EVIDENCE = Path(__file__).resolve().parent
R40 = DATA / "research/r40_engine_data"
CACHE = DATA / "research/r368_cache"
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
MONTHLY = DATA / "research/r157_membership/monthly_snapshots.parquet"
PRE_REG = EVIDENCE / "r368_capacity_preregistration.json"
PREFLIGHT = EVIDENCE / "r368_capacity_preflight.json"
RESULT = EVIDENCE / "r368_capacity_backtest.json"
FINAL_STATUS = EVIDENCE / "r368_final_status.json"
RESULT_MD = EVIDENCE / "r368_result.md"
R367_RESULT = EVIDENCE / "r367_size_neutralization_backtest.json"
CANDIDATE = "ai_hs300_weak_reversal_blend_size_h20_sl20_r368"
CONTROL = "ai_hs300_quality_size_h20_sl20_r368"
CAPACITIES = (10, 20)
COSTS = {
    2: {"fees_pct": .0004, "commission_pct": .0004, "stamp_tax_pct": .0005, "slippage_bps": 10.0},
    3: {"fees_pct": .0006, "commission_pct": .0006, "stamp_tax_pct": .0010, "slippage_bps": 15.0},
}
START = date(2022, 5, 5)
END = date(2026, 10, 14)
RECENT_START = date(2025, 10, 14)
PHASES = [START, date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), date(2025, 10, 14), date(2026, 1, 5)]

sys.path.insert(0, str(ROOT / "backend"))


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def input_fingerprints() -> dict:
    return module(EVIDENCE / "run_r368_capacity_preflight.py", "r368_fp").input_fingerprints()


def make_backtest_config(strategy_id: str, start: date, cost: int, capacity: int):
    from app.backtest.strategy import StrategyBacktestConfig

    return StrategyBacktestConfig(
        strategy_id=strategy_id,
        symbols=None,
        start=start,
        end=END,
        params={},
        overrides={},
        matching="open_t+1",
        max_positions=capacity,
        max_exposure_pct=1.0,
        initial_capital=1_000_000.0,
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        holding_days=20,
        regime_filter={"states": ["weak"]},
        **COSTS[cost],
    )


def membership() -> pl.DataFrame:
    monthly = pl.read_parquet(MONTHLY, columns=["query_date", "symbol"]).select(
        pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String)
    )
    daily = pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"]).select(
        pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String)
    )
    return pl.concat([monthly, daily], how="vertical_relaxed").unique().sort(["effective_date", "symbol"])


def curve_frame(curve: list[dict]) -> pl.DataFrame:
    rows=[]
    for item in curve or []:
        raw=item.get("date"); day=raw if isinstance(raw,date) else date.fromisoformat(str(raw)[:10])
        rows.append({"date":day,"value":float(item.get("value",0.0))})
    if not rows: raise RuntimeError("empty R368 curve")
    return pl.DataFrame(rows).sort("date").unique(subset=["date"],keep="last")


def stats(curve: pl.DataFrame) -> dict:
    values=[float(v) for v in curve["value"].to_list()]
    returns=[values[i]/values[i-1]-1.0 for i in range(1,len(values))]
    total=values[-1]/values[0]-1.0
    annual=(1.0+total)**(252.0/len(returns))-1.0
    sd=st.pstdev(returns); sharpe=st.mean(returns)/sd*math.sqrt(252.0) if sd else 0.0
    peak=values[0]; mdd=0.0
    for value in values:
        peak=max(peak,value); mdd=min(mdd,value/peak-1.0)
    return {"total_return":total,"annual":annual,"sharpe":sharpe,"mdd":mdd,"days":len(values)}


def segment_return(curve: pl.DataFrame, start: date) -> float | None:
    part=curve.filter(pl.col("date").is_between(start,END)).sort("date")
    if part.height<2:return None
    values=part["value"].to_list();return float(values[-1]/values[0]-1.0)


def key(label: str, capacity: int, start: date, cost: int) -> str:
    return f"{label}_cap{capacity}_{start}_{cost}x"


def main() -> int:
    for path in (RESULT,RESULT.with_name(RESULT.name+".sha256"),FINAL_STATUS,RESULT_MD,CACHE):
        if path.exists(): raise RuntimeError(f"R368 output exists; refusing overwrite: {path}")
    prereg=json.loads(PRE_REG.read_text(encoding="utf-8")); preflight=json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    if not preflight.get("all_checks") or preflight.get("no_return_or_backtest_evaluated") is not True:
        raise RuntimeError("R368 preflight did not pass")
    before=input_fingerprints()
    if before!=prereg["frozen_inputs"]["data_fingerprints"]: raise RuntimeError("R368 data fingerprints changed")
    if preflight.get("preregistration_sha256")!=sha(PRE_REG): raise RuntimeError("R368 preflight preregistration mismatch")
    if PREFLIGHT.with_name(PREFLIGHT.name+".sha256").read_text().split()[0]!=sha(PREFLIGHT): raise RuntimeError("R368 preflight checksum mismatch")
    for rel,expected in prereg["frozen_inputs"]["code_sha256"].items():
        if sha(ROOT/rel)!=expected: raise RuntimeError(f"R368 frozen code changed: {rel}")
    for rel,expected in prereg["frozen_artifacts"].items():
        if sha(ROOT/rel)!=expected: raise RuntimeError(f"R368 frozen strategy changed: {rel}")

    from app.backtest import strategy as strategy_module
    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyBacktestService
    from app.config import settings
    from app.factors.ext_factors import invalidate_ext_caches
    from app.factors.store import load_into_registry
    from app.indicators import pipeline
    from app.services import regime_builder
    from app.strategy.engine import StrategyEngine
    from app.tickflow.repository import DataStore,KlineRepository
    old_data=Path(settings.data_dir); old_membership_fn=strategy_module._hs300_membership_frame
    work=Path(tempfile.mkdtemp(prefix="r368_engine_")); curves={}; trades={}
    try:
        root=work/"engine_data";shutil.copytree(R40,root,symlinks=True)
        isolate=module(EVIDENCE/"r365_isolation.py","r368_isolation")
        isolation=isolate.prepare_isolated_data_root(root,DATA)
        if isolation["remaining_top_level_symlinks"]:raise RuntimeError("R368 data root not isolated")
        settings.data_dir=root;invalidate_ext_caches(root,keep_strategy_cache=True)
        regime_builder._REGIME_HISTORY_CACHE=None;regime_builder._REGIME_HISTORY_SIG=None
        load_into_registry(root);pipeline.invalidate_custom_signals()
        strategies=StrategyEngine(strategy_dirs=[DATA/"strategies/ai",EVIDENCE/"r368_strategies"],override_loader=lambda _sid:{})
        service=StrategyBacktestService(BacktestEngine(KlineRepository(DataStore(root))),strategies)
        members=membership();strategy_module._hs300_membership_frame=lambda _start,_end:members
        CACHE.mkdir(parents=True,exist_ok=False)
        for sid,label in ((CANDIDATE,"candidate"),(CONTROL,"control")):
            for capacity in CAPACITIES:
                for start in PHASES:
                    for cost in (2,3):
                        run_key=key(label,capacity,start,cost)
                        result=service.run(make_backtest_config(sid,start,cost,capacity))
                        if result.error:raise RuntimeError(f"R368 {run_key}: {result.error}")
                        curve=curve_frame(result.equity_curve)
                        if curve["date"].min()!=start or curve["date"].max()!=END:raise RuntimeError(f"R368 dates mismatch: {run_key}")
                        curve.write_parquet(CACHE/f"{run_key}.parquet",compression="zstd")
                        curves[run_key]=curve;trades[run_key]=int(result.stats.get("n_trades",len(result.trades)))
                        print(f"R368 ran {run_key}: trades={trades[run_key]}",flush=True)
    finally:
        settings.data_dir=old_data;strategy_module._hs300_membership_frame=old_membership_fn
        regime_builder._REGIME_HISTORY_CACHE=None;regime_builder._REGIME_HISTORY_SIG=None
        pipeline.invalidate_custom_signals();invalidate_ext_caches(old_data,keep_strategy_cache=True)
        load_into_registry(old_data);shutil.rmtree(work,ignore_errors=True)
    after=input_fingerprints()
    if before!=after:raise RuntimeError("R368 source inputs changed during run")

    metrics={}
    scorecard={}
    for label in ("candidate","control"):
        metrics[label]={};scorecard[label]={}
        for capacity in CAPACITIES:
            phase={str(start):curves[key(label,capacity,start,2)] for start in PHASES}
            main_curve=phase[str(START)]
            metrics[label][str(capacity)]={
                "2x":stats(main_curve),"3x":stats(curves[key(label,capacity,START,3)]),
                "recent_2x":segment_return(main_curve,RECENT_START),
                "phase_2x":{phase_start:stats(curve) for phase_start,curve in phase.items()},
                "trades":{key(label,capacity,start,cost):trades[key(label,capacity,start,cost)] for start in PHASES for cost in (2,3)},
            }
            scorecard[label][str(capacity)]={
                key(label,capacity,start,cost):{"metrics":stats(curves[key(label,capacity,start,cost)]),"trades":trades[key(label,capacity,start,cost)]}
                for start in PHASES for cost in (2,3)
            }
    r367=json.loads(R367_RESULT.read_text(encoding="utf-8"));candidate=metrics["candidate"];control=metrics["control"]
    cap10=candidate["10"];cap20=candidate["20"];control10=control["10"];control20=control["20"]
    comparisons={
        "candidate_20_minus_10_annual":cap20["2x"]["annual"]-cap10["2x"]["annual"],
        "candidate_20_minus_10_sharpe":cap20["2x"]["sharpe"]-cap10["2x"]["sharpe"],
        "candidate_20_minus_10_mdd":cap20["2x"]["mdd"]-cap10["2x"]["mdd"],
        "candidate_20_minus_10_recent":cap20["recent_2x"]-cap10["recent_2x"],
        "control_20_minus_10_annual":control20["2x"]["annual"]-control10["2x"]["annual"],
        "candidate_control_edge_10":cap10["2x"]["annual"]-control10["2x"]["annual"],
        "candidate_control_edge_20":cap20["2x"]["annual"]-control20["2x"]["annual"],
        "candidate_control_edge_change_20_minus_10":(cap20["2x"]["annual"]-control20["2x"]["annual"])-(cap10["2x"]["annual"]-control10["2x"]["annual"]),
        "cap20_candidate_annual_change_vs_r367":cap20["2x"]["annual"]-r367["metrics"]["candidate"]["2x"]["annual"],
        "cap20_candidate_recent_change_vs_r367":cap20["recent_2x"]-r367["metrics"]["candidate"]["recent_2x"],
        "cap20_candidate_mdd_change_vs_r367":cap20["2x"]["mdd"]-r367["metrics"]["size"]["candidate"]["2x"]["mdd"],
    }
    gates={
        "G1_cap20_annual_improves_at_least_2pp":comparisons["candidate_20_minus_10_annual"]>=.02,
        "G2_cap20_sharpe_not_worse_more_than_0_05":comparisons["candidate_20_minus_10_sharpe"]>=-.05,
        "G3_cap20_mdd_not_worse_more_than_3pp":comparisons["candidate_20_minus_10_mdd"]>=-.03,
        "G4_cap20_recent_not_worse_than_cap10":comparisons["candidate_20_minus_10_recent"]>=0,
        "G5_cap20_candidate_edge_not_worse_than_1pp":comparisons["candidate_control_edge_20"]>=comparisons["candidate_control_edge_10"]-.01,
        "G6_cap20_annual_not_more_than_5pp_below_cap10":comparisons["candidate_20_minus_10_annual"]>=-.05,
        "G7_at_least_4_of_6_cap20_phases_positive":sum(x["total_return"]>0 for x in cap20["phase_2x"].values())>=4,
        "G8_cap20_3x_cost_annual_nonnegative":cap20["3x"]["annual"]>=0,
        "G9_unlevered":True,
    }
    payload={
        "round":"R368","classification":"paired 10-versus-20 position-capacity experiment on the fixed R367 size-neutral sleeve",
        "hypothesis":prereg["hypothesis"],"rule":prereg["rule"],"preregistration_sha256":sha(PRE_REG),"preflight_sha256":sha(PREFLIGHT),
        "input_hashes_before":before,"input_hashes_after":after,"input_isolation":isolation,
        "metrics":metrics,"scorecard":scorecard,"comparisons":comparisons,"gates":gates,"all_gates_pass":all(gates.values()),
        "selected":None,"decision":{"status":"research_only_no_candidate_after_r368" if not all(gates.values()) else "research_only_candidate_for_followup","leverage":"none","paper_target_generated":False,"orders_generated":False,"production_enabled":False},
        "limits":prereg["known_limits"],
    }
    RESULT.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    RESULT.with_name(RESULT.name+".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n",encoding="ascii")
    status={"round":"R368","status":payload["decision"]["status"],"all_gates_pass":all(gates.values()),"failed_gates":[name for name,value in gates.items() if not value],"selected":None,"no_leverage":True,"paper_target_generated":False,"orders_generated":False,"production_enabled":False,"result_sha256":sha(RESULT)}
    FINAL_STATUS.write_text(json.dumps(status,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    FINAL_STATUS.with_name(FINAL_STATUS.name+".sha256").write_text(f"{sha(FINAL_STATUS)}  {FINAL_STATUS.name}\n",encoding="ascii")
    note=["# R368 · 市值中性反转策略持仓容量对照","",f"10仓候选年化 {cap10['2x']['annual']:.2%}, Sharpe {cap10['2x']['sharpe']:.3f}, MDD {cap10['2x']['mdd']:.2%}, 近一年 {cap10['recent_2x']:.2%}；20仓年化 {cap20['2x']['annual']:.2%}, Sharpe {cap20['2x']['sharpe']:.3f}, MDD {cap20['2x']['mdd']:.2%}, 近一年 {cap20['recent_2x']:.2%}。",f"质量对照10/20仓年化 {control10['2x']['annual']:.2%}/{control20['2x']['annual']:.2%}；容量变化差分年化边际 {comparisons['candidate_control_edge_change_20_minus_10']:+.2%}。",f"门槛通过 {sum(gates.values())}/{len(gates)}，selected=null；全 24 个候选/对照×相位×成本单元已写入 scorecard。无杠杆、未部署。","","证据：`r368_capacity_preflight.json`、`r368_capacity_backtest.json`、`r368_final_status.json`",""]
    RESULT_MD.write_text("\n".join(note),encoding="utf-8")
    RESULT_MD.with_name(RESULT_MD.name+".sha256").write_text(f"{sha(RESULT_MD)}  {RESULT_MD.name}\n",encoding="ascii")
    print(json.dumps({"metrics":metrics,"comparisons":comparisons,"gates":gates,"status":status},ensure_ascii=False,indent=2),flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
