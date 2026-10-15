import os
import json, time, urllib.request
BASE = "http://127.0.0.1:3018"
TOKEN = os.environ.get("TF_SESSION", "")

def post(path, payload, timeout=3600):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Cookie": f"tf_session={TOKEN}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())

def get(path, timeout=120):
    req = urllib.request.Request(BASE + path, headers={"Cookie": f"tf_session={TOKEN}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())

def run_raw(strategy_id="ai_hs300_lowrisk_alpha", start="2018-06-01", end="2026-10-14",
            maxpos=10, overrides=None, commission=None, slippage=None, holding_days=5):
    p = {"strategy_id": strategy_id, "start": start, "end": end, "matching": "open_t+1",
         "mode": "position", "hs300": True, "max_positions": maxpos,
         "position_sizing": "equal", "holding_days": holding_days, "asset_type": "stock"}
    if overrides is not None: p["overrides"] = overrides
    if commission is not None: p["commission_pct"] = commission
    if slippage is not None: p["slippage_bps"] = slippage
    t0 = time.time()
    r = post("/api/backtest/strategy/run", p)
    return r, time.time() - t0

def run_bt(**kw):
    r, el = run_raw(**kw)
    s = r.get("stats") or {}
    fa = (r.get("factor_attribution") or {}).get("factors") or []
    return s, [f.get("factor") for f in fa], r.get("error"), el, r

import os, json, time, statistics as st
import polars as pl

EV = os.path.dirname(os.path.abspath(__file__)) + "/"
REPO = os.path.abspath(os.path.join(EV, "..", "..", "..", "..", "..", ".."))
IDX = (pl.scan_parquet(os.path.join(REPO, "data", "kline_index_daily", "date=*", "part.parquet"))
         .filter(pl.col("symbol") == "000300.SH").select(["date","close"]).collect().sort("date")
         .with_columns(pl.col("date").cast(pl.Utf8)))
P10 = IDX.filter(pl.col("date") >= "2018-06-01").head(10)["date"].to_list()
P1Y = IDX.filter(pl.col("date") >= "2025-10-14").head(5)["date"].to_list()
SCORING = {"uf_low_amount":0.34,"uf_small_mv":0.33,"uf_low_vol":0.33}
HOLDS = [15,20,25,30,35,40]

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

def run(hold, phases, commission=None, slippage=None, mp=10, am=0.5e8):
    o={"scoring":SCORING,"scoring_replace":True,"max_hold_days":hold,"stop_loss":None,
       "basic_filter":{"amount_min":am}}
    recs=[]
    for ph in phases:
        s,fac,err,el,raw = run_bt(start=ph, maxpos=mp, overrides=o, commission=commission, slippage=slippage)
        if err: raise RuntimeError(f"hold={hold} {ph}: {err}")
        recs.append({"phase":ph,"ret":s.get("total_return"),"mdd":s.get("max_drawdown"),
                     "sharpe":s.get("sharpe"),"trades":s.get("n_trades"),
                     "yearly":{k:round(v,4) for k,v in yearly(raw.get("equity_curve")).items()}})
    return recs

out={"phases":P10,"holds":{}}
t0=time.time()
for h in HOLDS:
    recs=run(h,P10)
    out["holds"][str(h)]={"sharpe_mean":st.mean(x["sharpe"] for x in recs),
                          "sharpe_min":min(x["sharpe"] for x in recs),
                          "mdd_mean":st.mean(x["mdd"] for x in recs),
                          "ret_mean":st.mean(x["ret"] for x in recs),
                          "trades_mean":st.mean(x["trades"] for x in recs),
                          "ret_2018h2":st.mean(x["yearly"].get("2018",0.0) for x in recs), "recs":recs}
    a=out["holds"][str(h)]
    print(f"hold={h}: sharpe {a['sharpe_mean']:.3f} (min {a['sharpe_min']:.2f}) ret {a['ret_mean']*100:+.0f}% mdd {a['mdd_mean']*100:.1f}% trades {a['trades_mean']:.0f} 2018H2 {a['ret_2018h2']*100:+.1f}% ({time.time()-t0:.0f}s)", flush=True)
    json.dump(out, open(EV+"r15_hold_grid.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)

# 稳健中心 (内部 20/25/30/35)
idx={h:i for i,h in enumerate(HOLDS)}
internal=[h for h in [20,25,30,35]]
def nb_worst(h):
    i=idx[h]; ns=[HOLDS[i-1],h,HOLDS[i+1]]
    return min(out["holds"][str(x)]["sharpe_mean"] for x in ns)
internal.sort(key=lambda h:(-nb_worst(h), abs(h-20)))
center=internal[0]
print(f"\n稳健中心 (maximin, 内部格): hold={center} (nb_worst {nb_worst(center):.3f})", flush=True)

# 验证: 中心 vs 现行(20)
cur=out["holds"]["20"]
valid={}
for tag,h in {"default_20":20,"center":center}.items():
    c2=run(h,P10,commission=0.0004,slippage=10.0)
    y1=run(h,P1Y)
    valid[tag]={"hold":h,
        "cost2x_sharpe_mean":st.mean(x["sharpe"] for x in c2),
        "cost2x_ret_mean":st.mean(x["ret"] for x in c2),
        "y1_med":st.median(x["ret"] for x in y1),
        "y1":[round(x["ret"],4) for x in y1],
        "ret_2018h2":st.mean(x["yearly"].get("2018",0.0) for x in y1[:0]) if False else out["holds"][str(h)]["ret_2018h2"]}
    print(f"  [{tag}] hold={h} 2x sharpe {valid[tag]['cost2x_sharpe_mean']:.3f} ret {valid[tag]['cost2x_ret_mean']*100:+.0f}% | 1y med {valid[tag]['y1_med']*100:+.1f}% {valid[tag]['y1']} | 2018H2 {valid[tag]['ret_2018h2']*100:+.1f}%", flush=True)
out["center"]=center; out["nb_worst"]={str(h):nb_worst(h) for h in [20,25,30,35]}
out["valid"]=valid
json.dump(out, open(EV+"r15_hold_grid.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved r15_hold_grid.json")
