# R293 派生因子刷新调用链审计（2026-10-29）

## 结论

**没有发现系统日线/enriched 刷新入口自动运行 R257 的派生 builder 链。** 系统会在多条应用路径中更新日线、enriched 或某个扩展时序分区，但 alt_hs300 → alt_hs300_minrank (R253) → alt_hs300_qrs (R257) 的 build 脚本没有被这些路径调用。R257 的过期问题不是回测门禁造成的；这轮是源代码调用图审计，不涉及收益验证。

另有通用扩展数据定时拉取器。它只在某个 ExtConfig 配有启用的 pull 时请求并合并写该配置的一天分区，不能执行 Python 派生计算。本地 alt_hs300、alt_hs300_minrank 和 alt_hs300_qrs 的配置均未设置 pull。它不能替代 R257 builder，也没有解决当前三层派生依赖的自动刷新。

## 刷新入口矩阵

| 入口 | 日线持久化 | enriched 重算 | 扩展拉取/派生 | 缓存与视图 |
| --- | --- | --- | --- | --- |
| 工作日 daily_pipeline 定时任务及 /api/pipeline/run | 完整盘后管道按日期/完整性条件同步 | 新日期/修复时增量或全量；有除权变化时重算受影响股票；没有变化时跳过 | 不调用任何 R257 链 builder | 刷新 DuckDB 视图、data API 标记及仓库缓存；repository refresh 完成后还可能异步预热回测矩阵缓存，但不生成因子 |
| /api/kline/repair_daily | 调用相同 run_now(override_start_date=...) 全流程 | 与盘后管道相同 | 不调用派生 builder | 最后清理并刷新仓库缓存 |
| /api/kline/sync、/sync_batch | 写日 K 并刷新 kline_daily 视图 | **不运行 enriched** | 不调用派生 builder | 不刷新 repository 的 enriched 内存缓存或 data API 缓存；调用者不能把它视作完整派生刷新 |
| /api/kline/rebuild_enriched | 不拉行情 | 全量执行 run_pipeline() | 指标计算会临时装配已有时序扩展列；持久化 enriched 仍是窄表，不会计算或写入扩展值 | 更新 enriched 视图并失效 storage API 缓存；不是扩展 builder |
| /api/kline/extend_history | 向前拉日线/除权 | 全量执行 run_pipeline() | 不调用派生 builder | 刷视图、失效数据缓存并清理/刷新 repository 缓存 |
| 手动财务同步中股本表更新 | 写财务股本表，不写日线 | 对受影响股票调用 run_pipeline(symbols=...) | 只重算 enriched；不触发 R253/R257 | 重建视图并后台刷新 repository cache |
| 通用扩展 pull scheduler /api/ext-data/{id}/pull/run | 不写 K 线 | 不重建 enriched | 只对指定配置请求、合并并原子替换对应分区；无派生链联动 | 自动失效 ext factor frame 和 registry sync 状态；定时任务保留 strategy result cache，手动拉取默认清 strategy cache |

上述是代码路径行为，不表示本轮实际执行了这些任务。

## Builder 输入、覆盖和副作用

- **基础 alt_hs300**：_calendar() 使用现有 enriched 日期，以及 R87/R163 holder 研究缓存的 symbol 并集；本地审计到 2,138 个 enriched 日期、2,132 个基础扩展日期、459 个 holder symbol 文件和 8 个 R163 holder 批次文件。行业强度 _industry_daily() 再读这些 symbol 的历史 enriched 收盘价并使用行业映射。不是接收日线写入事件后按受影响日期/股票增量更新的 builder。
- **基础输出写入风险**：build_alt_factor_data.py::_write_partitions() 在计算结果全部准备完成后，先递归删除 data/ext_data/alt_hs300/timeseries，再逐日期原子写文件。atomic_write_parquet 仅保证单文件完整；整个目录没有 staging + 原子切换。中断或写盘失败可留下空/部分表，在线 reader 也可能在发布过程读到不完整快照。
- **R253 (alt_hs300_minrank)**：从已保存 alt_hs300 的管理费用率、OCF 利润率、毛利率改善三列和行业/历史成分快照重算。逐日文件原子覆盖，但不清理输出目录；重建范围是当前输入中全部日期/行，不是只算新日。
- **R257 (alt_hs300_qrs)**：从已保存 R253 因子和基础行业相对强度、并复用 R253 的历史非金融成分逻辑重算全部输入日期；逐日原子覆盖且不清除目录。它依赖前两层已先正确更新，单独运行 R257 不会修复过期组件。
- **注册与缓存**：三个 builder 更新扩展配置 (R253/R257 各自配置，基础 builder 更新 106 字段配置)，然后调用 invalidate_ext_caches(DATA) 和 ensure_synced(DATA)。这会清扩展时序帧缓存、扩展因子注册同步状态和策略结果缓存；不会代替调用者刷新 KlineRepository 的 enriched 内存缓存。在线分区签名含目录名、文件 mtime 和大小；外部逐分区覆盖可使读取侧重载该分区集合，但它只刷新已存值，不会生成 R253/R257 派生值。
- **过期日期边界**：当前本地 enriched 分区最新日为 2026-10-27，而 alt_hs300、R253、R257 三层最新分区均为 2026-10-19。基础 builder 删除旧目录后按当前 enriched 日历完整写回；R253/R257 不删除超出输入日期集合的既有输出分区。因此若输入日期范围缩短，后两层目录可能保留输入中已不存在的旧日期。

## 判断和后续含义

这次故障模式符合“日线/enriched 已更新，扩展派生分区未随之更新”的代码结构：常规行情管线维护 K 线与 enriched；研究 builder 是单独人工/脚本流程，没有依赖图、事件或调度衔接。R292 已证明在临时数据目录内计算和系统装配可恢复覆盖。R293 补充证明系统刷新入口本身不会自动维持 R257 派生链。

因此，下一步若要让覆盖持续有效，应先设计**隔离构建、依赖顺序、完整性校验和原子发布**，并确定新数据的受影响范围以及人工修复的明确入口。不能直接在线运行现有基础 builder：它会先删除正式 alt_hs300 时序目录。R253/R257 全部历史重算也有明显磁盘读写和计算成本；本轮没有运行或估算该成本。

R257 继续保持 research-only，生产保持 pause_production。本轮没有 provider 拉取、回测、筛选、撮合、paper 交易、订单、收益/成交读取、正式 K 线/enriched/扩展数据或缓存写入，也未改业务代码。

## 面板与证据

R293 已在本机 3018 面板单次注册并读回：候选 ID dbc1248434fb457f8312eadf47d459da，POST/读回均 HTTP 200，候选数 9→10，状态 pending；预检确认 R288–R292 均为 pending 且无重复候选。未重试 POST。原预注册 SHA-256 d9375edce45174178ecb6ca668c2c4db82c1c7d92d1848e5c23ac443613753d9；原输入快照 SHA-256 2a572056b215b046a0ad65dd661b2f8dbfb3cd5b482ae661871ba8baeb9cd989。原冻结文件原样保留。后续范围补充 `r293_scope_addendum.json` 又冻结了 12 个遗漏的源码/本地配置文件，包含 app startup、pull scheduler、扩展数据 API、因子缓存、repository/strategy cache 与三个配置；补充搜索仍为 128 处引用，三份配置 `pull_enabled=false`。原报告冻结时刻之后加入文件范围的事实已显式记录，故补充不是预注册前冻结。

对照补充源码后，更新报告中的缓存表述：三个 builder 清扩展因子帧缓存、注册同步状态和策略结果缓存，但不调用正在运行的 `KlineRepository.clear_cache()`，也不调用扩展 API 的 DuckDB `_refresh_views()`。扩展时序读取签名会检测分区 mtime/大小，能重读磁盘扩展帧；这不能清除 repository 中已经缓存的 enriched 帧，也不等同于切换一组三层完整一致的因子 generation。此差别需要在任何自动发布实现中显式处理。

补充源码/本地配置快照 SHA-256：`r293_scope_addendum.json` 为 375094694aedfd93771db71e8068fed73910dca9795344c65f7536e5311311d9；引用搜索 `r293_scope_addendum_search.txt` 为 be90d50846a5b3f59e90946517713dcb22bbb1c75b8ccc729b1be5de41254838。该补充审计没有重新运行、回测或触及生产因子数据。
