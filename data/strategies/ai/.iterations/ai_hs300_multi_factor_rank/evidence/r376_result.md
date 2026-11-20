# R376 · 市场状态条件化真实回测

基准：R374 平台中心 `fade05_h30_sl20`，2× 年化 13.21%、Sharpe 1.395、MDD -8.01%。

测试 195 个条件化变体；核心门槛通过 156 个，增量门槛通过 0 个，全部门槛（含邻域平台和配对 bootstrap）通过 0 个。

**结论：没有条件化变体通过全部门槛，状态条件化记为负知识，基准保持不变。**
| 原子 | 阈值 | 低仓位 | 年化 | Sharpe | MDD | 增量 | 平台 | 全过 |
| --- | ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: |
| breadth_above_ma20 | 0.20 | 85% | 13.18% | 1.395 | -8.01% | False | False | False |
| breadth_above_ma20 | 0.15 | 20% | 13.21% | 1.395 | -8.01% | False | False | False |
| breadth_above_ma20 | 0.15 | 30% | 13.21% | 1.395 | -8.01% | False | False | False |
| breadth_above_ma20 | 0.15 | 50% | 13.21% | 1.395 | -8.01% | False | False | False |
| breadth_above_ma20 | 0.15 | 70% | 13.21% | 1.395 | -8.01% | False | False | False |
| breadth_above_ma20 | 0.15 | 85% | 13.21% | 1.395 | -8.01% | False | False | False |
| index_momentum_20d | -0.05 | 20% | 13.21% | 1.395 | -8.01% | False | False | False |
| index_momentum_20d | -0.05 | 30% | 13.21% | 1.395 | -8.01% | False | False | False |

- 提高 Sharpe 但年化损失超过 0.5pp 的变体不算通过；只降仓不改善风险收益比的变体不算通过。
- 条件化原子和阈值已在一次探索性草稿中出现，本轮是固定假设的确认检验，不是盲测发现。
- 无杠杆、不生成纸面目标、订单或生产配置。
