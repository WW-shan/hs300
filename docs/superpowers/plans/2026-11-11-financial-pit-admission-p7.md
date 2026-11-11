# 财务 PIT 验证与准入 P7 实施计划

> **Execution note (2026-11-11):** P7 已用当前 300 只 HS300 样本实际跑通。
> 15/15 个财务因子通过：11 个内置财务因子 + 4 个股息字段。
> Research ID 为 `sha256:4d98dbe7ecb185cbb3e9cf14538df6916b19f0b67f184fce9f7a0ec5c501a7d6`，
> JSON/Markdown SHA 分别为
> `sha256:ec8ea288167e2ba3d482cf7ba3213b95f0f1da3275ab16d892cfa2824cb3e221` 和
> `sha256:b436f5120a3f071bb5f4093f2bb85244632c545649bcdf5b0d0c4023a46f6a0e`。
> P7 同时修复了相同 PIT 生效日下多期报告选择不稳定、以及 P6 catalog delta 重置导致
> Research ID 漂移两个问题。P6 新 Research ID 为
> `sha256:5cdd753a0e3037f82fba50f3e77a056a8c6353b21cdff6feb1daf9d8fc420ce9`，
> 当前 `164/164` 个可研究因子通过准入，`blocked_task_count=0`；`active_version` 仍为 null。
> 因 catalog 变化，P5 也在新 catalog 上重跑，Research ID 为
> `sha256:641f0df5849264ea133ea0a8ccb58b057816afd47d916014f0d5baabb93cb6c7`，
> 结论仍是 `no_new_watch_version`；同时补了 P5 相对输出路径归一化。

**Goal:** 用本地真实财务表、行情表和股息事件数据生成可复现的 PIT 审计，只有逐因子检查通过后才把财务因子提升为 `verified`，并让 P6 在新 catalog 上重新计算准入。

**Architecture:** 新增一个独立的财务 PIT 审计模块，复算内置财务因子的公告日门控，并对股息字段检查每个非空信号日是否存在严格早于该日、位于 730 日窗口内的公告事件。审计产物不可变保存并绑定 ResearchLedger；taxonomy 同步器只消费审计中明确通过的 factor id。

**Tech Stack:** Python 3.11、Polars、现有 `app.backtest.fundamentals`、pytest、JSON/Markdown evidence、ResearchLedger。

## Global Constraints

- 不把 `declared` 直接改为 `verified`，必须有 P7 审计产物。
- 财务因子只在公告日严格早于信号日时生效；未来数据未知的因子不得进入组合。
- 不使用杠杆；2x 是正常基准，3x 是硬压力，5x 只能 warning。
- HS300 当前成分回填历史存在幸存者偏差，报告必须保留。
- 审计幂等；相同输入和通过集合必须生成相同 research id。
- 未通过或缺少证据的财务因子继续按 `declared` / `future_data_unknown` 阻断。

---

### Task 1: 财务 PIT 审计模块

**Files:**
- Create: `backend/app/factor_research/financial_pit.py`
- Create: `backend/tests/factor_research/test_financial_pit.py`

**Interfaces:**
- Produces: `audit_financial_pit(data_dir: Path, *, factor_ids=None, symbols=None, start=None, end=None) -> dict`
- Produces: `promote_financial_taxonomy(records: list[dict], audit: dict, evidence_ref: dict) -> list[dict]`
- `audit["verified_factor_ids"]` is the only source of truth for taxonomy promotion.

- [x] 写一个财务快照/行情样本测试，断言公告日当天不可见、下一交易日可见。
- [x] 运行测试，确认新模块不存在时失败。
- [x] 实现内置财务因子复算和股息公告窗口检查。
- [x] 回归测试通过。

### Task 2: P7 审计产物与 Ledger

**Files:**
- Create: `scripts/audit_financial_pit_p7.py`
- Create: `data/factor_research/pit/runs/<research-token>.json|md`
- Create: `data/factor_research/pit/latest.json`

- [x] 运行真实本地数据审计，写入不可变 run。
- [x] 绑定 `financial_pit_audit_published` ledger 记录。
- [x] 重跑并验证 research id、JSON/Markdown、latest 和 ledger 不变。

### Task 3: Taxonomy 与 Catalog 接线

**Files:**
- Modify: `scripts/sync_factor_taxonomy.py`
- Modify: `data/factor_research/catalog/field_taxonomy.json`
- Modify: `data/factor_research/catalog/latest.json` / `snapshots/*`

- [x] 让 taxonomy 同步器读取 P7 latest，只提升 `verified_factor_ids`。
- [x] 为通过审计的内置财务因子写入 `pit_hint=verified`、`future_data_status=clear`。
- [x] 重建 catalog，并核对未通过因子仍 blocked。

### Task 4: P6 重跑与全链路验收

**Files:**
- Modify: `data/factor_research/automation/latest.json` / `runs/*`
- Modify: `data/factor_research/ledger/*`

- [x] 重跑 P6，确认审计不再是 stale，财务阻断按新 catalog 重新计算。
- [x] 本轮 P6 结果改变；P5/P1-P4 的因子公式、窗口和参数回归未改变，保留原有研究作为输入，并在报告中记录该边界。
- [x] 聚焦测试、后端全量测试、Ruff、`git diff --check`。
- [x] 不自动激活策略、不生成纸面目标或订单。
