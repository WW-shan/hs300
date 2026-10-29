# R294 派生因子隔离发布与缓存一致性安全验证（2026-10-29）

## 研究问题与边界

R293 已找到刷新调用链缺口：enriched 会更新而 alt_hs300 → R253 → R257 不会自动跟进。本轮研究若要安全补链，至少需要回答：新派生数据能否先在隔离目录构建、如何校验后发布、更新中断能否恢复、在线 reader 的缓存会不会仍返回旧快照。

本轮只对 data/.staging/ 下的合成文本目录做 Windows 同卷目录切换与进程内异常回滚探针，并复核源码缓存/视图失效链。没有读取因子值或收益，没有运行任何 builder、provider、回测、筛选、撮合或 panel 回测，也没有写/删/移动正式 data/ext_data、K 线、enriched、缓存或策略数据。它不是生产数据发布测试。

## 探针结果

probe_r294_directory_publication.py 在 data/.staging 创建两组临时树。实际 os.stat 显示 staging 与 data 在同一卷。成功场景依次把旧目录移到备份名、把 staged 目录移到 live 名；live 与 staged 的 SHA-256 清单一致，旧目录备份仍完整。第二场景在旧目录移到 backup 后注入异常，回滚逻辑把 backup 移回 live；验证旧 live 树逐文件哈希完整，staged 树仍在且完整。TemporaryDirectory 正常清理了合成探针目录。机器结果 overall_pass=true，SHA-256 07b665ef7584c200d6622d9804f581fe617e06f3afe077855f1497e159dbf83d。

该结果仅表明本机 NTFS/当前 Python 条件下，单个目录在正常进程执行及捕获异常时可用同卷 rename/rollback 的形态实现可恢复切换。没有模拟 kill -9、进程崩溃、系统掉电、杀毒/读锁竞争、磁盘写满或目录替换与并发 reader，因此不构成 crash durability 证明。更不能把连续替换 3 个目录称作一个原子事务。

## 实际调用链与缓存一致性

- 基础 builder 在所有计算结束后，_write_partitions() 仍先 shutil.rmtree(data/ext_data/alt_hs300/timeseries) 再逐日写文件；不能在线直接执行。R253/R257 是逐文件 atomic_write_parquet，但旧输入日期不在新输入时不会删除多余分区。
- 三个 builder 随后执行 ext_factors.invalidate_ext_caches(DATA) 与 ensure_synced(DATA)。源码确认失效会清 extension frame cache 与注册同步状态，并清策略结果缓存；它不调用正在运行的 KlineRepository.clear_cache()，也不调用扩展 API 的 _refresh_views()。直接跑 builder 时，进程内 enriched 帧因此可能保持旧数据；DuckDB 视图是否需要刷新取决于使用场景，API _refresh_views() 当前还承担 repository cache clear。
- 对照数据路径，attach_ext_columns() 的 extension timeseries frame 通过分区 mtime/size signature 检测磁盘变化，可在其下一次读取时重载扩展文件。这不是一致性发布协议：多树切换期间读者仍可能观察混合代际；在调用失效前也可能命中进程缓存。策略结果缓存清理只影响磁盘策略结果，不清 repository 内存帧。
- 常规扩展 API 写入会调用扩展缓存失效，API 路径再更新 DuckDB 视图并清 repository cache；盘后管道也有自己的 view/cache refresh。三层派生 builder 属于脚本，没接入这些在线调用链。

因此需要把发布和 cache/view refresh 放在同一个受控服务/协调点中：先阻止同一数据根上的并发构建/发布，校验完整 staged generation；发布成功后统一使扩展帧缓存、因子 registry sync、策略结果缓存、repository 内存缓存和相关 DuckDB view 进入同一新 generation。若发布后 cache/view 刷新失败，必须有可观测状态并能重试/恢复，不能只记录 warning 后宣称一致。现有代码没有跨 3 个因子树的 generation 指针或事务接口。

## 建议的安全构建/发布约束

这轮不实现业务代码；从当前脚本结构看，下一步不能直接把 DATA 临时改成 scratch 后就称为安全，因为 base builder 以 Path(__file__).resolve().parents[1] / data 固定路径，且多处加载真实 research、financial、industry 和 K 线文件。最小可维护改造应把只读输入根与输出根显式分开，并将纯计算与发布副作用拆离；否则通过复制整个 data/ 目录会产生很大体积，且难保证冻结/一致输入。

正式发布前至少 fail-closed 验证：

1. 冻结一次输入 generation：enriched 所有有效交易日、所需 research/financial/industry 文件及配置的 SHA-256/size/mtime；构建期间若上游变化则整批作废重跑，不接受混代输入。
2. 日期集精确对账：基础/R253/R257 的分区日期应与该 builder 契约的基础输入日期集合一致；确认 date= 目录名、Parquet 行内日期和范围均一致，拒绝空帧、缺日、多余旧日和重复 (symbol,date)。
3. schema/键/范围检查：每层字段集合及 dtype 符合配置；symbol 非空；R253/R257 行键唯一；rank 因子在预期 [0,1] 范围；每个日期有效覆盖达到冻结阈值，缺输入不能静默填零伪装有效。
4. 依赖覆盖检查：R253 所需三项基础字段同时可用；R257 的 R253 与基础行业强度按完全相同 (symbol,date) 键对账；保存逐日总行数、非空行数、有限值数、输入/输出 hash 和可重算摘要。日期/覆盖异常均禁止发布。
5. staged generation 先完成再切换：每一层写入独立 staging 子目录并逐文件校验；旧版本留作只读备份。单目录 rename 能形成单棵树切换，但三层不是单一原子事务。可选做法是让读者通过单个原子 current_generation manifest/指针解析三层路径，最终只原子切换该指针；如果继续使用固定物理路径，则需要持有全局读写锁并采用有 journal 的三树替换、启动恢复和逐步回滚协议，保证 readers 不在混代窗口读取。
6. 在写盘量、耗时、峰值内存和失败恢复经隔离验证前，不接到自动盘后任务；优先提供明确的人工 rebuild/预检入口和状态证明。增量构建只能后续另证：滚动窗口字段需从受影响交易日前回看至少最大窗口，横截面 rank 变化可能影响同日全池，财务修订还可能影响历史可见日期。

R294 没有证明以上门槛已实现，也没有测算全历史 rebuild 成本；这是候选设计约束，而不是可直接部署方案。

## 面板与冻结证据

R294 已在本机 3018 面板单次 POST 并 GET 读回，HTTP 200/200，候选数量 10→11，状态 pending，ID 4df004f0146b4e818f1f8ac16b31ebde；预检确认 R288–R293 均为 pending 且无同名同区间重复候选。POST 未重试。面板登记与读回证明 SHA-256：7c99c64aa8036f3e9ce467905ce2b3e475e60c44acc7d027af48e8821374689b。

预注册 SHA-256 3eb43c621aa8473a6f2b59c51ab4ec11b821b4a70c62ba853d9889890efe8c5c；输入/本地状态快照 SHA-256 350c6441f042aa8ead29ccd8f95beac8b5051d782b41c726acfdfe1df4a6583c；隔离 rename/rollback 机器结果 SHA-256 07b665ef7584c200d6622d9804f581fe617e06f3afe077855f1497e159dbf83d。每份 JSON、脚本、报告、搜索结果都有旁置 SHA-256 sidecar。R293 补充范围记录也保留为单独证据，没有覆盖 R293 原冻结文件。证据索引见 r294_round_index.md。

R257 继续保持 research-only，生产保持 pause_production。本轮没有启用任何门禁、策略或 provider。
## 冻结方法执行更正

预注册的输入快照方法写了“每层哈希两份已有分区”，但原始 selector 实际仅匹配 `2026-10-19` 和 `2026-10-27`，三层因子树的末日均为 `2026-10-19`，所以原快照每层只有末日一份。原冻结文件保持不变；事后补充的 `r294_partition_inventory_erratum.json` 为三层分别记录最早与最晚分区，共六个文件哈希。该补充在预注册/探针之后，属于透明更正，不算事前冻结。它只核对文件身份，不读取 parquet 值，不改变本轮发布探针结论。补充记录 SHA-256 `3752e31273433d5dd24a7fa0c32ccfb7bd748c7760cf2dd15feb457574440d77`。

