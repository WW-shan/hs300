# R292 研究轮次证据索引

## 结果

- 2026-10-19 冻结 universe：557 只股票；生产行业计算覆盖 546。
- 旧 alt_hs300 行业强度覆盖：28/557。
- 隔离重建 R257 覆盖：249/557。
- 系统 attach_ext_columns(include_snapshot=False) 覆盖：249 只；249/249 个非空值与隔离复合分区一致。
- 面板候选 ID：cd6f5bc7773b44c9bedb69430f363220；POST/GET HTTP 200；候选数 8→9；状态 pending。
- 2,154 个冻结输入在运行前后均通过 SHA-256、文件大小和 mtime 校验。
- 所有重建写入临时目录；没有正式行情、扩展因子、策略或缓存写入；没有回测、成交、收益或 provider 调用。

## 核心 SHA-256

- r292_preregistration.json：1133f16932db6b27f58f8e7dd93068ba929999906dac62213327e205ad516203
- r292_input_manifest.jsonl：6cf5e261461f65232de650e922ba9a1443cae81bf94255a37a2ba4f2f7d8dc62
- r292_isolated_attachment_audit.json：见同名 .sha256 sidecar
- r292_result.md：见同名 .sha256 sidecar
- 面板登记、意图及 POST 证明：分别见 r292_panel_registration.json、r292_panel_registration_intent.json、r292_panel_registration_post.json 和对应 sidecar。
- 冻结器、面板登记器和审计脚本均有 .sha256 sidecar。

## 解读

这验证了指定历史日的计算覆盖和生产装配路径可以在隔离环境恢复，未验证旧数值复现、PIT、收益、alpha 或生产有效性。R257 继续保持研究状态，生产保持 pause_production。
