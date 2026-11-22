# R488 防御资产条件化叠加

本轮冻结 R484 股票 sleeve，只使用 T-1 回撤/regime 与国债/黄金 ETF 做条件化防御。

- 样本数：7
- 训练+验证合格：6
- 选中：`baseline`
- 判定：**拒绝**
- 检验年化：10.11%；Sharpe：0.739；MDD：-13.64%
- 近一年年化：6.96%；Sharpe：0.557；MDD：-13.07%
- 检验相对 HS300：+7.01%
- 近一年相对 HS300：+16.51%
- 检验 p05：-10.80%
- 近一年 p05：-12.05%

## 门槛

- training_platform_exists: 通过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 通过
- recent_annual_min: 通过
- test_bootstrap_positive: 未过
- recent_bootstrap_positive: 未过
- test_excess_hs300_positive: 通过
- no_leverage: 通过

## 边界

- The defensive ETF history is local ignored data and only covers available instruments.
- Equity and ETF sleeves use different execution models.
- Conditional rules use T-1 signals and may react late.
- Test/recent windows are reused confirmation windows.
- 5x equity cost is warning-only.
- No leverage and no production activation.
