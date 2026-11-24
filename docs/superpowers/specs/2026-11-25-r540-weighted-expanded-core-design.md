# R540 训练期加权扩展因子核心设计

## 目标

在 R539C 已冻结的 13 因子、10 仓/60 日、行业上限 3、stop=-12% 执行上，比较四种训练期组合权重：等权、ICIR 加权、Top-8 等权、Top-5 等权。目标是判断压缩弱因子权重能否把近一年从 -0.9% 转正，同时保持全样本和 3× 压力。

## 研究类型

`project_formal`。

## 输入

- R536A 13 个独立代表及训练 IC/ICIR；
- R539C formal result/prereg；
- R526 PIT membership；
- 项目正式回测链。

## 评分

每个代表在当日 HS300 成员内做方向调整百分位 rank。四个方案：

1. `equal`：13 因子各 1/13；
2. `icir`：正训练 ICIR 归一化权重；
3. `top8_icir`：训练 ICIR 最高的 8 个等权；
4. `top5_icir`：训练 ICIR 最高的 5 个等权。

权重只用训练期确定，不使用 validation/recent。所有方案共享同一 13 因子候选集合；`equal` 是基准。

## 冻结执行

- 10 仓；
- 最大持有 60 日；
- 行业上限 3；
- stop_loss=-12%；
- 入场 70、退出 40；
- 无杠杆、总敞口 100%、T+1 开盘；
- 2× 主成本、3× 压力、5× warning-only。

## 训练期选择

每个方案在训练期跑正式 2× 回测，5000 次 block-20 bootstrap。资格：

- 年化 >= 3%；
- Sharpe >= 0.80；
- MDD 不深于 -20%；
- 平仓交易数 >= 20；
- bootstrap p05 > 0。

排序：bootstrap p05、Sharpe、年化、方案固定顺序。

## 确认

选中方案跑 full/recent 2×/3×/5×、validation 2×、6 个 fresh starts 2×；`equal` 跑 full/recent 2×作为 control。

## 判定

- validation 年化 > 0；
- 选中近期收益不低于 equal control；
- `gate_policy_v2` 全部硬门禁；
- 5× warning-only。

通过：`watch_only`，否则 `rejected`。
