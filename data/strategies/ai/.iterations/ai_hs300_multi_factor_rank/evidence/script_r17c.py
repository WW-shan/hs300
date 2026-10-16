# 输入说明: 需本地后端 (127.0.0.1:3018) 与已登录会话 cookie: 运行前 `export TF_SESSION=<tf_session cookie>`。
# 结果 JSON: 与本脚本同目录的 r17*_*.json。数据源为仓库内 data/ 下的本地缓存。
import os
import json, time, statistics as st
import polars as pl

EV = os.path.dirname(os.path.abspath(__file__)) + "/"
REPO = os.path.abspath(os.path.join(EV, "..", "..", "..", "..", "..", ".."))
IDX = (pl.scan_parquet(os.path.join(REPO, "data", "kline_index_daily", "date=*", "part.parquet"))
         .filter(pl.col("symbol") == "000300.SH").select(["date","close"]).collect().sort("date")
         .with_columns(pl.col("date").cast(pl.Utf8)))

P10 = IDX.filter(pl.col("date") >= "2018-06-01").head(10)["date"].to_list()
P1Y = IDX.filter(pl.col("date") >= "2025-10-14").head(5)["date"].to_list()


def post(path, payload, timeout=3600):
    import urllib.request
    req = urllib.request.Request("http://127.0.0.1:3018" + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Cookie": f"tf_session={os.environ.get('TF_SESSION','')}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def run_bt(strategy_id="ai_hs300_lowrisk_alpha", start="2018-06-01", end="2026-10-14",
           maxpos=10, overrides=None, commission=None, slippage=None, holding_days=5):
    p = {"strategy_id": strategy_id, "start": start, "end": end, "matching": "open_t+1",
         "mode": "position", "hs300": True, "max_positions": maxpos,
         "position_sizing": "equal", "holding_days": holding_days, "asset_type": "stock"}
    if overrides is not None: p["overrides"] = overrides
    if commission is not None: p["commission_pct"] = commission
    if slippage is not None: p["slippage_bps"] = slippage
    t0 = time.time()
    r = post("/api/backtest/strategy/run", p)
    s = r.get("stats") or {}
    fa = (r.get("factor_attribution") or {}).get("factors") or []
    return s, [f.get("factor") for f in fa], r.get("error"), time.time() - t0, r


V2 = {"uf_low_amount":0.34,"uf_small_mv":0.33,"uf_low_vol":0.33}
BASE = {"scoring":V2,"scoring_replace":True,"max_hold_days":35,"stop_loss":None,
        "basic_filter":{"amount_min":0.5e8}}
IND={"industry":"industry_l1"}
IND_MV={"industry":"industry_l1","styles":["log_float_mv"]}

CANDS = {
 "M0": dict(BASE, neutralize={}),
 "M1": dict(BASE, neutralize=dict(IND, shrink=0.5)),
 "M2": dict(BASE, neutralize=dict(IND_MV, shrink=0.5)),
 "M3": dict(BASE, neutralize=dict(IND, shrink=0.25)),
 "M4": dict(BASE, neutralize=dict(IND_MV, shrink=0.75)),
}

def yearly(curve):
    by={}
    for x in curve or []:
        d=str(x.get("date"))[:10]; v=x.get("value")
        if v is None: v=x.get("close")
        if v is None: continue
        by.setdefault(d[:4], []).append(v)
    out={}; prev=None
    for y in sorted(by):
        sv = prev if prev is not None else by[y][0]
        out[y]=by[y][-1]/sv-1; prev=by[y][-1]
    return out

def run(tag, phases, commission=None, slippage=None, mp=10):
    recs=[]
    for ph in phases:
        s,fac,err,el,raw = run_bt(start=ph, maxpos=mp, overrides=CANDS[tag],
                                     commission=commission, slippage=slippage)
        if err: raise RuntimeError(f"{tag} {ph}: {err} ({el:.0f}s)")
        recs.append({"phase":ph,"ret":s.get("total_return"),"mdd":s.get("max_drawdown"),
                     "sharpe":s.get("sharpe"),"trades":s.get("n_trades"),
                     "yearly":{k:round(v,4) for k,v in yearly(raw.get("equity_curve")).items()}})
    return recs

def agg(recs):
    return {"sharpe_mean":st.mean(x["sharpe"] for x in recs),
            "sharpe_min":min(x["sharpe"] for x in recs),
            "mdd_mean":st.mean(x["mdd"] for x in recs),
            "ret_mean":st.mean(x["ret"] for x in recs),
            "trades_mean":st.mean(x["trades"] for x in recs), "recs":recs}

out={"phases_10":P10,"phases_1y":P1Y,"cands":{}}
t0=time.time()
for tag in CANDS:
    recs=run(tag,P10); out["cands"][tag]=agg(recs)
    a=out["cands"][tag]
    print(f"[10ph] {tag}: sharpe {a['sharpe_mean']:.3f} (min {a['sharpe_min']:.2f}) "
          f"ret {a['ret_mean']*100:+.0f}% mdd {a['mdd_mean']*100:.1f}% ({time.time()-t0:.0f}s)", flush=True)
    json.dump(out, open(EV+"r17c_shrink.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)

for tag in CANDS:
    recs=run(tag,P1Y)
    out["cands"][tag]["y1"]=[round(x["ret"],4) for x in recs]
    out["cands"][tag]["y1_med"]=st.median(x["ret"] for x in recs)
    print(f"[1y]   {tag}: med {out['cands'][tag]['y1_med']*100:+.1f}% {out['cands'][tag]['y1']}", flush=True)
json.dump(out, open(EV+"r17c_shrink.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)

for tag in CANDS:
    recs=run(tag,P10,commission=0.0004,slippage=10.0)
    a=agg(recs)
    out["cands"][tag]["cost2x_sharpe_mean"]=a["sharpe_mean"]
    out["cands"][tag]["cost2x_ret_mean"]=a["ret_mean"]
    print(f"[2x]   {tag}: sharpe {a['sharpe_mean']:.3f} ret {a['ret_mean']*100:+.0f}% ({time.time()-t0:.0f}s)", flush=True)
json.dump(out, open(EV+"r17c_shrink.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)

n0=out["cands"]["M0"]["recs"]; years=sorted({y for r in n0 for y in r["yearly"]})
for tag in CANDS:
    if tag=="M0":
        out["cands"][tag]["pos_excess_years"]=None; out["cands"][tag]["pos_excess_years_full"]=None; continue
    recs=out["cands"][tag]["recs"]; pos=0; posfull=0; vals={}
    for y in years:
        a=[r["yearly"].get(y) for r in n0 if r["yearly"].get(y) is not None]
        b=[r["yearly"].get(y) for r in recs if r["yearly"].get(y) is not None]
        ex=st.mean(b)-st.mean(a); vals[y]=round(ex*100,2)
        if ex>0:
            pos+=1
            if y<="2025": posfull+=1
    out["cands"][tag]["yearly_excess"]=vals
    out["cands"][tag]["pos_excess_years"]=pos
    out["cands"][tag]["pos_excess_years_full"]=posfull
    print(f"[excess] {tag}: 逐年 {posfull}/8 (完整年), 含2026 {pos}/9  {vals}", flush=True)
json.dump(out, open(EV+"r17c_shrink.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved r17c_shrink.json")
