import csv, json, os, statistics as st, urllib.request
T=os.environ["TEMP"]
runs=json.load(open(os.path.join(T,"r8_curves.json"),encoding="utf-8"))
# benchmark map
u="http://127.0.0.1:3018/api/kline/daily?symbol=000300.SH&start_date=2018-05-31&end_date=2026-10-14"
with urllib.request.urlopen(u,timeout=60) as resp: d=json.loads(resp.read().decode())
irows=sorted(d.get("rows") or [], key=lambda x:x["date"])
imap={x["date"]:x["close"] for x in irows}
W=252
allpos=[]; allbeat=[]; allret=[]
for ph,cur in runs["v2"].items():
    vals=[v for _,v in cur]; dates=[dd for dd,_ in cur]
    for i in range(W,len(vals)):
        r=vals[i]/vals[i-W]-1
        d0,d1=dates[i-W],dates[i]
        if d0 in imap and d1 in imap:
            br=imap[d1]/imap[d0]-1
        else:
            br=None
        allret.append(r); allpos.append(1 if r>0 else 0)
        if br is not None: allbeat.append(1 if r-br>0 else 0)
print(f"相位合并滚动1年 (10相位 x ~1780窗口, n={len(allret)}):")
print(f"  正收益 {sum(allpos)/len(allpos):.1%} | 跑赢沪深300 {sum(allbeat)/len(allbeat):.1%}")
s=sorted(allret)
print(f"  中位 {st.median(s):+.1%} | P10 {s[len(s)//10]:+.1%} | P05 {s[len(s)//20]:+.1%} | 最差 {s[0]:+.1%} | 最好 {s[-1]:+.1%}")
print(f"  负收益窗口占比 {1-sum(allpos)/len(allpos):.1%}")
# 因子近一年 IC (2025-10-14~2026-10-14)
def post(path,payload):
    r=urllib.request.Request("http://127.0.0.1:3018"+path,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=1200) as resp: return json.loads(resp.read().decode())
res=post("/api/backtest/factor/batch",{"factor_names":["log_amount","log_float_mv","annual_vol_20d"],"start":"2025-10-14","end":"2026-10-14","n_groups":5,"rebalance":"monthly","hs300":True})
print("近一年因子IC (1y):")
for r in res.get("results") or []:
    if r.get("error"): print("  ",r.get("factor_name"),"ERR",r["error"][:60]); continue
    print(f"  {r['factor_name']:18s} IC={r['ic_mean']:+.3f} 胜率={r.get('ic_win_rate')} 多空={r.get('long_short_return')}")
