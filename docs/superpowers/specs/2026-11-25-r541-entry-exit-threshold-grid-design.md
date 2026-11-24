# R541 扩展组合入场/退出阈值网格设计

## 目标

R539C 在 13 因子、10 仓/60 日、行业上限 3、stop=-12% 下只剩近一年 -0.91% 未过。R541 冻结其余所有参数，只优化入场 score 阈值和退出 score 阈值，训练期用 5000 次 block bootstrap 选择。

## 输入

- R539C formal result/preregistration；
- R536A 13 个独立代表；
- R526 PIT membership；
- 项目正式回测引擎。

## 网格

入场：60、70、80；退出：30、40、50；9 个固定组合。

固定：13 因子等权、10 仓、60 日持有、行业上限 3、stop=-12%、无 trailing、无杠杆、T+1 开盘、2× 主成本、3× 压力、5× warning-only。

## 训练选择

2022-05-05 至 2024-12-31；每组合 5000 次 block-20 bootstrap。

资格：年化>=3%、Sharpe>=0.80、MDD>=-20%、交易数>=20、bootstrap p05>0。

排序：p05、Sharpe、年化、固定组合顺序。validation/recent 不参与。

## 确认

选中组合跑 full/recent 2×/3×/5×、validation 2×、6 个 fresh starts 2×；默认 entry70/exit40 作为 control，跑 full/recent 2×。

## 判定

验证期年化>0；选中近一年收益高于 default 70/40；gate_policy_v2 全部硬门禁；5× warning-only。通过 watch_only，否则 rejected。
