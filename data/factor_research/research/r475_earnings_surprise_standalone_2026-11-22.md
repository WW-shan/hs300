# R475 业绩预告/快报 PEAD 独立 sleeve 确认

本轮只检验项目已有的预告/快报预期差字段，不覆盖 R100 的历史证据。

- 样本数：162
- 训练+验证合格：0
- 验证 bootstrap 正尾：0
- 选中：`combined|p10|h10|e60|x30`
- 判定：**拒绝**
- 检验年化：-6.82%
- 检验 Sharpe：-0.920
- 检验 MDD：-10.74%
- 近一年年化：-8.16%
- 检验 p05：-21.76%
- 近一年 p05：-18.03%

## 门槛

- training_platform_exists: 未过
- validation_bootstrap_positive: 未过
- test_annual_min: 未过
- test_sharpe_min: 未过
- test_mdd_min: 通过
- recent_annual_min: 未过
- stress_3x_positive: 未过
- test_total_p05_positive: 未过
- recent_total_p05_positive: 未过
- no_leverage: 通过

## 边界

- This is a standalone confirmation of existing PEAD fields, not a new data source.
- R100 historical-membership and event evidence are preserved.
- Current HS300 membership and current industry classification retain survivorship bias.
- The test and recent windows have been observed before.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
