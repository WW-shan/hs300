# R510 因子库前沿整合

## 当前规模

- 因子总数：235
- 有研究证据：118
- 统计通过：11
- 严格准入：5
- R499 核心成员：ext_alt_hs300_cash_flow_stability_4q_low, ext_alt_hs300_dividend_yield, ext_alt_hs300_fund_holding_ratio_low, ext_alt_hs300_idio_vol_40d_low, value_quality

## 组合层结论

- R503 滚动一年正收益：15/15
- R503 自然年正收益：5/5
- R506 验证 p05：4.68%
- R506 检验 p05：-4.83%
- R506 近一年 p05：-3.61%
- R508 可用增量：0
- R509 Amihud 方向修正后可用增量：0

## 未进入 R499 核心的统计通过因子

- `amihud_20d`
- `ext_alt_hs300_holder_top10_share_chg_qoq`
- `ext_alt_hs300_holder_top10_share_chg_yoy`
- `ext_alt_hs300_lhb_event_count_20d_low`
- `ext_alt_hs300_lhb_inst_net_buy_ratio_20d`
- `ext_alt_hs300_short_ratio_low`
- `ext_alt_hs300_vol_of_vol_60d_low`
- `inventory_turnover_improvement_yoy`

## 结论

当前因子库的统计通过因子没有在 R506 上形成训练+验证增量；继续在同一批因子上调权重或做邻域搜索，收益递减且增加过拟合风险。下一步应优先新增原始收益源或改进风控建模，而不是继续从现有 235 个因子中反复挑选。

## 边界

- This is a consolidation of existing evidence, not a new backtest.
- Current HS300 membership and industry classification retain survivorship bias.
- Test/recent windows have been observed before.
- No leverage and no production activation.
