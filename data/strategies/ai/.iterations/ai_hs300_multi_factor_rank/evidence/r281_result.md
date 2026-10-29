# R281：扩展因子可在运行帧装配，财务 PIT 普通策略缺桥接；当前输入仍不可执行（2026-10-28）

本轮按 R281 预注册做了只读面板、代码链路与本地输入核验；没有运行候选或筛选器，没有生成/归档信号，没有同步、回测、实盘或 paper 操作。预注册 SHA-256 `197fdf354f03352d65833aa3412fcadb47cbb1ea0c9a40e3d5546b637be68112`。

**面板证明**：3018 四个只读 API 均返回 200。当前 223 个策略、229 个因子、0 个策略加载错误；R275 候选注册且 `research_only=true`，冻结源 SHA 与 `c707832d…bcdd2d8` 一致；所需扩展/自定义因子 ID 均已注册。面板证明 SHA-256 `e47cda241816352c2ee552e30716807ac4dc75d1ffc0a4d61f172c31bec7654d`。

**对 R280 的重要更正——扩展因子**：R280 用 enriched Parquet 物理列判断运行时上下文是否含扩展因子，推论错误。Parquet 有意只保存窄行情列；正常启动预热通过 `compute_enriched_history_window -> compute_signals -> attach_ext_columns(include_snapshot=False)`，按 `(symbol,date)` 把扩展时序列加到运行帧，仓库把该完整帧放进内存 current/history cache。策略冷读慢路径也会调用 `compute_signals`。因此不能说系统“没有扩展因子装配路径”。

**但装配并非所有分支都可靠**：仓库预热失败会降级缓存窄 Parquet 帧；最新日和历史缓存命中分支可直接返回缓存，不会重新 join 扩展列。冷历史重算有 join，缓存路径则取决于缓存如何构建。扩展配置/数据读取失败时 `attach_ext_columns` 是逐配置告警并跳过；策略 `REQUIRED_FEATURES` 的通用验证只检查资产类型和周期、不检查必需列；候选过滤直接引用扩展列，缺列会报错，而评分解析无法生成因子值时可能把该评分腿跳过。因此这个路径需要显式缺列审计和失败保护，但并不等于日常预热成功时字段不存在。

**另类数据日期与覆盖**：`kline_daily_enriched` 和 `alt_hs300` 最新分区同为 2026-10-19，扩展分区上的三项候选字段对 300 个当前成分都有非空值；但当前名单为 2026-10-27，且扩展 pull 未配置。更关键的是 10-19 enriched 只有 28 行，其中仅 3 行命中当前 HS300 名单，远不足以代表 300 股截面；因此当前数据既过时又严重不完整。扩展运行时 join 可以解决物理列不在 Parquet 的结构问题，不能补齐缺失的行情行或消除日期错配。

**财务 PIT 数据与普通策略桥接**：本地 `metrics`、`income`、`balance_sheet`、`cash_flow`、`shares` Parquet 均存在；四张报表/指标表的报告期最新至 2026-06-30，原始公告日期最大在 2026-10-14/15。只读调用系统 `load_fundamental_snapshot` 和 `attach_fundamental_factors`（仅在本地 10-19 行情与当前成分的 3 行交集上）可得到五个 PIT 列，五列各 3/3 非空；这验证了现有回测 PIT helper 和数据在这三个标的上的可读性，不代表 R275 全池已经完整。普通 `ScreenerService.build_strategy_context` 与 `StrategyEngine` 评分链没有调用这两个 helper，二者只在策略/因子回测和 mining runtime 路径使用。故 `roe_latest/pb_latest/debt_ratio_latest/ocf_ni/accrual` 在普通策略上下文依然没有已实现的 PIT 注入路径。

**安全边界与判定**：普通 `/api/strategy/run` 与 screener 预设拒绝 research-only；批量路径会跳过研究候选，日线执行会写共享 `strategy_cache.json`。未证明既能运行研究草稿又隔离共享缓存、实时/paper hooks 并留下独立归档的系统路径。本轮判定 `not_ready_no_signal_generated`、`all_pass=false`；候选继续 research-only，生产保持 `pause_production`。没有证据支持放宽门禁；目前主要问题是本地输入未更新/不完整、普通策略缺财务 PIT 接线，以及执行隔离和证据归档方案未建立。

**证据与哈希**：`r281_preregistration.json`、`r281_panel_registration.json`、`r281_runtime_assembly_audit.json`、`r281_runtime_assembly_audit.py` 均留在同目录。审计 SHA-256 `ddac0b640d50b0ae1a25ded9c188a807c0caea191434f3b9467b7b4dd5cabb60`；审计 runner SHA-256 `8b1b66a3696be4f486a0241e201111737889bbb6e26e7f29bc37627efa8f80da`。R281 前后检查的 `strategy_cache.json`、`factor_cache.json` 和 `fundamental_cache.json` 均不存在且状态一致。所有 JSON/Markdown 证据均有 SHA-256 sidecar。审计脚本只 GET 面板并读取源码/本地文件；财务 PIT 探测调用既有纯数据函数，不触发策略或缓存写入。

## R280 证据更正

保留 R280 原始与更正审计文件不变。R280 的扩展字段“没有进入标准运行上下文”结论过强，应以 R281 代码链路结论替代：正常预热和冷历史重算均存在扩展列装配；Parquet 物理 schema 不能代表运行帧 schema。R280 的其余未就绪理由仍成立：本地日期与成分名单不一致、全市场行情覆盖严重不足、普通策略没有财务 PIT 装配、research-only 候选没有被证明可隔离执行及归档。R280 原 corrected audit SHA `e0cb26d26364744fd974c3fb72fa5af9ce5784e2053020208e43cca51fd20280`，R280 结果 SHA `61844297fe397d39076efbdc05093bdcc223b58f107614a4ef6f36a184bf2752`；新解释仅追加于 R281，不改写旧证据。
