# R425 研究总账：目前没有可直接上线的无杠杆策略

## 因子库

- R391 统计通过：11 个
- R418 严格准入：4 个
- 准入名单：ext_alt_hs300_dividend_yield, ext_alt_hs300_fund_holding_ratio_low, ext_alt_hs300_idio_vol_40d_low, ext_alt_hs300_vol_of_vol_60d_low
- 统计通过但被拒绝：amihud_20d, ext_alt_hs300_holder_top10_share_chg_qoq, ext_alt_hs300_holder_top10_share_chg_yoy, ext_alt_hs300_lhb_event_count_20d_low, ext_alt_hs300_lhb_inst_net_buy_ratio_20d, ext_alt_hs300_short_ratio_low, inventory_turnover_improvement_yoy

## 财报与情绪

- 财报多周期合格：7 个候选-周期；PB 与 OCF/债务在 5/20/60 日均过中性 IC。
- PB 加入 8 腿组合：rejected（未赢基线训练/验证）。
- 纯 PB 独立策略：rejected（检验年化 -8.21%）。
- 情绪因子统计通过：new_panic_reversal_5d；组合替换：rejected。

## 当前组合尾部

| 候选 | 检验年化 | Sharpe | MDD | 检验p05 | 近一年p05 |
| --- | ---: | ---: | ---: | ---: | ---: |
| current_8f | 7.06% | 0.630 | -13.28% | -12.12% | -10.03% |
| compact_4f | 6.02% | 0.530 | -12.06% | -13.98% | -12.74% |

## 最终判断

- **当前 8 腿是最接近可用的候选，但尾部仍为负，不能称为最终有效策略。**
- 4 腿候选的检验年化更低，尾部更差；不能因为训练/验证好看就推荐。
- PB/OCF 的 IC 证据成立，但独立组合和现有组合替换均失败，只能作为后续条件/滤网研究，不作为独立策略。
- 情绪恐慌反转统计通过，但组合替换失败；不进入因子集。

## 下一步优先级

1. 先做组合层尾部控制，不再新增因子：测试持仓数、行业暴露、现金/指数底仓与再平衡规则。
2. 若组合层仍不能使 p05 转正，转向纯防御资产/ETF 轮动，不再强行从 HS300 因子中找绝对收益。
3. 新的财报/情绪因子只有在 R418 同等级年度稳定性 + R420 同等级尾部审计都通过后，才允许进入组合池。

## 边界

- Synthesis uses existing evidence only.
- No new statistical or portfolio experiment is run.
- The test windows have been observed before.
- Current HS300 membership applied to history retains survivorship bias.
