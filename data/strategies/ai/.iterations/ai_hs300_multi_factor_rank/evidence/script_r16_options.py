"""R16: 用户实测反馈二 (近一年仍亏) — 三个部署形态在"近一年各起始相位"与"全历史"下的对照。

产出 evidence/r16_recent_year_options.json。口径: HS300 当前成分, T+1 开盘成交, 默认成本
(佣金 2bp + 印花税 10bp 卖出 + 滑点 5bp); v2 = ai_hs300_lowrisk_alpha (现文件 MAX_HOLD_DAYS=35),
v1 = ai_hs300_multi_factor_rank (止损 -20%, maxpos=20), 50/50 = 两条日净值曲线的每日再平衡合成 (朴素线性)。
"""
import sys, json, statistics as st, os

BASE = "http://127.0.0.1:3018"
TOKEN = os.environ.get("TF_SESSION", "")
import json as _json, time as _time, urllib.request as _ur
def post(path, payload, timeout=3600):
    req = _ur.Request(BASE + path, data=_json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json", "Cookie": f"tf_session={TOKEN}"})
    with _ur.urlopen(req, timeout=timeout) as resp:
        return _json.loads(resp.read().decode())
def run_bt(strategy_id="ai_hs300_lowrisk_alpha", start="2018-06-01", end="2026-10-14", maxpos=10, **kw):
    p = {"strategy_id": strategy_id, "start": start, "end": end, "matching": "open_t+1", "mode": "position",
         "hs300": True, "max_positions": maxpos, "position_sizing": "equal", "holding_days": 5, "asset_type": "stock"}
    r = post("/api/backtest/strategy/run", p)
    s = r.get("stats") or {}
    fa = (r.get("factor_attribution") or {}).get("factors") or []
    return s, [f.get("factor") for f in fa], r.get("error"), 0.0, r

PH = ["2025-10-14", "2025-10-15", "2025-10-16", "2025-10-17", "2025-10-20"]
EV = os.path.dirname(os.path.abspath(__file__)) + "/"

def curve_map(raw):
    return {str(x.get("date"))[:10]: x.get("value") for x in (raw.get("equity_curve") or [])}

def blend(ca, cb):
    a, b = curve_map(ca), curve_map(cb)
    dts = sorted(set(a) & set(b))
    ra = [a[dts[i]]/a[dts[i-1]]-1 for i in range(1, len(dts))]
    rb = [b[dts[i]]/b[dts[i-1]]-1 for i in range(1, len(dts))]
    bl = [(x+y)/2 for x, y in zip(ra, rb)]
    eq = 1.0; peak = 1.0; mdd = 0.0
    for x in bl:
        eq *= 1+x; peak = max(peak, eq); mdd = min(mdd, eq/peak-1)
    return {"ret": eq-1, "mdd": mdd, "sharpe": st.mean(bl)/st.stdev(bl)*(252**0.5)}

out = {"phases": PH, "recent": [], "full": None}
print(f"{'起始':<12}{'v2@35':>9}{'v1@20':>9}{'50/50':>9}{'50/50_MDD':>11}")
for p in PH:
    s2, _, _, _, r2 = lib.run_bt(start=p, end="2026-10-14")
    s1, _, _, _, r1 = lib.run_bt(strategy_id="ai_hs300_multi_factor_rank", start=p, end="2026-10-14", maxpos=20)
    b = blend(r2, r1)
    row = {"start": p, "v2_35": {"ret": s2.get("total_return"), "mdd": s2.get("max_drawdown"), "sharpe": s2.get("sharpe"), "trades": s2.get("n_trades")},
           "v1_20": {"ret": s1.get("total_return"), "mdd": s1.get("max_drawdown"), "sharpe": s1.get("sharpe"), "trades": s1.get("n_trades")},
           "blend_50_50": b}
    out["recent"].append(row)
    print(f"{p:<12}{s2.get('total_return')*100:>+8.1f}%{s1.get('total_return')*100:>+8.1f}%{b['ret']*100:>+8.1f}%{b['mdd']*100:>10.1f}%")
med = {k: st.median([r[k]["ret"] for r in out["recent"]]) for k in ("v2_35", "v1_20", "blend_50_50")}
pos = {k: sum(1 for r in out["recent"] if r[k]["ret"] > 0) for k in ("v2_35", "v1_20", "blend_50_50")}
out["recent_median"] = med; out["recent_positive_count"] = pos
print(f"\n中位: v2@35 {med['v2_35']*100:+.1f}% ({pos['v2_35']}/5 正) | v1@20 {med['v1_20']*100:+.1f}% ({pos['v1_20']}/5 正) | 50/50 {med['blend_50_50']*100:+.1f}% ({pos['blend_50_50']}/5 正)")

# 全历史 (单相位 2018-06-01 起)
s2, _, _, _, r2 = lib.run_bt(start="2018-06-01")
s1, _, _, _, r1 = lib.run_bt(strategy_id="ai_hs300_multi_factor_rank", start="2018-06-01", maxpos=20)
b = blend(r2, r1)
out["full"] = {"start": "2018-06-01",
               "v2_35": {"ret": s2.get("total_return"), "mdd": s2.get("max_drawdown"), "sharpe": s2.get("sharpe")},
               "v1_20": {"ret": s1.get("total_return"), "mdd": s1.get("max_drawdown"), "sharpe": s1.get("sharpe")},
               "blend_50_50": b}
print(f"全历史 2018-06-01~: v2@35 {s2.get('total_return')*100:+.0f}%/{s2.get('sharpe')}/{s2.get('max_drawdown')*100:.0f}% | "
      f"v1@20 {s1.get('total_return')*100:+.0f}%/{s1.get('sharpe')}/{s1.get('max_drawdown')*100:.0f}% | "
      f"50/50 {b['ret']*100:+.0f}%/{b['sharpe']:.2f}/{b['mdd']*100:.0f}%")
json.dump(out, open(EV + "r16_recent_year_options.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved r16_recent_year_options.json")
