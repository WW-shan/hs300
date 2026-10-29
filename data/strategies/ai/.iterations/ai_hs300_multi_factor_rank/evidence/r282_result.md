# R282：研究门禁和安全回测路径复核（2026-10-28）

**预注册问题**：`research_only` 是否拦住冻结的 R275 候选通过系统筛选和回测，以及仓库是否已经有不污染共享研究/生产状态的历史评估路径。本轮只读审计；没有调用策略/筛选/回测执行端点，没有生成信号、收益结果或候选归档。

**面板证明**：本机 3018 面板的策略、因子、能力矩阵、数据源和回测状态五个 GET 均返回 200。面板显示 223 个策略、229 个因子、0 个策略加载错误；R275 候选已注册、`research_only=true`、`polars_expr`，控制策略也已注册，六个所需扩展/评分因子均存在。候选本机源码 SHA-256 与冻结值 `c707832d…bcdd2d8` 相同。面板 API 返回的策略元数据本身不含源码散列，因此用候选文件哈希和冻结身份/评分元数据分别核验，不把面板响应误称为源码哈希证明。

**门禁定位**：普通 `/api/screener/run_preset` 明确拒绝 `research_only`，批量 `/api/screener/run_all` 会跳过此类候选；而 `/api/backtest/strategy/run` 和 SSE 策略回测入口将策略 ID 交给 worker，worker 会加载 AI 策略目录，策略回测服务直接通过注册表 `get()` 解析，没有发现 `research_only` 检查。故 `research_only` 是筛选/生产可见路径的限制，不是全局历史回测禁令；不能笼统地说候选“没有可执行研究通道”。

**候选数据装配**：R275 是 `polars_expr`，其回测路径使用进程内 panel cache（TTL 180 秒），不是 matrix-native 的共享磁盘矩阵缓存。标准回测特征计划从所需字段解析扩展列与财务字段；`BacktestEngine.load_panel_for_backtest` 以 `include_snapshot=False` 时序对齐扩展数据，并通过 `load_fundamental_snapshot` / `attach_fundamental_factors` 按 PIT 日期接入财务数据；评分链会调用 `materialize_scoring_columns` 展开已注册自定义/复合因子。PIT helper 有公告时点约束，缺数据不应伪装为零。上述是源码调用链证据，不代表本轮执行验证或当前样本覆盖充分。

**副作用边界**：worker 在子进程内执行；该 polars 表达式候选不走 `matrix_native` 磁盘矩阵读写/裁剪分支，策略回测 API 源码也没有把结果写进 screener 策略缓存或 research candidate store。它会读取共享 Parquet、维表与用户因子注册文件，且执行会占用系统回测 worker/计算资源；因此属于“有隔离进程的只读历史计算”，不等于完全无共享资源影响。当前 `data/user_data/strategy_cache.json` 与 `research_candidates.json` 不存在；磁盘矩阵缓存下已有 1 份 manifest，是既存状态，本轮未改动。

**仍未具备有效新验证的原因**：本地 enriched 和 `alt_hs300` 数据都停在 2026-10-19；官方当前 HS300 名单是 2026-10-27。10-19 的 enriched 仅有 28 行、其中当前成分匹配 3 行。R281 已检查 10-19 另类源分区对 300 个当前成分都有候选扩展值，但这不能补足 10-20 至 10-27 的市场和扩展历史，也不能证明当时可获得性。R275 的历史表现及其反复查看过的样本不能被本轮重新包装成前瞻结果。

**结论**：`research_only` 的确挡住普通筛选，但没有挡住标准策略历史回测；因此“研究出不来是因为门禁太死”只在筛选入口这一部分成立。更准确的当前阻塞是输入数据陈旧、覆盖稀疏以及没有证据化的独立前瞻信号归档。R282 判为 `backtest_path_available_unexecuted_forward_readiness_false`；没有实际回测、IC/收益检验、阈值扫描、数据拉取、信号生成、paper 或订单。候选保持 `research_only=true`，生产状态保持 `pause_production`。没有依据修改或放宽门禁。

证据文件：`r282_preregistration.json`、`r282_panel_registration.json`、`r282_isolation_audit.py`、`r282_isolation_audit.json`，均配套 SHA-256 sidecar。完整源码片段、API 响应散列及状态元数据保存在审计 JSON 中。
