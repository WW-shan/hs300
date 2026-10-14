import json, time, urllib.request, os, math, statistics as st
BASE="http://localhost:3018"
def post(path,payload,timeout=3600):
    r=urllib.request.Request(BASE+path,data=json.dumps(payload).encode("utf-8"),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode("utf-8"))
def get(path,timeout=120):
    with urllib.request.urlopen(BASE+path,timeout=timeout) as resp: return json.loads(resp.read().decode("utf-8"))
C1={"overnight_ret_20d":1/3,"uf_low_vol":1/3,"uf_small_mv":1/3}
def run(sid, start, scoring=None, end="2026-10-14"):
    p={"strategy_id":sid,"start":start,"end":end,"matching":"open_t+1","mode":"position",
       "hs300":True,"max_positions":10,"position_sizing":"equal","holding_days":5,"asset_type":"stock"}
    if scoring is not None:
        p["overrides"]={"scoring":scoring,"scoring_replace":True,"max_hold_days":20,"stop_loss":None}
    return post("/api/backtest/strategy/run",p)
def curve(res):
    out=[]
    for x in res.get("equity_curve") or []:
        d=str(x.get("date"))[:10]; v=x.get("value")
        if v is None: v=x.get("close")
        if v is not None: out.append((d,float(v)))
    return out
def rets(c):
    return {c[i][0]: c[i][1]/c[i-1][1]-1 for i in range(1,len(c))}
def sharpe_of(dr):
    v=list(dr.values())
    if len(v)<2: return None
    sd=st.pstdev(v)
    return st.mean(v)/sd*math.sqrt(252) if sd>0 else None
def mdd_of(c):
    peak=c[0][1]; m=0.0
    for d,v in c:
        peak=max(peak,v); m=min(m,v/peak-1)
    return m
def yearly_of(c):
    by={}
    for d,v in c: by.setdefault(d[:4],[]).append(v)
    o={}; prev=None
    for y in sorted(by):
        sv=prev if prev is not None else by[y][0]
        o[y]=by[y][-1]/sv-1; prev=by[y][-1]
    return o
d=get("/api/kline/daily?symbol=000300.SH&start_date=2018-06-01&end_date=2018-06-20")
phases=[x["date"] for x in sorted(d.get("rows") or [], key=lambda r:r["date"])][:10]
bm_rows=sorted((get("/api/kline/daily?symbol=000300.SH&start_date=2018-05-31&end_date=2026-10-14").get("rows") or []), key=lambda r:r["date"])
BM={}; byc={}
for x in bm_rows: byc.setdefault(str(x["date"])[:4],[]).append(float(x["close"]))
prev=None
for y in sorted(byc):
    sv=prev if prev is not None else byc[y][0]
    BM[y]=byc[y][-1]/sv-1; prev=byc[y][-1]
print("phases:",phases, flush=True)
runs={}
for name,sid,sc in (("v1","ai_hs300_multi_factor_rank",None),("v2","ai_hs300_lowrisk_alpha",None),("C1","ai_hs300_lowrisk_alpha",C1)):
    runs[name]={}
    t0=time.time()
    for ph in phases:
        r=run(sid,ph,sc)
        runs[name][ph]=curve(r)
        s=r.get("stats") or {}
        print(f"  {name} {ph}: ret={s.get('total_return'):+.3f} sharpe={s.get('sharpe')} ({time.time()-t0:.0f}s)", flush=True)
json.dump(runs, open(os.path.join(os.environ["TEMP"],"r8_curves.json"),"w",encoding="utf-8"), ensure_ascii=False)
# blends
def blend(ca,cb,wa=0.5,rebalance=True):
    da=dict(ca); db=dict(cb)
    dates=[d for d,_ in ca if d in db]
    if not dates: return []
    if rebalance:
        ba=wa; out=[(dates[0],1.0)]
        for i in range(1,len(dates)):
            ra=da[dates[i]]/da[dates[i-1]]-1; rb=db[dates[i]]/db[dates[i-1]]-1
            r=ba*ra+(1-ba)*rb
            out.append((dates[i],out[-1][1]*(1+r)))
        return out
    # no rebalance: fixed initial shares
    a0=da[dates[0]]; b0=db[dates[0]]
    return [(d, wa*da[d]/a0+(1-wa)*db[d]/b0) for d in dates]
out={}
combos={"A_v1v2_reb":("v1","v2",0.5,True),"B_v1v2_noreb":("v1","v2",0.5,False),
        "C_v2C1_reb":("v2","C1",0.5,True)}
agg={}
for label,(n1,n2,w,reb) in combos.items():
    per=[]
    for ph in phases:
        bc=blend(runs[n1][ph],runs[n2][ph],w,reb)
        dr=rets(bc); y=yearly_of(bc)
        exc={k:round(y[k]-BM[k],4) for k in y if k in BM}
        per.append({"phase":ph,"ret":bc[-1][1]/bc[0][1]-1,"mdd":mdd_of(bc),"sharpe":sharpe_of(dr),
                    "yearly":{k:round(v,4) for k,v in y.items()},"excess":exc})
    shs=[p["sharpe"] for p in per]; mdds=[p["mdd"] for p in per]; rr=[p["ret"] for p in per]
    years=[str(y) for y in range(2019,2027)]
    pos=sum(1 for y in years if st.mean([p["excess"].get(y,0.0) for p in per])>0)
    agg[label]={"ret_mean":st.mean(rr),"sharpe_mean":st.mean(shs),"mdd_mean":st.mean(mdds),"pos_years":pos,
                "yearly_mean_excess":{y:round(st.mean([p["excess"].get(y,0.0) for p in per]),4) for y in ["2018"]+years}}
    print(f"== {label}: ret_mean={st.mean(rr):+.3f} sharpe_mean={st.mean(shs):.2f} mdd_mean={st.mean(mdds):+.3f} pos={pos}/8", flush=True)
    print("   excess:",agg[label]["yearly_mean_excess"], flush=True)
# v1/v2 correlation per phase
cors=[]
for ph in phases:
    ra=rets(runs["v1"][ph]); rb=rets(runs["v2"][ph])
    common=[d for d in ra if d in rb]
    xa=[ra[d] for d in common]; xb=[rb[d] for d in common]
    ma=st.mean(xa); mb=st.mean(xb)
    cov=sum((x-ma)*(yy-mb) for x,yy in zip(xa,xb))/len(xa)
    sa=st.pstdev(xa); sb=st.pstdev(xb)
    cors.append(cov/(sa*sb))
print(f"v1-v2 daily return corr: mean={st.mean(cors):.3f} min={min(cors):.3f} max={max(cors):.3f}", flush=True)
out={"agg":agg,"corr":{"mean":st.mean(cors),"min":min(cors),"max":max(cors)},
     "runs_summary":{n:{ph:{"ret":runs[n][ph][-1][1]/runs[n][ph][0][1]-1} for ph in phases} for n in runs},
     "bm_yearly":{k:round(v,5) for k,v in BM.items()}}
# individual per-phase aggregates for reference
for n in ("v1","v2","C1"):
    per=[]
    for ph in phases:
        c=runs[n][ph]; y=yearly_of(c)
        per.append({"ret":c[-1][1]/c[0][1]-1,"mdd":mdd_of(c),"sharpe":sharpe_of(rets(c)),
                    "excess":{k:round(y[k]-BM[k],4) for k in y if k in BM}})
    years=[str(y) for y in range(2019,2027)]
    pos=sum(1 for y in years if st.mean([p["excess"].get(y,0.0) for p in per])>0)
    out[f"single_{n}"]={"ret_mean":st.mean([p["ret"] for p in per]),"sharpe_mean":st.mean([p["sharpe"] for p in per]),
                        "mdd_mean":st.mean([p["mdd"] for p in per]),"pos_years":pos,
                        "yearly_mean_excess":{y:round(st.mean([p["excess"].get(y,0.0) for p in per]),4) for y in ["2018"]+years}}
    print(f"single {n}: ret_mean={out[f'single_{n}']['ret_mean']:+.3f} sharpe={out[f'single_{n}']['sharpe_mean']:.2f} mdd={out[f'single_{n}']['mdd_mean']:+.3f} pos={pos}/8", flush=True)
json.dump(out, open(os.path.join(os.environ["TEMP"],"r8.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved r8.json")
