# P9 财务期限分层与 Regime 条件化报告

- Research ID：`sha256:b9be9705a3b48bb69f050a57520541c8c9e71aa6b2f1f4960d64a8207bf5228a`
- 生成时间：`2026-11-11T17:32:08Z`
- P8 财务增量：`sha256:448cbd37b4faefc94025243be86b496779fcf649273d2e15b1a1835a42874038`
- T-1 Regime 指纹：`sha256:896cb3bc25916f9a7e965eae20506410176761420e01ca9eeaa876cc080b8f94`
- 决策：`no_statistical_candidate`
- `active_version` 保持 null；不生成纸面目标或订单。

## 期限资格

| 因子 | 60日 Gate | 确认中性化口径 | 原因 |
| --- | --- | --- | --- |
| `pb_latest` | pass | industry, size, industry_size | - |
| `accrual` | pass | industry, size, industry_size | - |
| `ocf_ni` | pass | industry, size | - |

## 组合候选

| 候选 | 组合 | 统计 | 2x | 3x | 近一年 | 弱市一年 | 状态 |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| `p9_pb_weak` | pb_latest | reject | - | - | - | - | statistical_rejected |
| `p9_accrual_weak` | accrual | reject | - | - | - | - | statistical_rejected |
| `p9_ocf_ni_weak` | ocf_ni | reject | - | - | - | - | statistical_rejected |
| `p9_pb_accrual_weak` | pb_latest+accrual | reject | - | - | - | - | statistical_rejected |
| `p9_pb_accrual_ocf_ni_weak` | pb_latest+accrual+ocf_ni | reject | - | - | - | - | statistical_rejected |

## 稳健性

- 期限资格通过：3/3。
- 统计通过：0；组合通过：0。
- 权重邻域平台：False。
- 5x 成本只作 warning：False。

## 负知识

- `p9_accrual_weak`：marginal_ic_not_positive, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p9_ocf_ni_weak`：marginal_ic_not_positive, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p9_pb_accrual_ocf_ni_weak`：marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p9_pb_accrual_weak`：marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p9_pb_weak`：marginal_ic_q_above_0_10

## 边界

- T-1 regime 对齐；未解析的前一交易日状态会 fail-closed。
- 财务因子来自 P7 PIT 证据，组合结果只是研究准入，不是收益保证。
- 合成条件复合腿尚未注册为正式因子，因此即使通过也只会标记 `requires_registration`。
- HS300 当前成分回填历史存在幸存者偏差。
