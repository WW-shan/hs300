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

## 追加证据（R427–R431）

- 动态状态权重：检验年化 3.66%，低于静态 7.06%；MDD -15.36%，更差，拒绝。
- 财报质量缺口：FCF/收入、OCF/收入在 5/20/60 日单因子中性 IC 均通过。
- 但两者对当前 8 腿的边际 IC 在 5/20/60 日全部为负或接近零，合格周期为 0。
- FCF/OCF 替换当前 8 腿后训练/验证均下降，拒绝。
- PB+OCF+低波独立组合检验年化 -1.85%，拒绝。
- 结论：财报质量因子有独立截面信号，但对当前 8 腿没有组合边际价值；继续硬塞不会改善策略。

## 追加证据（R432）

- 当前 8 腿中只有 `idio_vol` 和 `vol_of_vol` 两个 MC 可调参数族；其余 6 腿是固定口径。
- `idio_vol` 20 日、`vol_of_vol` 60 日、两者同时替换，训练年化分别 15.78%/15.07%/15.21%，均低于当前 17.85%；验证也全部下降。
- 结论：MC 推荐参数在单因子/参数族层面有效，但直接替换当前组合会破坏组合互补性，不能把 MC 推荐值当成组合最优值。

## 追加证据（R433–R434）

- 62 个注册参数族全部有 MC 覆盖；推荐 11 族、拒绝 51 族。
- 当前 8 腿只映射到 2 个可调族（idio_vol、vol_of_vol），另有 9 个“孤儿推荐”不在组合中。
- 9 个孤儿推荐里只有 amihud 同时满足 R391 统计通过；其余 amount_level、turnover_level、max_return 的增量 IC 为负且被统计拒绝。
- amihud 120 替换 holder 后，训练 17.85%→13.61%、验证 27.57%→23.04%，被拒绝。
- 结论：MC 推荐参数和统计通过因子都不能直接等于组合增量；当前组合的参数选择不是“全部合格”，而是少数可调族在当前结构下的折中。
