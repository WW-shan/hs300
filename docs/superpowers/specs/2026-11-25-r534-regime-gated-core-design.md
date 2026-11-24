# R534 因子库核心市场情绪/状态门控设计

## 目标

在不改动 R533 六因子核心和已选执行参数（10 仓、最大持有 60 日）的前提下，测试 T-1 市场情绪/状态门控是否能降低近期暴露和尾部回撤，并改善最近一年收益。

## 研究类型

`project_formal`。所有收益必须来自项目 `run_with_evidence + require_formal_evidence`。

## 输入

- R533A formal result/preregistration；
- R533B gate-key reaudit；
- R526 PIT HS300 bundle；
- 项目 `regime_history`；
- 冻结的六因子评分面板。

## 时间边界

`regime_history` 当前最后一天为 2026-10-21，因此本轮确认截止 2026-10-21，不向后填充。

- 训练：2022-05-05 至 2024-12-31；
- 验证：2025-01-01 至 2025-10-13；
- 近一年：2025-10-14 至 2026-10-21；
- 全样本：2022-05-05 至 2026-10-21；
- 6 个 fresh start 与 R533 一致，但统一截止 2026-10-21。

## 冻结策略

- 六因子、方向、参数不变；
- 10 仓；
- 最大持有 60 日；
- 入场 70、退出 40；
- 单票等权、最大敞口 100%、无杠杆；
- 2× 主成本、3× 压力、5× 仅 warning；
- T+1 开盘成交。

## 门控候选

全部使用项目内置 `regime_filter`，按信号日前一交易日状态生效，不手工回填或使用当日状态：

1. `always_on`：无门控，作为对照；
2. `exclude_weak`：允许 `strong/lean_strong/range/lean_weak`；
3. `exclude_weak_pair`：允许 `strong/lean_strong/range`；
4. `score_min_20`：T-1 市场 score >= 20；
5. `score_min_30`：T-1 市场 score >= 30；
6. `exclude_weak_pair_min20`：允许 `strong/lean_strong/range` 且 score >= 20。

## 训练期 MC 选择

每个规则在训练期跑正式 2× 回测，并做 5000 次 block-20 bootstrap。

资格：

- 年化收益不低于 R533 训练年化减 3pp；
- Sharpe >= 1.10；
- MDD 不深于 -15%；
- 平仓交易数 >= 80；
- bootstrap 总收益 5% 分位 > 0。

排序：

1. bootstrap MDD 5% 分位越高越好；
2. bootstrap 总收益 5% 分位越高越好；
3. Sharpe；
4. 年化；
5. 预注册规则顺序。

只看训练期，验证/近一年不参与选择。

## 确认

选中规则后运行：

- always-on 对照：全样本和近一年 2×；
- 选中规则：全样本 2×/3×/5×、验证 2×、近一年 2×/3×/5×；
- 两因子简化对照：带相同门控的全样本和近一年；
- 6 个 fresh start：选中规则 2×。

## 判定

硬门禁仍使用 `gate_policy_v2`：

- 2× 全样本、2022/2023、近一年、fresh starts；
- 相对两因子 control；
- 3× 压力；
- 集中度；
- 5× warning-only。

额外条件：

- 验证期年化 > 0；
- 选中规则近一年收益必须高于 always-on 同区间收益。

全通过：`watch_only`，仅进入纸面复核。
任一硬门禁失败：`rejected`。

## 已知研究边界

- 近一年在此前 R533 已被观察；R534 是事后条件化研究，不能宣称独立发现。
- R526 名单和行业 vintage 是研究重建，不是供应商原始快照。
- `regime_history` 是项目本地历史状态，不是实时 vintage 审计。
- 不提升 active version，不生成订单或纸面目标。
