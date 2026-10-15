import os
# 输入: r13_panel.parquet (未入仓库, 需本地重建). 重建方式 (backend venv):
#   df = pl.scan_parquet("data/kline_daily_enriched/date=*/part.parquet").select(
#       ["symbol","date","open","high","low","close","volume","amount","turnover_rate"]).collect()
#   df = df.with_columns(pl.col("date").cast(pl.Date))
#   out = compute_indicators(df, needed={"ma20","ma60","annual_vol_20d","momentum_10d","amplitude"})
#   out = out.with_columns([...])   # log_amount / turnover_mean_20d / amihud_20d / max_ret_20d / 振幅均值
#   out.write_parquet("<evidence>/r13_panel.parquet")
# (逐列定义见台账 R13-0: 与回测引擎 scoring_value_expr 的公式一致)
import polars as pl, json, statistics as st
p = pl.read_parquet(os.path.join(os.path.dirname(os.path.abspath(__file__)), "r13_panel.parquet"))
p = p.filter(pl.col("ma60").is_not_null())
p = p.with_columns([
    (-pl.col("log_amount")).alias("low_amount"),
    (-(pl.col("close")*pl.col("volume")/pl.col("turnover_rate")).log()).alias("small_mv"),
    (-pl.col("annual_vol_20d")).alias("low_vol"),
    (-pl.col("turnover_mean_20d")).alias("low_turnover"),
    pl.col("amihud_20d").alias("illiquidity"),
    (-pl.col("max_ret_20d")).alias("low_lottery"),
    (-pl.col("momentum_10d")).alias("short_reversal"),
    (-pl.col("amplitude_ma20")).alias("low_amplitude"),
])
legs = ["low_amount","small_mv","low_vol","low_turnover","illiquidity","low_lottery","short_reversal","low_amplitude"]
# coverage by year
cov = (p.group_by(pl.col("date").dt.year().alias("y"))
        .agg([pl.col(c).is_null().mean().round(4).alias(c) for c in legs] + [pl.len().alias("n")])
        .sort("y"))
print(cov)
# monthly sampled cross-sectional spearman
dates = (p.select("date").unique().sort("date").to_series().to_list())
sample = dates[::21]
rk = p.filter(pl.col("date").is_in(sample)).with_columns([pl.col(c).rank().over("date").alias(c) for c in legs])
corr = {}
for i,a in enumerate(legs):
    for b in legs[i+1:]:
        v = rk.group_by("date").agg(pl.corr(a,b).alias("c"))["c"].drop_nulls().to_list()
        corr[f"{a}~{b}"] = {"mean": round(st.mean(v),3), "min": round(min(v),3), "max": round(max(v),3), "n": len(v)}
for k,v in corr.items(): print(k, v)
json.dump({"coverage": cov.to_dicts(), "corr": corr, "sample_dates": len(sample)}, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "r13_dimension_screen.json"),"w"), ensure_ascii=False, indent=1)

# 注: 输入 r13_panel.parquet 由本地 enriched 面板构建 (compute_indicators 于 300 只 HS300 当前成分,
# 2018-01-02~2026-10-14, 589,892 行); 未随仓库提交 (数据不入库), 重建见台账 R13-0 说明。
