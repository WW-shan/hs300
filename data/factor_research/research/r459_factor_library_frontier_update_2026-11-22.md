# R459 因子库前沿更新

| 项目 | 结果 |
| --- | --- |
| 因子库 | 235 个，118 个有研究证据 |
| R418 严格准入 | 5 个 |
| R450 行业+规模中性补充准入 | 2/5 个 |
| 参数族 MC | 62 族覆盖，11 推荐 / 51 拒绝 |
| 新财报复合 | value_quality, value_quality_safety |
| value_quality 60日中性IC | 0.0725 |
| value_quality 权重 bootstrap 下界 | 0.0586 |
| value_quality 组合 | 检验年化 17.27%，p05 -0.94%，拒绝 |
| 紧凑 PB+OCF 组合 | 近一年 p05 -18.22%，拒绝 |
| 防御资产混合 | 检验 p05 -12.92%，拒绝 |
| 情绪条件 | 通过单元：[{'emotion': 'market_profit_score', 'sleeve': 'idio_vol_40_low', 'horizon': '20'}] |

## 结论

本轮新增了可复现的财报价值/质量信号，但没有改变“尚无可用无杠杆最终策略”的结论。`value_quality` 的统计信号和权重稳定性成立，组合验证仍被近一年收益与 bootstrap 尾部否决；情绪条件只有一个 20 日单元通过，不能当成完整择时开关。

## 下一步

- 用 `value_quality` 做条件层，而不是继续在单一股票组合里硬拼权重。
- 继续对情绪条件做 T-1 时点、分年稳定性和交易成本审计。
- 把防御资产混合与股票复合因子分开验证，不把研究级混合当成可执行组合。
- 每一轮都保留负知识、更新总账并提交推送。

## 边界

- This update reuses frozen evidence and does not rerun statistics.
- A statistically valid composite can still fail portfolio and tail gates.
- The current HS300 membership and industry snapshot retain survivorship bias.
- Emotion condition results are conditional IC evidence, not a timing rule.
- No leverage and no production activation.
