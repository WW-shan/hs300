# R535A Stats Import Amendment

## 失败

R535 已完成 85 个家族、472 个参数点的内存扫描，但在 BH-FDR 汇总时报：

```text
AttributeError: research_r530_unified_factor_library_mc has no attribute stats_v2
```

没有发布 result/Markdown，也没有根据扫描结果改参数或门槛。

## 根因

R535 调用 `r530.stats_v2.bh_fdr_qvalues`，但 `stats_v2` 在 R530 中是直接导入的局部符号，不能按该属性路径访问。

## 修正

R535A 直接：

```python
from app.backtest import stats_v2
```

并使用 `stats_v2.bh_fdr_qvalues`。

## 不变项

- 85 个家族和 472 个参数点不变；
- 训练期选择规则、1000/5000 次 bootstrap、固定 seed、验证/recent/FDR 门槛全部不变；
- R535 原预注册和失败记录保留。
