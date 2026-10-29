# R286 轮次索引（2026-10-28）

本轮在 3018 面板登记并读回后，按预注册只读复算 2021-01-04 至 2026-10-14 的 1,398 个历史成分交易日。审计使用 R78 日成分、系统 enriched 收盘价派生的 MA60、历史 `alt_hs300` 前十大流通股东列及三项宏观输入；没有读取未来收益、交易记录、净值或回测回包。

对 415,800 条 `ma60`、股东值和宏观值均非空的预门槛成分行，ERP≥5.5、信用利差≥-0.8、期限利差≥0.3 三项同时通过 244,925 行（58.90%）。按市场日期统计，三项门槛分别通过 1,058/1,398（75.68%）、1,313/1,398（93.92%）和 1,203/1,398（86.05%）日。保持另外两项通过时，单独移除 ERP、信用利差、期限利差分别会增加 87,509、12,503、58,166 条预门槛特征行。

结果能说明这些门槛在该历史数据里的活跃度与重叠，不能说明放进来的被排除股票是否盈利，也没有验证完整基础筛选、排序和真实持仓选择后的差异。因此不能据此放宽门禁，也不是 alpha 证据。宏观源缺少历史供应商发布时间和修订 vintage，时点数据偏差仍未排除；候选保持 `research_only`，生产保持 `pause_production`。

初版 `r286_gate_exposure_audit.json` 的行级掩码存在实现错误：将三个门槛全部通过后的行当作预门槛队列，使三个单项增量排除数在定义上必然为零。该初版与原结果文件原样保留用于审计。本轮已按原预注册使用未筛门槛队列生成更正版，且所有来源哈希与计数恒等式通过。

3018 候选 ID `c651d84a828b451ba59af7ea3f3bd8d7` 当前为 `validated`；该面板状态仅表示诊断流程完成，不代表 alpha 验证或生产审批。

## 证据与 SHA-256

- `r286_preregistration.json`: `3c94e091ebdd6c5806cacf1498ca253fd219989e9d743a5fefdd5c61cb861a78`
- `r286_panel_registration.json`: `337d3e009216294e8856341aa300dd20c3d65823cbe861138d477d19799326f3`
- `r286_input_snapshot.json`: `9c12828eafe3c6478b93e2dde7a36a88bda6baf8338864a1c72a49ee21cc14d7`
- 初版审计 `r286_gate_exposure_audit.json`: `a86feb4902bfd093c9b4450eb66733862a53643d29681ead010c3e61b7f9ee33`
- 更正审计 `r286_gate_exposure_audit_v2.json`: `b49e5921b62e21f9780b90d1085e9a931c551235efe4d40eafdce66d5ec03719`
- 更正结果 `r286_result_v2.md`: `65a011c1c0f0870e5c909f2b4bc665c2d5ea0dda005251389cbffb0f6c7073ed`
- 面板完成状态读回 `r286_panel_final_readback.json`: `fcb526031b5cc95893d9eccf893e39c8583aa73984d062dca92541eb94aa3f13`
- 执行器 `r286_gate_exposure_audit_v2.py`: `dff433e842ea99bf67aad9b5b7a43c5dcf8787086e23200459b025a806a9acd2`
- 登记器 `r286_register_panel_once.py`: `41a8f89c62fa997c139bcc3a63d21302e60ec86e847a0824dc7afbae6cb687df`
