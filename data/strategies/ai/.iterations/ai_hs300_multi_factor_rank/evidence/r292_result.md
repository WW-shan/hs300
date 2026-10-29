# R292 隔离重建与系统装配验证（2026-10-29）

## 问题

R291 发现 alt_hs300 的 2026-10-19 行业因子分区只有 28/557 个非空值。R292 验证只在临时目录用当前 enriched 行情重算行业因子，能否经生产 R257 合成函数和系统扩展因子装配函数恢复覆盖。

## 结果

生产 _industry_daily 对冻结的 557 只股票产生 546 行，行业相对强度非空 546 个。把该日重算结果与原有 alt_hs300_minrank 质量输入放入隔离目录后，生产 R257 合成函数得到 249 个非空值；系统生产 attach_ext_columns(..., include_snapshot=False) 对目标日 enriched 行装配出同样 249 个非空股票。249 个非空值逐项完全一致。

冻结的原 alt_hs300 目标分区仍只有 28 个行业相对强度值；R291 所记录的旧 R257 复合分区只有 2 个。R292 没有重现旧数值，也没有运行策略、回测或撮合。它证明的是该日期的覆盖可通过生产计算链恢复，并可由系统准确装配。

## 面板与边界

R292 候选 ID cd6f5bc7773b44c9bedb69430f363220 已向本机 3018 面板单次 POST 并读回，POST/GET 均 HTTP 200，候选数 8→9，状态 pending。R288、R289、R290、R291 均仍为 pending。没有重试 POST。

重建输出和临时扩展配置仅写入自动删除的临时目录。冻结的 2,154 个输入文件在执行前后均核对 SHA-256、大小与修改时间，没有变化。未调用 provider 或 builder 的 main()，未写正式行情/扩展因子、缓存或策略，未读取收益或成交，未运行回测或撮合。

本结果仅说明 2026-10-19 的因子计算覆盖和系统装配可以恢复。由于使用的是刷新后的行情，不能将重算值解释为旧历史值或当时可获得的 PIT 信号，也不构成 alpha、策略收益或生产有效性证明。R257 继续保持研究状态，生产继续 pause_production。

## 证据

- 预注册 SHA-256：1133f16932db6b27f58f8e7dd93068ba929999906dac62213327e205ad516203。
- 输入清单包含 2,154 个文件，其中 2,138 个 enriched 行情分区；SHA-256：6cf5e261461f65232de650e922ba9a1443cae81bf94255a37a2ba4f2f7d8dc62。
- 机器结果：r292_isolated_attachment_audit.json；面板证明：r292_panel_registration.json。
- 脚本：audit_r292_isolated_r257_attachment.py、freeze_r292_preregistration.py、register_r292_panel_once.py。结果、报告、输入清单、预注册、脚本和面板请求/读回证明均配有 SHA-256 sidecar。
