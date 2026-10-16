import json, time, urllib.request, os, statistics as st
BASE="http://localhost:3018"
def post(path,payload,timeout=3600):
    r=urllib.request.Request(BASE+path,data=json.dumps(payload).encode("utf-8"),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode("utf-8"))
def get(path,timeout=120):
    with urllib.request.urlopen(BASE+path,timeout=timeout) as resp: return json.loads(resp.read().decode("utf-8"))
CANDS={
 "v2":{"uf_low_amount":0.34,"uf_small_mv":0.33,"uf_low_vol":0.33},
 "C1":{"overnight_ret_20d":1/3,"uf_low_vol":1/3,"uf_small_mv":1/3},
 "C2":{"overnight_ret_20d":0.25,"uf_low_vol":0.25,"uf_small_mv":0.25,"uf_low_amount":0.25},
}
def run(scoring,start,end="2026-10-14"):
    p={"strategy_id":"ai_hs300_lowrisk_alpha","start":start,"end":end,"matching":"open_t+1",
       "mode":"position","hs300":True,"max_positions":10,"position_sizing":"equal",
       "holding_days":5,"asset_type":"stock",
       "overrides":{"scoring":scoring,"scoring_replace":True,"max_hold_days":20,"stop_loss":None}}
    return post("/api/backtest/strategy/run",p)
def yearly(curve):
    by={}
    for x in curve or []:
        d=str(x.get("date"))[:10]; v=x.get("value")
        if v is None: v=x.get("close")
        if v is None: continue
        by.setdefault(d[:4],[]).append(v)
    out={}; prev=None
    for y in sorted(by):
        sv=prev if prev is not None else by[y][0]
        out[y]=by[y][-1]/sv-1; prev=by[y][-1]
    return out
# phase starts: first 10 trading days from 2018-06-01
d=get("/api/kline/daily?symbol=000300.SH&start_date=2018-06-01&end_date=2018-06-20")
phases=[x["date"] for x in sorted(d.get("rows") or [], key=lambda r:r["date"])][:10]
print("phases:",phases)
# benchmark yearly (fixed, same grid)
bm={}
rows=[]
d=get("/api/kline/daily?symbol=000300.SH&start_date=2018-05-31&end_date=2026-10-14")
for x in sorted(d.get("rows") or [], key=lambda r:r["date"]):
    bm.setdefault(str(x["date"])[:4],[]).append(x["close"])
prev=None
BM={}
for y in sorted(bm):
    sv=prev if prev is not None else bm[y][0]
    BM[y]=bm[y][-1]/sv-1; prev=bm[y][-1]
print("bm yearly:",{k:round(v,4) for k,v in BM.items()})
out={"phases":phases,"benchmark_yearly":{k:round(v,5) for k,v in BM.items()},"candidates":{}}
for name,sc in CANDS.items():
    res=[]
    t0=time.time()
    for ph in phases:
        r=run(sc,ph); s=r["stats"]; y=yearly(r.get("equity_curve"))
        exc={k:round(y[k]-BM[k],4) for k in y if k in BM}
        res.append({"phase":ph,"ret":s.get("total_return"),"mdd":s.get("max_drawdown"),
                    "sharpe":s.get("sharpe"),"calmar":s.get("calmar"),"trades":s.get("n_trades"),
                    "yearly":{k:round(v,4) for k,v in y.items()},"excess":exc})
        print(f"  {name} {ph}: ret={s.get('total_return'):+.3f} sharpe={s.get('sharpe')} mdd={s.get('max_drawdown'):+.3f} ({time.time()-t0:.0f}s)")
    rets=[x["ret"] for x in res]; shs=[x["sharpe"] for x in res]; mdds=[x["mdd"] for x in res]
    years=[str(y) for y in range(2019,2027)]
    pos_years=sum(1 for y in years if st.mean([x["excess"].get(y,0.0) for x in res])>0)
    agg={"ret_mean":st.mean(rets),"ret_min":min(rets),"ret_max":max(rets),
         "sharpe_mean":st.mean(shs),"sharpe_min":min(shs),"sharpe_max":max(shs),
         "mdd_mean":st.mean(mdds),"mdd_min":min(mdds),
         "pos_excess_years":pos_years,
         "yearly_mean_excess":{y:round(st.mean([x["excess"].get(y,0.0) for x in res]),4) for y in ["2018"]+years}}
    out["candidates"][name]={"phases":res,"agg":agg}
    print(f"== {name}: ret mean {agg['ret_mean']:+.3f} [{agg['ret_min']:+.3f},{agg['ret_max']:+.3f}] "
          f"sharpe mean {agg['sharpe_mean']:.2f} [{agg['sharpe_min']:.2f},{agg['sharpe_max']:.2f}] "
          f"mdd mean {agg['mdd_mean']:+.3f} pos_years {pos_years}/8")
    print("   mean yearly excess:",agg["yearly_mean_excess"])
json.dump(out, open(os.path.join(os.environ["TEMP"],"r7.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved r7.json")
