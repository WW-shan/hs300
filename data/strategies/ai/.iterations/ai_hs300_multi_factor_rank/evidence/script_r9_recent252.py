import json, os
T=os.environ["TEMP"]
runs=json.load(open(os.path.join(T,"r8_curves.json"),encoding="utf-8"))
# trailing 252-day return ending 2026-10-14 (or last available), per phase
for name in ("v1","v2","C1"):
    outs=[]
    for ph,cur in runs[name].items():
        vals=[v for _,v in cur]
        if len(vals)>253:
            outs.append(vals[-1]/vals[-253]-1)
        else:
            outs.append(vals[-1]/vals[0]-1)
    outs=sorted(outs)
    import statistics as st
    print(f"{name:3s} 最近252交易日 (10相位): min={outs[0]:+.1%} 中位={st.median(outs):+.1%} max={outs[-1]:+.1%} 全正={all(x>0 for x in outs)}")
