# R290 R257 当前信号系统装配与数据新鲜度审计

## 结论

截至 2026-10-27，R257 不能为当前 HS300 产生同日信号。系统日 K 和官方当前成分都已到最新日期，但 R257 复合因子及两项组成数据最新只到 2026-10-19。用系统真实的 `attach_ext_columns(..., include_snapshot=False)` 对 10-27 当日当前成分行情按 `(symbol, date)` 精确连接，300 个成分均无 R257 复合值（0/300）。这是**研究因子数据没有更新**，不是面板注册、research-only 或回测门禁挡住了计算。

R257 的历史统计曾显示较好的 IC 和收益，但单票/前三/前五净 P&L 集中度门槛分别为 27.95%/64.08%/94.43%，超过预注册上限 25%/55%/75%，因此历史候选没有通过。当前审计不读取收益或成交，也不重新检验 alpha。生产决策继续 `pause_production`。

## 日期和覆盖

| 数据 | 最新日期 | 结果 |
| --- | --- | --- |
| `kline_daily_enriched` | 2026-10-27 | 官方当前 HS300 300 只均有当日行情 |
| `alt_hs300` / `stock_rel_industry_20d` | 2026-10-19 | 10-27 没有精确日期分区 |
| `alt_hs300_minrank` / `management_ocf_gm_min_rank` | 2026-10-19 | 10-27 没有精确日期分区 |
| `alt_hs300_qrs` / R257 复合因子 | 2026-10-19 | 10-27 没有精确日期分区 |
| 系统装配后 R257 非空覆盖 | 2026-10-27 | **0/300 (0%)** |

旧的 10-19 分区也不应冒充当前信号：其复合因子全市场只有 2 个非空值；与 10-28 官方名单相交后，只有 2/300 当前成分有值。各组成项在当前成分中的非空数量为：行业相对强度 3/300、质量最弱项 249/300；由于复合因子要求两个组成项同时存在，实际仅 2/300。

## 使用的系统链路

- `StrategyDependencyResolver.resolve` 解析策略依赖。
- `BacktestEngine.load_panel_for_backtest` 根据 feature plan 的扩展列调用扩展列装配，并在历史路径设置 `include_snapshot=False`。
- `app.factors.ext_factors.attach_ext_columns` 按 `(symbol, date)` 精确左连接 timeseries；缺少日期不会以前值前向填充。
- R257 策略过滤明确要求 `ext_alt_hs300_qrs_quality_relative_strength_min_rank` 非空。

审计实际调用上述生产装配函数。为避免读取约 292 MB 的大扩展因子库，只在自动清理的临时目录中放入三张原始 config 的单字段副本和原封不动的 2026-10-19 Parquet 分区；行情、名单和源 Parquet 均只读，源分区字节 SHA-256 已记录。

## 门禁判断

- `research_only` 会拦普通策略筛选，但不是全局历史回测禁令；本轮无需运行回测即可验证因子列装配。
- 面板本轮成功从 6 条候选增加到 7 条，R290 保持 `pending`，R288/R289 也保持 `pending`；因此并不存在“面板注册后被门禁拒绝研究”的现象。
- 阻塞来自上游数据现状：R257 builder 只读取现存两项因子分区并将复合值写入扩展分区。它本身不拉取或补算基础行业相对强度、财报质量数据；直接运行会重写历史扩展分区、更新 config 并使缓存失效，因此本轮按只读约束没有运行。
- 虽然主行情每天更新，代码搜索未发现 R257 builder 挂入后端日 K 同步链的调用。当前也未找到为 R257 两个基础输入建立 10-20 之后历史分区的自动流程。要让 R257 真正可用，需要先用明确、可审计的方式更新两项组成数据，再按 PIT 规则重建复合因子，并通过当日装配与覆盖校验；不能通过前向填充或放宽收益门禁伪造可用性。

## 安全范围

本轮没有调用 provider、同步数据、运行因子 builder、读取前瞻收益/历史成交、运行回测、生成订单、创建 paper 账户或改生产状态。系统装配审计只在临时目录运行。

## 面板和可复现证据

- 预注册：`r290_preregistration.json`；SHA-256 `6197c12c5b9926fcf488d5721c3a0f8bf0ebd429b63376752a6fe98ff7a52d58`。
- 一次性面板登记意图、POST 原始响应、读回证明：`r290_panel_registration_intent.json`、`r290_panel_registration_post.json`、`r290_panel_registration.json`，都有 SHA-256 sidecar。登记 ID `2451e5a386d5489584e4121f42d97792`，HTTP POST/GET 均为 200，状态 `pending`。
- 机器审计结果：`r290_system_attachment_audit.json`；SHA-256 `c16d0aa4bbba66eb4193568b127b6f936c5190061d81d70e1393e3ab47bbaa39`。
- 可复现脚本：`r290_system_attachment_audit.py`；哈希和所有输入 SHA-256 见机器审计结果及 `r290_preregistration.json`。
- 登记脚本：`r290_register_panel_once.py`；预检查及 POST/读回细节见 `r290_panel_registration.json`。
- R257 历史结果与集中度失败明细：`r257_quality_relative_strength.json`；策略和装配源码的哈希列在预注册及机器审计 JSON。

完整的 R290 结果文件与机器结果文件各自带 `.sha256` sidecar。
