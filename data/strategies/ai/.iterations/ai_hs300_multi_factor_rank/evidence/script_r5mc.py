import json, os, math, random
import statistics as st
T=os.environ["TEMP"]
s1=json.load(open(os.path.join(T,"r3_step1.json"),encoding="utf-8"))
eq=s1["v2"]["equity_curve"]
vals=[float(x.get("value") or x.get("close")) for x in eq if (x.get("value") or x.get("close"))]
rets=[vals[i]/vals[i-1]-1 for i in range(1,len(vals))]
def mdd(vs):
    peak=vs[0]; m=0.0
    for v in vs:
        peak=max(peak,v); m=min(m, v/peak-1)
    return m
random.seed(20261014)
B=20; N=len(rets); nb=(N+B-1)//B
sims=[]
for _ in range(3000):
    rs=[]
    for _ in range(nb):
        j=random.randrange(0,N-B+1); rs.extend(rets[j:j+B])
    rs=rs[:N]
    vs=[1.0]
    for r in rs: vs.append(vs[-1]*(1+r))
    sims.append((vs[-1]-1, mdd(vs)))
tr=sorted(s for s,_ in sims); md=sorted(m for _,m in sims)  # md ascending = worst first
def q(a,p): return a[min(len(a)-1,max(0,int(p*len(a))))]
res={"n_sims":3000,"block":B,
  "total_p01":q(tr,0.01),"total_p05":q(tr,0.05),"total_p25":q(tr,0.25),"total_p50":q(tr,0.50),
  "mdd_worst1":md[0],"mdd_p05":q(md,0.05),"mdd_p25":q(md,0.25),"mdd_p50":q(md,0.50),"mdd_best95":q(md,0.95),
  "p_loss":sum(1 for s,_ in sims if s<0)/3000,
  "p_mdd_worse_30":sum(1 for _,m in sims if m<-0.30)/3000,
  "p_mdd_worse_25":sum(1 for _,m in sims if m<-0.25)/3000,
  "real_total":vals[-1]/vals[0]-1,"real_mdd":mdd(vals)}
print(json.dumps({k:(round(v,4) if isinstance(v,float) else v) for k,v in res.items()},ensure_ascii=False,indent=1))
json.dump(res, open(os.path.join(T,"r5mc.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
