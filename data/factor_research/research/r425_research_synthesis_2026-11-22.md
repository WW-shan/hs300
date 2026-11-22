# R425 研究总账：目前没有可直接上线的无杠杆策略

> 最新前沿以 R451 为准；R446/R447 的 ETF 数值受时序缺陷影响，修正结果见 R448。

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

## 追加证据（R435）

- 新增可选双准入守卫：组合候选可同时要求 P6 `portfolio_ready` 与 R418 `research_admitted`，默认行为保持兼容。
- 当前 8 腿中只有 3 腿同时通过双准入：股息率、基金持仓比例低位、40 日残差低波。
- `vol_of_vol`、`margin_chg`、`holder_top10` 只有 P6 就绪，缺 R418 统计准入。
- `cash_flow_stability`、`ocf_margin` 在 P6 层即 blocked（financial_pit_not_verified / future_data_unknown）。
- 结论：当前 8 腿组合在严格治理口径下本身不是“全部合格因子”的组合；收益问题之前，先存在准入结构问题。

## 追加证据（R436–R440）

- 扩展财务因子 `cash_flow_stability_4q_low`、`ocf_margin_4q` 完成独立 PIT 审计和未来数据审计，构建路径均使用法定截止日、截止日后生效、backward join，前缀不变。
- taxonomy 显式迁移 `future_data_status=clear`，参数注册表从 blocked 移到 fixed；P6 portfolio_ready 从 176 增至 178。
- 严格研究准入：cash_flow_stability 在 60 日通过边际 IC 准入；ocf_margin 未通过；vol_of_vol_20d 也未通过（年度同号 0.625，边际 IC 不显著）。
- 双准入复核：当前 8 腿从 3 腿提升到 4 腿；仍不合格的是 vol_of_vol_20d、ocf_margin、margin_chg、holder_top10。
- 结论：数据/PIT 层已修复，但统计/组合准入仍只覆盖一半当前组合。

## 追加证据（R441）

- `margin_chg_1m_low` 5 日边际 IC 0.000718、NW p=0.032、q=0.096，但年度同号仅 0.667，严格规则拒绝。
- `holder_top10_share_chg_yoy` 已由 R418 以年度同号 0.5 拒绝，未重复测试。
- 当前 8 腿双准入最终为 4/8：股息率、基金持仓比例低位、40 日残差低波、现金流稳定性 4Q。
- 另外 4 腿（vol_of_vol_20d、ocf_margin、margin_chg、holder_top10）均未通过严格研究准入。

## 追加证据（R442）

- 仅使用 R439 的 4 个双准入因子重建组合，10 仓/40 日点估计：训练 27.18%、验证 19.26%、检验 11.28%、检验 Sharpe 0.91、MDD -11.69%、3× 10.18%。
- 但检验 bootstrap p05 -10.59%、近一年 p05 -13.39%，严格门槛仍拒绝。
- 结论：治理合格组合的点估计优于当前 8 腿，但尾部风险并未解决；“准入合格”不等于“尾部合格”。

## 追加证据（R443）

- 在 4 因子 10 仓/40 日上测试止损、移动止损、组合 overlay。
- 训练+验证唯一选出的是 10% 止损；但检验年化 4.72%、Sharpe 0.42、p05 -17.79%，差于无止损基线 11.28%。
- 结论：止损类尾部控制破坏收益且未改善尾部；4 因子组合需要非止损型风险预算，而不是价格止损。

## 追加证据（R444）

- 在 4 因子 10 仓/40 日上测试固定现金、指数+现金、波动率目标降仓。
- 没有任何 overlay 同时在训练+验证上超过 baseline；选中 baseline。
- baseline 检验 p05 -10.22%、近一年 p05 -13.62%，尾部仍不合格。
- 结论：简单现金/指数/波动率预算不能解决该组合的尾部；价格止损（R443）也无效。

## 追加证据（R446）

- akshare provider 实际可返回 6 个核心 ETF 的 2018–2026 长历史；本地已补齐 510300、510500、512100、510880、511010、518880 的 daily/enriched。
- 测试 20/60/120 日相对动量与风险调整动量，月度换仓。
- 最好规则 momentum_20：检验年化 8.68%、Sharpe 0.504，但 MDD -26.68%、检验 p05 -27.65%、近一年 -12.87%。
- 结论：简单防御 ETF 轮动仍不满足低回撤/尾部要求；数据可行性解决，不等于策略可行。

## 追加证据（R447）

- R446 的 ETF 轮动模拟器存在 open-to-open 时序错位，R447 用正确的月末信号、下一交易日开盘换仓重算。
- 修正后最好规则 momentum_20：检验年化 11.97%、Sharpe 0.651，但 MDD -22.68%、检验 p05 -23.02%、近一年 -8.67%。
- R447 判定拒绝；R446 旧结果不作为有效证据。
- 结论：简单防御 ETF 轮动在正确时序下仍不具备低回撤/正尾部能力。
