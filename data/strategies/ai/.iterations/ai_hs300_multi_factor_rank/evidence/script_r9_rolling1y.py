import csv, json, math, statistics as st, urllib.request
EV=r"D:\Code\hs300\data\strategies\ai\.iterations\ai_hs300_multi_factor_rank\evidence\v2_equity_2018-06-01_2026-10-14.csv"
rows=[]
with open(EV,encoding="utf-8") as f:
    for r in csv.DictReader(f): rows.append((r["date"],float(r["value"])))
vals=[v for _,v in rows]; dates=[d for d,_ in rows]
# rolling 252 trading-day returns
W=252
r1y=[]
for i in range(W,len(vals)):
    r1y.append((dates[i], vals[i]/vals[i-W]-1))
arr=[r for _,r in r1y]
pos=sum(1 for r in arr if r>0)/len(arr)
print(f"v2 rolling 1y windows (n={len(arr)}, {dates[W]} ~ {dates[-1]}):")
print(f"  正收益概率 {pos:.1%} | 中位 {st.median(arr):+.1%} | P10 {sorted(arr)[len(arr)//10]:+.1%} | P90 {sorted(arr)[int(len(arr)*0.9)]:+.1%}")
print(f"  最差 {min(arr):+.1%} ({[d for d,r in r1y if r==min(arr)][0]}) | 最好 {max(arr):+.1%} ({[d for d,r in r1y if r==max(arr)][0]})")
# index rolling 1y
u="http://127.0.0.1:3018/api/kline/daily?symbol=000300.SH&start_date=2018-06-01&end_date=2026-10-14"
with urllib.request.urlopen(u,timeout=60) as resp: d=json.loads(resp.read().decode())
irows=sorted(d.get("rows") or [], key=lambda x:x["date"])
ic=[x["close"] for x in irows]; idates=[x["date"] for x in irows]
imap=dict(zip(idates,ic))
pa=[]
for dt,_ in r1y:
    j=idates.index(dt) if dt in imap else None
    if j is None or j<W: continue
    pa.append((dt, ic[j]/ic[j-W]-1))
exc=[(dt, dict(r1y)[dt]-r) for dt,r in pa]
epos=sum(1 for _,e in exc if e>0)/len(exc)
ipos=sum(1 for _,r in pa if r>0)/len(pa)
print(f"沪深300 rolling 1y: 正收益概率 {ipos:.1%}")
print(f"v2 滚动1年**跑赢指数**概率: {epos:.1%} | 超额中位 {st.median([e for _,e in exc]):+.1%}")
print("最近5个滚动1年超额:", [(dt, f"{e:+.1%}") for dt,e in exc[-5:]])
# monthly returns of last 13 months
idx={d:i for i,d in enumerate(dates)}
def month_last(y,m):
    ds=[d for d in dates if d[:7]==f"{y}-{m:02d}"]
    return ds[-1] if ds else None
mv=[]
for y,m in [(2025,10),(2025,11),(2025,12),(2026,1),(2026,2),(2026,3),(2026,4),(2026,5),(2026,6),(2026,7),(2026,8),(2026,9),(2026,10)]:
    d1=month_last(y,m)
    if not d1: continue
    prev=[d for d in dates if d<d1[:8]+"01"]
    d0=prev[-1] if prev else None
    if d0: mv.append((f"{y}-{m:02d}", vals[idx[d1]]/vals[idx[d0]]-1))
print("近13个月:", [(k,f"{v:+.1%}") for k,v in mv])
