# R451 当前研究前沿

| 项目 | 结果 | 状态 |
| --- | --- | --- |
| 因子库 | 235 个，118 个有研究证据 | 索引 |
| R418 严格准入 | 5 个 | 研究准入 |
| R450 行业+规模中性补充准入 | 2/5 个 | 降级证据 |
| 参数族 MC | 62 族覆盖，11 推荐 / 51 拒绝 | 非全部合格 |
| 4 因子预注册选中 | 检验年化 3.03%，p05 -19.02% | 拒绝 |
| 4 因子最佳点估计 | 10 仓 / 40 日，检验年化 11.28%，p05 -10.59% | 非预注册选中，不可推荐 |
| 5 因子组合 | 检验年化 3.75%，Sharpe 0.350，p05 -18.76% | 拒绝 |
| 防御 ETF | risk_parity_vol60 检验年化 3.58%，p05 -4.48% | 拒绝 |

## 结论

目前没有通过无杠杆、正尾部、近一年为正三项要求的最终策略。最接近可用的是 4 因子 HS300 组合的最佳点估计，但它不是预注册选中项，bootstrap 尾部仍为负；因此不能把点估计当作上线依据。

## 下一步

- 继续在已有严格准入因子上做组合层验证，先要求中性化后信号成立。
- 参数优化只接受相邻平台、分段同号和 bootstrap 下界为正的参数。
- ETF 与 HS300 的简单止损、现金底仓、波动率目标和行业轮动已经证伪，不重复试错。
- 每一轮保留负知识并提交，不以单个漂亮窗口替代预注册门禁。

## 边界

- This synthesis reuses frozen evidence and does not rerun statistics.
- The test and recent windows have been observed before.
- Current HS300 membership applied to history retains survivorship bias.
- A point estimate is not a selection rule and cannot be promoted.
- No leverage; 5x cost remains warning-only.
