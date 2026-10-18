import json, math, os, statistics as st, time, urllib.request
import polars as pl
BASE="http://localhost:3018"; END="2026-10-16"
V2={"uf_low_amount":0.34,"uf_small_mv":0.33,"uf_low_vol":0.33}
P2_OV={"scoring":V2,"scoring_replace":True,"max_hold_days":35,"stop_loss":None,
  "basic_filter":{"price_min":3,"price_max":500,"market_cap_min":10e8,"amount_min":0.5e8,"exclude_st":True,"exclude_new_days":60},
  "neutralize":{"industry":"industry_l1","shrink":0.5}}
V20_OV={"scoring":{"overnight_ret_20d":0.5,"amplitude_trend_20_60":0.5},"scoring_replace":True,"max_hold_days":20,"stop_loss":-0.20}
V20_P={"use_trend_filter":True}
idx=(pl.scan_parquet(r"D:\Code\hs300\data\kline_index_daily\date=*\part.parquet",extra_columns="ignore")
     .filter(pl.col("symbol")=="000300.SH").select(["date","close"]).collect().sort("date")
     .with_columns(pl.col("date").cast(pl.Utf8)))
ID=idx["date"].to_list(); IC=dict(zip(ID,idx["close"].to_list()))
LONG=[d for d in ID if d>="2018-06-01"][:10]; REC=[d for d in ID if d>="2025-10-14"][:5]
def post(p,payload,timeout=3600):
    r=urllib.request.Request(BASE+p,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode())
def run(sid,start,mp,ov,pa,comm=None,slip=None):
    p={"strategy_id":sid,"start":start,"end":END,"matching":"open_t+1","mode":"position","hs300":True,
       "max_positions":mp,"position_sizing":"equal","holding_days":5,"asset_type":"stock"}
    if ov: p["overrides"]=ov
    if pa: p["params"]=pa
    if comm is not None: p["commission_pct"]=comm
    if slip is not None: p["slippage_bps"]=slip
    r=post("/api/backtest/strategy/run",p)
    if r.get("error"): raise RuntimeError(r["error"])
    return {"stats":r.get("stats") or {},"curve":[(str(x["date"])[:10],float(x["value"])) for x in (r.get("equity_curve") or [])]}
def mdd(v):
    peak=v[0]; m=0.0
    for x in v: peak=max(peak,x); m=min(m,x/peak-1)
    return m
def sharpe(curve):
    v=[x[1] for x in curve]; r=[v[i]/v[i-1]-1 for i in range(1,len(v))]
    sd=st.pstdev(r); return st.mean(r)/sd*math.sqrt(252) if sd>0 else 0.0
def yearly(curve):
    by={}
    for d,v in curve: by.setdefault(d[:4],[]).append((d,v))
    o={}; prev=None
    for y in sorted(by):
        vs=[v for _,v in by[y]]; sv=prev if prev is not None else vs[0]; o[y]=vs[-1]/sv-1; prev=vs[-1]
    return o
def bench_yearly(curve):
    by={}
    for d,_ in curve: by.setdefault(d[:4],[]).append(d)
    o={}; prev=None
    for y in sorted(by):
        ds=by[y]; sv=IC[prev] if prev else IC[ds[0]]; o[y]=IC[ds[-1]]/sv-1; prev=ds[-1]
    return o
def blend(ca,cb,w):
    db=dict(cb); da=dict(ca); ds=[d for d,_ in ca if d in db]
    out=[(ds[0],1.0)]
    for i in range(1,len(ds)):
        ra=da[ds[i]]/da[ds[i-1]]-1; rb=db[ds[i]]/db[ds[i-1]]-1
        out.append((ds[i],out[-1][1]*(1+w*ra+(1-w)*rb)))
    return out
def agg(runs):
    rets=[x["stats"]["total_return"] for x in runs]; shs=[x["stats"]["sharpe"] for x in runs]; md=[x["stats"]["max_drawdown"] for x in runs]
    return {"ret_mean":st.mean(rets),"ret_min":min(rets),"ret_max":max(rets),"sharpe_mean":st.mean(shs),"mdd_mean":st.mean(md),"runs":runs}
out={}
for name,sid,mp,ov,pa in (("P2","ai_hs300_lowrisk_alpha",20,P2_OV,None),("V20","ai_hs300_multi_factor_rank",20,V20_OV,V20_P)):
    t0=time.time(); L=[]; R=[]
    for s in LONG:
        r=run(sid,s,mp,ov,pa); L.append(r); print(f"{name} long {s}: {r['stats'].get('total_return'):+.3f}/{r['stats'].get('sharpe'):.2f}/{r['stats'].get('max_drawdown'):+.3f} ({time.time()-t0:.0f}s)",flush=True)
    for s in REC:
        r=run(sid,s,mp,ov,pa); R.append(r); print(f"{name} rec {s}: {r['stats'].get('total_return'):+.3f}",flush=True)
    c2=run(sid,REC[0],mp,ov,pa,0.0004,10.0)
    out[name]={"long":agg(L),"recent":agg(R),"cost2x":c2}
    json.dump(out,open(os.path.join(os.environ["TEMP"],"reval2.json"),"w",encoding="utf-8"),ensure_ascii=False)
for w in (0.8,0.7,0.5):
    k=f"B{int(w*100)}"; L=[]; R=[]
    for i in range(10):
        c=blend(out["P2"]["long"]["runs"][i]["curve"],out["V20"]["long"]["runs"][i]["curve"],w); L.append({"stats":{"total_return":c[-1][1]-1,"max_drawdown":mdd([v for _,v in c]),"sharpe":sharpe(c)},"curve":c})
    for i in range(5):
        c=blend(out["P2"]["recent"]["runs"][i]["curve"],out["V20"]["recent"]["runs"][i]["curve"],w); R.append({"stats":{"total_return":c[-1][1]-1,"max_drawdown":mdd([v for _,v in c]),"sharpe":sharpe(c)},"curve":c})
    out[k]={"long":agg(L),"recent":agg(R)}
    print(f"{k}: long ret {out[k]['long']['ret_mean']*100:+.1f}% sh {out[k]['long']['sharpe_mean']:.2f} mdd {out[k]['long']['mdd_mean']*100:.1f}% | recent ret {out[k]['recent']['ret_mean']*100:+.1f}% [{out[k]['recent']['ret_min']*100:+.1f},{out[k]['recent']['ret_max']*100:+.1f}] 正收益 {sum(1 for x in R if x['stats']['total_return']>0)}/5",flush=True)
W=252
def roll(curves):
    pos=[];beat=[];rets=[]
    for c in curves:
        v=[x[1] for x in c]; ds=[x[0] for x in c]
        for i in range(W,len(v)):
            r=v[i]/v[i-W]-1; rets.append(r); pos.append(1 if r>0 else 0)
            if ds[i-W] in IC and ds[i] in IC: beat.append(1 if r-(IC[ds[i]]/IC[ds[i-W]]-1)>0 else 0)
    s=sorted(rets)
    return {"n":len(s),"positive":sum(pos)/len(pos),"beat":sum(beat)/len(beat),"median":st.median(s),"p05":s[len(s)//20],"worst":s[0]}
out["rolling_1y"]={"P2":roll([x["curve"] for x in out["P2"]["long"]["runs"]]),"V20":roll([x["curve"] for x in out["V20"]["long"]["runs"]]),"B80":roll([x["curve"] for x in out["B80"]["long"]["runs"]])}
for k in ("P2","V20","B80"):
    y=yearly(out[k]["long"]["runs"][0]["curve"]); b=bench_yearly(out[k]["long"]["runs"][0]["curve"])
    out[k]["yearly"]={a:round(v,4) for a,v in y.items()}; out[k]["yearly_excess"]={a:round(y[a]-b[a],4) for a in y if a in b}
    print(k,"rolling:",{a:round(b_,3) if isinstance(b_,float) else b_ for a,b_ in out["rolling_1y"][k].items()},flush=True)
    print(k,"yearly:",out[k]["yearly"],"excess:",out[k]["yearly_excess"],flush=True)
json.dump(out,open(os.path.join(os.environ["TEMP"],"revalidate_v037.json"),"w",encoding="utf-8"),ensure_ascii=False)
print("saved revalidate_v037.json",flush=True)
