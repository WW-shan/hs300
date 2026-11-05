# R333 结果：R332 候选存在参数脆弱性

R333 不选择新参数，只检验 R332 的 40/60/90 日窗口 × 12/15/18% 目标波动邻域。

| 项目 | 结果 | 预注册门槛 |
| --- | ---: | ---: |
| 9 个 overlay 变体全过 | **3/9** | >=7/9 |
| 3 个 joint target 变体全过 | **2/3** | >=2/3 |
| 中心 60d/15% | 通过 | 必须通过 |
| 参数平台 | **失败** | 两项计数均达标 |

- 40 日窗口的 2022 均为 -0.036%；90 日窗口均为 -0.465%；60 日窗口均为 +0.143%。收益、Sharpe 和 MDD 本身没有问题，问题只是 2022 年符号随窗口翻转。
- 12% joint target 的 2022 为 +0.303%，18% joint target 为 -0.064%。
- 按 R333 预注册，R332 从 `candidate_for_paper_trading_review` 降级为 `research_only_parameter_fragile`，不能视为稳健的最终候选。
- 这个负结果说明：继续微调 lookback 或 target vol 只是在挑中心点；需要改变风险预算结构。

证据：`evidence/r333_parameter_plateau.json`、`evidence/r333_r332_parameter_plateau_preregistration.json`。
