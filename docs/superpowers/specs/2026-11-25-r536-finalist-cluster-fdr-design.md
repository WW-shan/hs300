# R536 全库 Finalist 独立簇与 FDR 复核设计

## 目标

R535A 产生 22 个统计 finalist，但 22 个相关因子共用一次 BH-FDR，所有 q 都大于 0.10。R536 只在训练期做相关性聚类，挑选每个独立暴露的代表，再在独立簇层重做 BH-FDR。

不重新选择参数，不修改 R535A 结果。

## 研究类型

`statistical_screen`。

## 输入

- R535A result/preregistration 及 sidecar；
- R526 daily HS300 membership 与 industry history；
- R530 factor evaluator；
- R531 训练期月末相关性/union-find helper。

## 聚类

- 只使用 22 个 R535A finalist 的训练期月末截面；
- 对每对因子逐日期计算横截面 Spearman 相关，再取均值；
- `abs(rho) >= 0.70` 合并为同一暴露；
- 代表因子由 `train_icir` 最高者担任；
- train IC、train block20 p05、距离当前参数依次作为确定性 tie-break；
- validation/recent 不参与聚类或代表选择。

## FDR 与复核

22 个 finalist 的 validation p 值不再跨 22 个高度相关测试统一 FDR。改为：

- 每个独立簇只取代表；
- 对 representatives 的 validation p 值做 BH-FDR；
- q <= 0.10。

其它门槛沿用 R535A：

- coverage >= 0.90；
- train IC >= 0.01；
- train block20 95% CI 下限 > 0；
- full IC >= 0.01；
- validation IC > 0；
- recent IC > 0；
- 训练 plateau；
- block10/40 mean positive 仅敏感性。

## 结论

- 至少一个 representative 全通过：`admitted_for_research`；
- 无 representative 通过：`rejected`；
- 相关性/输入不完整：`inconclusive`。

## 产物

- `r536_finalist_cluster_fdr_preregistration.json` + sidecar；
- `r536_finalist_cluster_fdr_result.json` + sidecar；
- `r536_finalist_cluster_fdr_result.md` + sidecar；
- ResearchLedger、资产台账和审计更新。

## 边界

- 聚类阈值 0.70 在验证结果前固定；
- 只产生研究因子代表，不产生组合收益；
- 不覆盖 R535A 的原始 FDR 结论；
- 不提升 active version。
