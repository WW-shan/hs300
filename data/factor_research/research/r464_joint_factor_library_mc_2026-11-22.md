# R464 因子库联合蒙特卡洛

本轮把已准入因子袖、R452 价值质量复合因子和 R463 近候选放进同一套预注册联合抽样；训练+验证只用于选样，检验+近一年只做确认。

- 样本数：101
- 训练+验证合格：62
- 验证 bootstrap 正尾：0
- 邻域稳定：0
- 选中：`lhs_044` / seven_vq / 10 仓 / 35 日 / 暴露 100%
- 判定：**拒绝**
- 检验年化：8.09%
- 检验 Sharpe：0.690
- 检验 MDD：-11.19%
- 近一年年化：1.90%
- 检验 p05：-11.06%
- 近一年 p05：-17.40%

## 门槛

- training_platform_exists: 通过
- validation_bootstrap_positive: 未过
- neighborhood_platform_pass: 未过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 通过
- recent_annual_min: 通过
- stress_3x_positive: 通过
- test_total_p05_positive: 未过
- recent_total_p05_positive: 未过
- no_leverage: 通过

## 选中参数

- 权重：{"ext_alt_hs300_cash_flow_stability_4q_low": 0.1357142857142857, "ext_alt_hs300_dividend_yield": 0.1357142857142857, "ext_alt_hs300_fund_holding_ratio_low": 0.1357142857142857, "ext_alt_hs300_holder_top10_share_chg_yoy": 0.1357142857142857, "ext_alt_hs300_idio_vol_40d_low": 0.1357142857142857, "ext_alt_hs300_ocf_margin_4q": 0.1357142857142857, "ext_alt_hs300_vol_of_vol_20d_low": 0.1357142857142857, "value_quality": 0.05}
- 入场/退出：60 / 50
- 3× 检验年化：6.91%
- 5× warning 检验年化：4.59%

## 边界

- The test and recent windows have been observed before; this is a new pre-registered protocol, not a blind holdout.
- Current HS300 membership and industry classification retain survivorship bias.
- The value-quality composites are built from existing catalog factors, not new raw data sources.
- Joint Monte Carlo samples are correlated, not independent trials.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
