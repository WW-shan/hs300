# R536A Non-Finite Serialization Amendment

## 背景

R536 的聚类和 FDR 计算已完成（22 finalists、13 clusters），但在 ResearchLedger 计算 canonical hash 时失败：

```text
ValueError: Out of range float values are not JSON compliant: nan
```

未生成正式 R536 result。

## 根因

R531 的训练期 pairwise correlation helper 在部分因子对的日期截面全部为缺失时返回 `NaN`。R536 将原始 correlation pair 直接放进严格 JSON payload，而 ledger 明确拒绝 NaN/Infinity。

## 修正

新增递归 `sanitize_finite()`：

- dict/list 递归处理；
- 非有限 float（NaN/Infinity）转换为 `null`；
- 其余数值不变。

这样保留“缺失/未知”的语义，不伪造 0 相关性，也不改变聚类阈值或代表选择。

## 不变项

- 22 个 finalist、训练期相关矩阵、0.70 聚类阈值不变；
- cluster-level BH-FDR、gate policy 和代表规则不变；
- R536 原预注册与失败记录保留；
- R536A 使用新预注册和结果文件名。
