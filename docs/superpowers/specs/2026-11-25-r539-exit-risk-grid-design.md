# R539 扩展组合退出风控网格设计

## 目标

R538C 已通过 Sharpe、MDD、行业集中度和 3× 压力，但近一年仍为负。R539 冻结 13 个独立代表、10 仓、60 日持有、行业上限 3，只优化退出风控，确认更激进的止损/移动止损是否改善近一年。

## 研究类型

`project_formal`。

## 输入

- R538C formal result/preregistration；
- R536A 13 个独立代表；
- R526 PIT membership；
- 项目回测引擎。

## 冻结执行

- 13 因子等权 rank；
- 10 仓；
- 最大持有 60 日；
- 行业上限 3；
- 入场 70、退出 40；
- 2× 主成本、3× 压力、5× warning-only；
- 无杠杆、最大敞口 100%、T+1 开盘。

## 退出候选

1. stop_loss=-5%，无 trailing；
2. stop_loss=-8%，无 trailing（策略默认控制）；
3. stop_loss=-12%，无 trailing；
4. stop_loss=-15%，无 trailing；
5. 无止损、无 trailing；
6. stop_loss=-8%，trailing_stop=-10%；
7. stop_loss=-12%，trailing_stop=-10%。

## 训练期选择

训练 2022-05-05 至 2024-12-31，每候选 5000 次 block-20 bootstrap。

资格：

- 年化 >= 3%；
- Sharpe >= 0.80；
- MDD 不深于 -20%；
- 平仓交易数 >= 20；
- bootstrap p05 > 0。

排序：bootstrap p05、Sharpe、年化、候选固定顺序。

validation/recent 不参与选择。

## 确认

选中候选与默认 stop=-8% 控制分别跑 full/recent 2×；选中候选额外跑 full/recent 3×/5×、validation 2× 和 6 个 fresh starts 2×。

## 判定

- 验证期年化 > 0；
- 选中规则近一年收益必须高于默认 stop=-8%；
- `gate_policy_v2` 全部硬门禁；
- 5× warning-only。

通过：`watch_only`；失败：`rejected`。

## 边界

- 因子和执行主参数不变；
- 近一年已多次观察，R539 是事后退出优化；
- 不生成订单、纸面目标或 active version。
