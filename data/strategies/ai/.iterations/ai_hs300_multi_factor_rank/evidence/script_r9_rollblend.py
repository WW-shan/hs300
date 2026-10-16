import json, os, statistics as st, urllib.request
T=os.environ["TEMP"]
runs=json.load(open(os.path.join(T,"r8_curves.json"),encoding="utf-8"))
u="http://127.0.0.1:3018/api/kline/daily?symbol=000300.SH&start_date=2018-05-31&end_date=2026-10-14"
with urllib.request.urlopen(u,timeout=60) as resp: d=json.loads(resp.read().decode())
irows=sorted(d.get("rows") or [], key=lambda x:x["date"])
imap={x["date"]:x["close"] for x in irows}
W=252
def blend_curve(ca,cb,w=0.5):
    db=dict(cb); dates=[dd for dd,_ in ca if dd in db]
    da=dict(ca); out=[(dates[0],1.0)]
    for i in range(1,len(dates)):
        ra=da[dates[i]]/da[dates[i-1]]-1; rb=db[dates[i]]/db[dates[i-1]]-1
        out.append((dates[i],out[-1][1]*(1+w*ra+(1-w)*rb)))
    return out
def stats_of(curves,label):
    pos=[];beat=[];rets=[]
    for cur in curves:
        vals=[v for _,v in cur]; dates=[dd for dd,_ in cur]
        for i in range(W,len(vals)):
            r=vals[i]/vals[i-W]-1; rets.append(r); pos.append(1 if r>0 else 0)
            d0,d1=dates[i-W],dates[i]
            if d0 in imap and d1 in imap:
                beat.append(1 if r-(imap[d1]/imap[d0]-1)>0 else 0)
    s=sorted(rets)
    print(f"{label}: n={len(s)} 正收益={sum(pos)/len(pos):.1%} 跑赢指数={sum(beat)/len(beat):.1%} 中位={st.median(s):+.1%} P05={s[len(s)//20]:+.1%} 最差={s[0]:+.1%}")
stats_of([runs["v2"][ph] for ph in runs["v2"]], "v2 单用      ")
stats_of([runs["v1"][ph] for ph in runs["v1"]], "v1 单用      ")
stats_of([blend_curve(runs["v1"][ph],runs["v2"][ph],0.5) for ph in runs["v1"]], "v1+v2 各半  ")
stats_of([blend_curve(runs["v2"][ph],runs["C1"][ph],0.5) for ph in runs["v2"]], "v2+C1 各半   ")
