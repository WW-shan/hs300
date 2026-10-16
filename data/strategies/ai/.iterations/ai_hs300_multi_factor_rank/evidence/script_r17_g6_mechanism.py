# R17 机制验证 (G6): 单相位 2018-06-01 起, 重建每日等权持仓, 对比
#   ① 持仓平均市值分位 |p-0.5|  ② 持仓平均行业 HHI
# 输入: 本地后端 (127.0.0.1:3018) + `export TF_SESSION=<tf_session cookie>`;
#       仓库内 data/ 的 enriched 日K / 同花顺行业 / HS300 当前成分缓存。
# 用法: python script_r17_g6_mechanism.py [b|c]   (b = R17b 的 N0/N5/N6/N7; c = R17c 的 M0/M1/M2)
import os, sys, json, time, statistics as st, urllib.request
import polars as pl

EV = os.path.dirname(os.path.abspath(__file__)) + "/"
REPO = os.path.abspath(os.path.join(EV, "..", "..", "..", "..", "..", ".."))
START = "2018-06-01"
V2 = {"uf_low_amount": 0.34, "uf_small_mv": 0.33, "uf_low_vol": 0.33}
BASE = {"scoring": V2, "scoring_replace": True, "max_hold_days": 35, "stop_loss": None,
        "basic_filter": {"amount_min": 0.5e8}}


def post(path, payload, timeout=3600):
    req = urllib.request.Request("http://127.0.0.1:3018" + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Cookie": f"tf_session={os.environ.get('TF_SESSION','')}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def run_bt(start=START, maxpos=10, overrides=None):
    p = {"strategy_id": "ai_hs300_lowrisk_alpha", "start": start, "end": "2026-10-14",
         "matching": "open_t+1", "mode": "position", "hs300": True, "max_positions": maxpos,
         "position_sizing": "equal", "holding_days": 5, "asset_type": "stock"}
    if overrides is not None:
        p["overrides"] = overrides
    t0 = time.time()
    r = post("/api/backtest/strategy/run", p)
    return r.get("stats") or {}, r.get("error"), time.time() - t0, r


GROUPS = {
    "b": {
        "N0": dict(BASE, neutralize={}),
        "N5": dict(BASE, neutralize={"industry": "industry_l1"}),
        "N6": dict(BASE, neutralize={"styles": ["log_float_mv"], "exclude": ["uf_small_mv"]}),
        "N7": dict(BASE, neutralize={"industry": "industry_l1", "styles": ["log_float_mv"],
                                     "exclude": ["uf_small_mv"]}),
    },
    "c": {
        "M0": dict(BASE, neutralize={}),
        "M1": dict(BASE, neutralize={"industry": "industry_l1", "shrink": 0.5}),
        "M2": dict(BASE, neutralize={"industry": "industry_l1", "styles": ["log_float_mv"], "shrink": 0.5}),
    },
}
# R18: 中性化 × 分散度 (maxpos 随候选变化)
MAXPOS = {"b": {}, "c": {}, "d": {"P2": 20, "P3": 20}}
GROUPS["d"] = {
    "P0": dict(BASE, neutralize={}),
    "P2": dict(BASE, neutralize={"industry": "industry_l1", "shrink": 0.5}),
    "P3": dict(BASE, neutralize={}),
}
OUT_JSON = {"b": "r17b_g6_mechanism.json", "c": "r17c_g6_mechanism.json", "d": "r18_g6_mechanism.json"}
MV_CHECK = {"b": ("N6", "N7"), "c": ("M2",), "d": ()}
HHI_CHECK = {"b": ("N5", "N7"), "c": ("M1",), "d": ("P2", "P3")}

group = sys.argv[1] if len(sys.argv) > 1 else "c"
CANDS = GROUPS[group]

with open(os.path.join(REPO, "data", "hs300", "current_members.json")) as fh:
    mem = [m["symbol"] for m in json.load(fh)["members"]]

mv = (pl.scan_parquet(os.path.join(REPO, "data", "kline_daily_enriched", "date=*", "part.parquet"), extra_columns="ignore")
      .filter(pl.col("symbol").is_in(mem))
      .select(["symbol", "date", "close", "volume", "turnover_rate"])
      .with_columns(pl.col("date").cast(pl.Utf8))
      .filter((pl.col("date") >= "2018-05-25") & (pl.col("date") <= "2026-10-14"))
      .filter(pl.col("turnover_rate") > 0)
      .with_columns(log_float_mv=(pl.col("close") * pl.col("volume") / pl.col("turnover_rate")))
      .with_columns(mv_pct=(pl.col("log_float_mv").rank().over("date") / pl.len().over("date")))
      .select(["symbol", "date", "mv_pct"]).collect())
MV = {(r[0], r[1]): r[2] for r in mv.iter_rows()}

EXT = pl.read_parquet(os.path.join(REPO, "data", "ext_data", "ext_hy_ths", "part.parquet"))
IND = {r[0]: r[1] for r in EXT.select(["symbol", "所属同花顺行业"]).iter_rows() if r[1]}


def ind_of(sym):
    v = IND.get(sym)
    return str(v).split("-")[0] if v else None


cal = (pl.scan_parquet(os.path.join(REPO, "data", "kline_index_daily", "date=*", "part.parquet"), extra_columns="ignore")
       .filter(pl.col("symbol") == "000300.SH").select("date").collect().sort("date")
       .with_columns(pl.col("date").cast(pl.Utf8))["date"].to_list())
cal = [x for x in cal if x >= START]

out = {"start": START, "group": group, "cands": {}}
t0 = time.time()
for tag, ov in CANDS.items():
    s, err, el, raw = run_bt(maxpos=MAXPOS[group].get(tag, 10), overrides=ov)
    if err:
        raise RuntimeError(f"{tag}: {err}")
    trades = raw.get("trades") or []
    pcts, hhis, by_date = [], [], {}
    for tr in trades:
        sym = tr.get("symbol"); ed = str(tr.get("entry_date"))[:10]; xd = str(tr.get("exit_date"))[:10]
        if not sym or not ed:
            continue
        ind = ind_of(sym)
        for x in [d for d in cal if d >= ed and (not xd or d <= xd)]:
            p = MV.get((sym, x))
            if p is not None:
                pcts.append(abs(p - 0.5))
            by_date.setdefault(x, []).append(ind)
    for inds in by_date.values():
        n = len(inds); cnt = {}
        for i in inds:
            cnt[i] = cnt.get(i, 0) + 1
        hhis.append(sum((c / n) ** 2 for c in cnt.values()))
    out["cands"][tag] = {"sharpe": s.get("sharpe"), "ret": s.get("total_return"), "n_trades": len(trades),
                         "mv_pct_absdev_mean": st.mean(pcts) if pcts else None, "mv_pct_n": len(pcts),
                         "hhi_mean": st.mean(hhis) if hhis else None, "hhi_n": len(hhis)}
    print(tag, out["cands"][tag], f"({time.time()-t0:.0f}s)", flush=True)

base_tag = {"b": "N0", "c": "M0", "d": "P0"}[group]
b = out["cands"][base_tag]
for tag in MV_CHECK[group]:
    v, bv = out["cands"][tag]["mv_pct_absdev_mean"], b["mv_pct_absdev_mean"]
    print(f"{tag} mv |p-0.5| {v:.4f} vs {bv:.4f} -> {(1-v/bv)*100:.1f}% 变化")
for tag in HHI_CHECK[group]:
    v, bv = out["cands"][tag]["hhi_mean"], b["hhi_mean"]
    print(f"{tag} HHI {v:.4f} vs {bv:.4f} -> {(1-v/bv)*100:.1f}% 下降")

with open(EV + OUT_JSON[group], "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
print("saved " + OUT_JSON[group])
