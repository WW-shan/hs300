# R280 结果（2026-10-28）— 盘后快照链路未就绪，未生成信号

**预注册与面板**：R280 预注册 SHA-256 `b607f04b6813aff0c681be60a7ae411c172a18d11e52d5d55eaa7bbf63d22a3a`。只读重新核对 3018 面板，无 reload；223 个策略、229 个因子、0 个策略加载错误。R266 对照和冻结 R275 期限候选均已注册；候选仍为 `research_only=true`，源码哈希与冻结值一致。四项计分因子都在因子 API 中。面板证明 SHA-256 `72fe4007dc1dc110901bf31e0bfd8cb6bae32d6851962359951f4f7a1615ebfb`。

**日期与输入覆盖**：本地 enriched 日线和 `alt_hs300` 另类因子分区最新日期均为 2026-10-19，但官方当前 HS300 成分缓存 `as_of=2026-10-27`，R279 通过系统注册 AkShare provider 读取的样本日线也到最近完成交易日 2026-10-27。故同日行情/另类因子不能和当前名单对齐。更正后的物理列映射确认三项策略所需另类字段（前十流通股东持股、ERP、信用利差）均存在于 10-19 另类因子分区，且配置已注册；它们不在 enriched 分区。策略标准上下文由 `ScreenerService.build_strategy_context` 读取 enriched current/history，未发现把另类分区或 `ext_columns` 自动注入策略帧的路径。

**策略与财务装配审计**：`StrategyEngine.run` 会按评分要求调用 `materialize_scoring_columns`，但这一步只对已有帧执行 DSL/虚拟因子计算；R275 的 `required_features` 扩展列也没有被普通日线引擎作 fail-closed 校验，`filter` 直接引用缺失列会在运行时失败。`uf_quality_value_defensive` 要求 `roe_latest`、`pb_latest`、`debt_ratio_latest`，`uf_cash_quality` 要求 `ocf_ni`、`accrual`；这些 PIT 输入均不在当前 enriched schema。虽然因子注册表标了财务 PIT 属性，标准筛选上下文没有接入财务 PIT 装配；单独回测模块 `backend/app/backtest/fundamentals.py` 有 PIT 处理，不等于普通策略路径会自动使用它。仅注册因子不代表这些评分已执行。

**门禁和副作用**：公共 `/api/screener/run_preset` 明确拒绝 research-only 候选；`run_all` 会跳过候选，正常日线执行还会写共享 `strategy_cache.json`。底层引擎虽然能运行研究策略，但本轮没有发现已验证的隔离调用和归档路径。为保持安全边界，未调用策略/筛选运行、未生成信号、未回测、未写共享缓存、市场分区或扩展数据，也未启动轮询、监控或 paper hook。

**判定**：`panel_registration_proof_pass=true`；本地日线与另类分区日期一致，但未与当前成分/最近 provider 交易日对齐；另类字段未进入标准策略上下文；财务 PIT 列缺失；隔离执行/归档路径未证明。R280 `all_pass=false`，结论 `not_ready_no_signal_generated`。本轮不回答收益或 alpha，不放宽研究门禁，候选保持 research-only，生产维持 `pause_production`。

初版只读审计因把 `ext_alt_hs300_*` 因子 ID 误当成另类分区物理列名，留下了 `r280_snapshot_feasibility_audit.json`（SHA-256 `9e70a8f360da22f5f14b5d5fada1d5f0196a1a5dde1c8cdc095e385edc6c4f5a`）。未覆盖原文件；按预注册的追加更正流程，以独立更正预注册和只读确定性映射生成 `r280_snapshot_feasibility_audit_corrected.json`（SHA-256 `e0cb26d26364744fd974c3fb72fa5af9ce5784e2053020208e43cca51fd20280`）。更正只改变另类字段是否存在于源分区的细项；仍判定不可运行。

证据：`evidence/r280_preregistration.json`、`evidence/r280_panel_registration.json`、`evidence/r280_snapshot_feasibility_audit.json`、`evidence/r280_audit_correction_preregistration.json`、`evidence/r280_snapshot_feasibility_audit_corrected.json`，对应 `.sha256` sidecar 均已保存。
