# 因子集与持续研究系统设计

> 状态：设计稿，待用户评审；本稿只定义目标契约，不代表代码已实现。
>
> 范围：在现有因子注册表、因子回测和策略评分之上新增研究层，分级为 L2。复用现有
> `backend/app/factors/registry.py`、`backend/app/factors/store.py`、
> `backend/app/factors/ext_factors.py`、`backend/app/backtest/factor.py` 和
> `backend/app/strategy/scoring.py`，不重写因子计算、评分引擎或回测引擎。
>
> 依据：`CONTRIBUTING.md` 第 1、2、3、6、8、9、13 节和
> `docs/secondary-development.md` 第 1、2、4、6、8 节。本文出现的“已实现”仅指
> 当前真实代码；“设计”条目不得被调用方当作现有 API。

---

## 0. 当前基线与必须先纠正的口径

### 0.1 当前因子盘点

以 2026-11-10 工作区实际注册表为基线：

| 维度 | 数量 | 说明 |
| --- | ---: | --- |
| 可调用因子总数 | 220 | `all_factors(asset_type="stock")` 的注册表快照 |
| base | 135 | 已物化指标或扩展列 |
| virtual | 52 | 评分时按依赖物化 |
| custom | 19 | DSL 自定义因子 |
| composite | 14 | 复合因子 |
| built-in | 81 | 70 个技术/价量 + 11 个 PIT 财务声明 |
| extension data | 106 | `data/ext_data/*/config.json` 的数值字段 |
| custom + composite | 33 | `data/user_data/custom_factors/*.json` |

自定义/复合因子的源定义状态为：28 个 `draft`、5 个 `retired`、0 个 `active`。
注册表元数据中 187 个 `stable`、33 个 `experimental`。

**`stable` 只表示定义稳定，不表示经过统计检验，更不表示有超额收益。** 本系统不得把
`stable` 映射成 `active`，也不得把“已被研究提到”映射成“已验证有效”。

### 0.2 研究证据缺口

当前统一五年 IC 扫描只覆盖 77 个内置因子。另有 143 个注册表因子尚未进入统一扫描：
106 个扩展因子、33 个动态因子、4 个较新的内置财务 TTM 因子。它们可以出现在母集，
但不能因为“存在定义”被写成“已验证”。

### 0.3 当前审计的错误口径

旧脚本 `scripts/audit_factor_strategy_coverage.py` 用字符串包含和“是否在注册表”判断
覆盖率，导致以下对象被混为“缺失因子”：

1. `amount`、`consecutive_limit_ups` 等真实物理/ enriched 列；
2. 评分表达式中的虚拟字段；
3. 已退役或只存在于历史研究中的因子；
4. 真正的悬空引用；
5. 字符串信号字段。

新系统必须先解析引用类型，再计算覆盖率和缺口，禁止用 id 前缀或模糊名称猜测。

### 0.4 数据新鲜度基线

当前扩展数据最新分区为 2026-11-05，共 2,143 个日分区。已知陈旧样例：

- `northbound_net_buy_20d` 最后非空值 2024-08-16；
- `cpi_yoy`、`ppi_yoy` 最后非空值 2025-09-30；
- `pmi` 最后非空值 2025-10-30。

这些字段可以保留在母集和研究池中，但陈旧状态必须进入审计结论，不能静默参与 active
因子集。

### 0.5 当前 R360 研究状态

R360 的行业相对强弱衰减退出在 R369 的 v2 门槛下进入纸面复核，但不是生产启用。
R370 证明组合层参数邻域较稳，但行业回看、四腿权重、过滤阈值、黄金均线和 regime
映射尚未全部完成蒙特卡洛/邻域验证；R371 证明 `3/5/10 × 10/20/30 × -15%/-20%/-25%`
的三维平台存在，但没有把 27 个相关格点宣称为独立发现。

因此 `core_v1` 的初始状态只能是 `watch`，不能自动成为 `active`，也不能产生订单或
纸面目标。

---

## 1. 目标与非目标

### 1.1 目标

1. 自动生成可复现的因子母集，完整记录所有注册表因子、虚拟字段、物理列、扩展数据
   来源、PIT 声明、覆盖率和最新非空日期。
2. 用多个研究池组织候选因子，池成员可以持续增加，但任何成员加入都不等于有效。
3. 用不可变版本管理因子集；新增、删除、调权、换角色都创建新版本，不覆盖旧版本。
4. 用追加式研究台账记录每次审计、负面结论、版本变更、证据哈希和操作原因。
5. 把“数据完整”“统计上有信号”“对现有集合有增量”“可投资”拆成四个独立维度，
   避免一条极端门槛把所有方向一次性否决。
6. 支持持续加入新因子：注册新因子 → 母集自动出现 → 研究池 watch → 新因子集版本 →
   追加式审计 → 只按证据逐级变更。
7. 让所有持久化研究结论可迁移、可校验、可在新机器复现；不依赖被 `.gitignore` 忽略的
   运行时缓存。

### 1.2 非目标

- 不自动发现新因子，不自动修改 DSL、指标管道或评分公式。
- 不重写 `FactorBacktestService`、`BacktestEngine` 或策略执行引擎。
- 不把因子集直接变成实盘账户、订单或杠杆配置。
- 不把当前沪深 300 名单声称为历史时点成分；所有回测仍须记录幸存者偏差边界。
- 不提交原始 K 线、Parquet、账户数据、Token 或大体量回测曲线。
- 不承诺一个因子在所有角色、所有市场状态下都有效。
- 不把 5× 成本当作杠杆；本项目策略仍是无杠杆、long-only、目标权重合计不超过 1。

---

## 2. 术语与状态模型

### 2.1 三个不同对象

| 对象 | 回答的问题 | 是否代表有效 |
| --- | --- | --- |
| 因子母集 `FactorMotherSet` | 当前系统里有哪些可解析的定义和数据字段？ | 否 |
| 研究池 `ResearchPool` | 我们准备持续观察哪些候选？ | 否 |
| 因子集版本 `FactorSetVersion` | 某一时点具体用哪些因子、什么角色、什么权重？ | 只代表当时的研究配置 |

因子母集是派生快照；研究池是可版本化的候选集合；因子集是带角色和权重的不可变研究
配置。三个对象不能互相替代。

### 2.2 三条正交状态轴

1. **定义可用性** `definition_status`：`available`、`retired`、`missing`。
   来自注册表、扩展配置、自定义因子文件和历史别名，不是收益评价。
2. **集合成员状态** `membership_status`：`watch`、`active`。
   `membership_status` 属于某个因子集版本；同一因子可以在 A 集合 active、在 B 集合
   watch，不能写入全局因子定义。
3. **证据结论** `evidence_verdict`：`unassessed`、`promising`、`mixed`、
   `negative`、`inconclusive`。只由审计台账产生，不回写注册表。

源定义中的 `draft/active/watch/retired` 继续由现有自定义因子 store 管理。它们只保证
定义生命周期和可加载性，不能被解释为因子集成员状态或证据结论。

### 2.3 引用类型

所有策略、复合因子和因子集引用都必须解析成以下类型之一：

| 类型 | 含义 | 是否可直接进入因子集 |
| --- | --- | --- |
| `registered_factor` | 当前注册表中的 `FactorSpec` | `alpha`、`risk_filter`、`conditioner`、`exit_signal` 可 |
| `virtual_scoring_field` | `scoring_value_expr` 可物化的虚拟字段 | 仅在该字段同时注册为因子时进入 |
| `physical_column` | `BASE_COLUMNS` 或 `ENRICHED_COLUMNS` 的已声明列 | 仅 `neutralizer`/`diagnostic` 显式允许 |
| `historical_definition` | 有证据的历史/退役定义别名 | 否，仅审计和历史报告 |
| `signal_field` | 字符串扩展字段或信号过滤字段 | 否，只能进入策略过滤条件 |
| `dangling_reference` | 以上都不是 | 否，必须报告并阻断相关写入 |

`amount` 与 `consecutive_limit_ups` 分别解析为 `physical_column`，不得被计入“缺失因子”。
`ext_alt_hs300_qrs_*`、`minrank`、Piotroski、自由现金流等历史 ID 只有存在显式
`historical_aliases.json` 记录和证据引用时才可标记为 `historical_definition`；不得仅凭
前缀或名称相似自动识别。

### 2.4 因子角色

因子集成员使用以下角色：

| 角色 | 用途 | 可接受的引用 |
| --- | --- | --- |
| `alpha` | 个股横截面打分或收益预测 | 数值 `registered_factor` |
| `risk_filter` | 剔除高风险标的或施加风险条件 | `registered_factor` 或显式 `physical_column` |
| `conditioner` | 市场状态、行业状态、事件门控 | `registered_factor`；市场级因子只能用于此角色 |
| `exit_signal` | 持仓退出或衰减信号 | `registered_factor` 或显式事件字段 |
| `neutralizer` | 行业、规模等风险中性化变量 | `registered_factor` 或 `physical_column` |
| `control` | 对照基线，不参与主评分 | `registered_factor` |
| `diagnostic` | 只展示统计结果 | 任意已解析数值引用 |

市场级/宏观因子不得被塞入个股 `alpha` 角色。分类不合格的因子可以留在母集和池中，
但只能作为 `conditioner`、`diagnostic` 或等待人工复核。

### 2.5 横截面类型

每个因子在每份母集快照中记录以下一种 `cross_section_type`：

- `stock_cross_sectional`：同一天不同股票有足够多不同值，适合个股排序；
- `industry_cross_sectional`：行业内近似常量、行业间有差异；
- `event_sparse`：大部分日期无持仓/无信号，只能做事件研究；
- `market_level`：同一天各股票值相同或只有一条市场序列，适合 regime/conditioner；
- `text_or_signal`：字符串或不可数值化的信号字段；
- `unknown`：证据不足，不能进入 active alpha。

分类必须基于多个日期的横截面统计，不以单日快照或字段名主观判定。计算方式见 §10.2。

---

## 3. 分层架构与数据流

### 3.1 组件

| 组件 | 职责 | 不负责 |
| --- | --- | --- |
| `FactorRegistryAdapter` | 读取注册表、扩展字段、动态因子，生成稳定签名 | 不改公式、不做回测 |
| `FactorReferenceResolver` | 解析注册表/虚拟字段/物理列/历史别名/悬空引用 | 不按名称猜测含义 |
| `FactorCatalogBuilder` | 生成不可变母集快照 | 不评价收益 |
| `ResearchPoolStore` | 管理研究池的不可变修订 | 不自动激活成员 |
| `FactorSetStore` | 管理不可变因子集版本和 head 解析 | 不修改旧版本 |
| `ResearchLedger` | 追加记录、哈希链、负面知识、回放 head | 不覆盖历史记录 |
| `FactorAuditService` | 覆盖率、分类、PIT、IC、增量、成本分层审计 | 不偷偷换因子或参数 |
| `FactorResearchApi` | 读快照、池、集合、台账和审计摘要 | 不在请求内跑全量扫描 |
| `FactorSetScoringAdapter` | 把明确的 alpha 成员转换为既有 scoring 配置 | 不静默丢 conditioner/exit/risk 角色 |

### 3.2 数据流

```text
registry.py / ext_factors.py / store.py
             │
             ▼
  FactorRegistryAdapter ──► FactorReferenceResolver
             │                         │
             ▼                         ▼
  registry snapshot            reference index
             └──────────────┬──────────┘
                            ▼
                   FactorCatalogBuilder
                            │  immutable snapshot
                            ▼
                  ResearchPoolStore（候选）
                            │
                            ▼
                 FactorSetStore（新版本）
                            │
                            ▼
                   ResearchAuditService
                            │
                            ▼
                  ResearchLedger（追加）
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
       derived heads/index           API / reports
```

### 3.3 边界

- API 只负责参数校验、读写编排和响应映射，不直接读 Parquet。
- 母集构建只读注册表和配置，不读取全量行情；数据覆盖率由单独审计任务生成。
- 重计算必须走已有 `FactorBacktestService`/`BacktestEngine` 面板加载路径，禁止另建一套
  因子计算或回测逻辑。
- 因子研究层失败不阻止应用启动；但 failed research subsystem 不能被当成“没有因子”
  或“全部有效”，相关写操作必须 fail-closed。
- 因子集只描述研究配置，不直接改变生产策略；策略必须显式钉住集合版本。

---

## 4. 存储与 Git 策略

### 4.1 目录

采用 `data/factor_research/` 作为版本化研究资产根目录。该目录不是运行时行情缓存，
需要通过 `.gitignore` 的精确例外纳入 Git：

```text
data/factor_research/
├── README.md
├── catalog/
│   ├── latest.json
│   ├── snapshots/
│   │   └── <snapshot_id>.json
│   ├── historical_aliases.json
│   ├── field_taxonomy.json
│   └── classification_evidence/
│       └── <snapshot_id>.json
├── pools/
│   └── <pool_id>/
│       ├── revisions/
│       │   └── r0001.json
│       └── head.json                 # 派生缓存，可由台账重建
├── sets/
│   └── <set_id>/
│       ├── versions/
│       │   └── v0001.json
│       └── head.json                 # 派生缓存，可由台账重建
├── policies/
│   ├── default_v1.json
│   └── core_v1.json
├── audits/
│   └── <audit_id>/
│       ├── manifest.json
│       ├── metrics.json
│       ├── findings.json
│       └── report.md
├── ledger/
│   ├── records.jsonl
│   ├── head.json                     # 派生缓存，可由 records 重建
│   └── schema.json
└── cache/                            # 忽略：大体量面板、NAV、临时扫描
```

`.gitignore` 的目标规则为：

```gitignore
!data/factor_research/
!data/factor_research/**
data/factor_research/cache/
data/factor_research/**/*.parquet
data/factor_research/**/*.tmp
data/factor_research/**/__pycache__/
```

`latest.json`、`head.json` 和 `ledger/head.json` 是派生指针，可以重建；删除它们不能让
已有定义丢失。所有 `snapshots/`、`versions/`、`revisions/`、`audits/` 和
`records.jsonl` 一旦写入即视为不可变内容。

### 4.2 为什么不用运行时目录

- `data/` 默认被忽略，不能把永久研究历史放进未被追踪的目录。
- `data/user_data/custom_factors/` 已有版本化例外，但只适合用户因子定义，不适合放
  因子集、审计台账和大体量证据。
- `data/strategies/ai/.iterations/` 继续承载旧的 R 轮研究；新因子研究系统使用独立目录，
  避免把研究基础设施耦合到某一个策略迭代。
- 原始数据、Parquet、NAV、bootstrap 路径缓存只进入 `cache/`，不进 Git。

### 4.3 文件写入规则

- JSON 文件先写同目录临时文件，`flush`、`fsync`、`os.replace`，再尽量 fsync 父目录。
- 已有版本文件禁止覆盖；若目标存在且内容 hash 不同，返回冲突而不是替换。
- `records.jsonl` 采用跨进程文件锁 + 追加 + `flush` + `fsync`。
- 所有写入都在 `data/factor_research/.write.lock` 的短临界区内完成；锁内不执行网络、
  全量回测或长时间因子计算。
- `head.json`/`latest.json` 是派生结果，不参与“是否可信”的最终判断；读取时以台账和
  内容 hash 校验为准。

### 4.4 派生指针 schema

`catalog/latest.json`：

```json
{
  "schema_version": 1,
  "snapshot_id": "sha256:<64-hex>",
  "registry_fingerprint": "sha256:<64-hex>",
  "classification_ref": "sha256:<64-hex-or-null>",
  "source_completeness": "complete",
  "generated_at": "2026-11-10T08:00:00Z",
  "factor_count": 220,
  "ledger_record_id": "lr_<24-hex>"
}
```

`pools/<pool_id>/head.json`：

```json
{
  "schema_version": 1,
  "pool_id": "core_v1",
  "revision": 1,
  "revision_sha256": "sha256:<64-hex>",
  "updated_at": "2026-11-10T08:00:00Z",
  "ledger_record_id": "lr_<24-hex>"
}
```

`sets/<set_id>/head.json`：

```json
{
  "schema_version": 1,
  "set_id": "core_v1",
  "state": "watch",
  "watch_version": 1,
  "watch_version_sha256": "sha256:<64-hex>",
  "active_version": null,
  "active_version_sha256": null,
  "paused_version": null,
  "paused_version_sha256": null,
  "updated_at": "2026-11-10T08:00:00Z",
  "ledger_record_id": "lr_<24-hex>"
}
```

`ledger/head.json`：

```json
{
  "schema_version": 1,
  "last_sequence": 42,
  "last_record_hash": "sha256:<64-hex>",
  "updated_at": "2026-11-10T08:00:00Z"
}
```

指针文件不携带业务事实；`ledger_record_id`/`*_version_sha256` 必须能在台账和不可变内容中
找到对应对象。删除指针后，从台账重放并重建。`expected_head_sha256` 只对 head 的语义
字段做规范化 hash，不包含 `updated_at` 和文件系统元数据；从台账重建后 token 必须一致。

---

## 5. 因子母集（Mother Set）

### 5.1 快照 schema

每份 `catalog/snapshots/<snapshot_id>.json` 是不可变 JSON，至少包含：

```json
{
  "schema_version": 1,
  "snapshot_id": "sha256:<64-hex>",
  "generated_at": "2026-11-10T08:00:00Z",
  "registry_fingerprint": "sha256:<64-hex>",
  "source_completeness": "complete",
  "factor_count": 220,
  "factors": [
    {
      "factor_id": "momentum_20d",
      "factor_version": 1,
      "label": "20日动量",
      "group": "动量",
      "kind": "base",
      "formula_text": "20个交易日累计收益率",
      "dependencies": [],
      "components": [],
      "direction": "none",
      "unit": "none",
      "warmup_bars": 1,
      "pit_status": "not_applicable",
      "pit_source": "none",
      "future_data_status": "clear",
      "asset_types": ["stock", "etf"],
      "scale_free": true,
      "incremental_safe": true,
      "definition_stability": "stable",
      "definition_status": "available",
      "source": {
        "type": "builtin",
        "module": "factors.registry",
        "catalog": "_CATALOG"
      },
      "data_class": "price",
      "cross_section_type": "stock_cross_sectional",
      "classification_ref": "sha256:<64-hex-or-null>",
      "classification_confidence": 0.98,
      "definition_fingerprint": "sha256:<64-hex>",
      "reference_kind": "registered_factor"
    }
  ],
  "physical_columns": [],
  "virtual_scoring_fields": [],
  "historical_definitions": [],
  "dangling_references": [],
  "load_errors": []
}
```

字段约束：

- `snapshot_id` 只由稳定内容决定，不包括生成时间和展示排序；同一注册表内容重复构建得到
  同一 ID。
- `source_completeness`：`complete`、`partial`、`unavailable`。扩展配置或自定义因子加载
  出错时为 `partial`，并保留 `load_errors`；`partial` 母集不能用于 active 提升。
- `factor_count` 是本次快照的实际数量，不得在代码里写死 220；220 只是当前基线。
- `classification_ref` 指向 `classification_evidence/<classification_id>.json`；没有分类
  证据时为 null。`classification_confidence` 是审计证据置信度，不是收益置信度。
- `definition_stability` 只复制注册表的定义稳定性，禁止映射为有效。

### 5.2 快照中的辅助集合

`physical_columns` 至少包含：

```json
{
  "column_id": "amount",
  "dtype": "float",
  "data_class": "price",
  "asset_types": ["stock", "etf"],
  "factor_eligible": false,
  "source": "enriched"
}
```

`virtual_scoring_fields` 至少包含：

```json
{
  "field_id": "turnover_z_60d",
  "registered_factor_id": "turnover_z_60d",
  "dependencies": ["turnover_rate"],
  "factor_eligible": true,
  "source": "strategy.scoring"
}
```

`historical_definitions` 从 `historical_aliases.json` 投影，包含原 id、分类、替代因子
id（如有）、证据引用和有效区间。`dangling_references` 只包含当前无法解析且没有历史
证据的引用，不能把尚未启用或未注册的候选名自动放入其中。

`catalog/field_taxonomy.json` 是数据类别和 PIT 提示的显式配置，不能从字段名推断。每条
记录至少包含：

```json
{
  "factor_id": "roe_latest",
  "data_class": "financial",
  "pit_hint": "declared",
  "asset_types": ["stock"],
  "source_field": "roe",
  "evidence_refs": []
}
```

规则：

- 内置财务因子和已知技术因子可以从注册表/数据契约得到初始分类；
- 扩展字段没有 taxonomy 记录时 `data_class=unknown`，不得 active；
- taxonomy 变更必须进入 fingerprint，并让相关因子集标记 stale；
- `pit_hint` 只提供线索，不能替代公告日联表的 `pit_status=verified` 证据；
- taxonomy 文件本身也必须记录内容 hash 和变更原因。

### 5.3 来源 locator

source 不使用机器绝对路径：

| 类型 | locator 示例 |
| --- | --- |
| builtin | `{"type":"builtin","module":"factors.registry","catalog":"_CATALOG"}` |
| extension | `{"type":"extension","config_id":"alt_hs300","field":"stock_rel_industry_20d"}` |
| custom | `{"type":"custom","factor_id":"uf_cash_quality","file_sha256":"..."}` |
| composite | `{"type":"composite","factor_id":"cf_industry_cash_quality","file_sha256":"..."}` |

扩展字段即使来自未追踪的 `data/ext_data`，其字段名、dtype、mode、配置指纹和分类证据
必须写入母集快照，保证从 Git 快照可以看到当时的数据契约。

### 5.4 定义指纹

`definition_fingerprint` 使用以下字段的规范化 JSON 计算：

- `kind`、`version`、`formula_text`；
- 递归展开前的直接依赖、复合成员及权重；
- `warmup_bars`、`asset_types`、`scale_free`、`incremental_safe`；
- `pit_status`、`pit_source`、`future_data_status`、`data_class`；
- 扩展来源的 `config_id`、字段名、dtype、mode、配置内容 hash；
- `field_taxonomy.json` 中与该因子相关的归一化记录；
- 自定义/复合文件的语义内容 hash。

展示字段 `label`、`group` 和 `generated_at` 不进入计算指纹。公式、参数、数据字段、
PIT 语义或来源契约变化必须产生新指纹；旧因子集因此标记 stale，不能静默替换。

规范化 JSON 固定为 UTF-8、键排序、无 NaN、紧凑分隔符，使用 SHA-256。指纹统一写成
`sha256:<64 位小写十六进制>`。

### 5.5 母集生命周期

1. 显式加载扩展配置和动态因子；失败要记录，不调用 lazy sync 后假装完整。
2. 读取所有 `FactorSpec`，展开虚拟字段依赖和复合成员。
3. 解析物理列、字符串信号字段和历史别名。
4. 计算注册表指纹和每个定义的指纹。
5. 若有数据审计缓存，挂接覆盖率、最新非空和横截面分类；没有则保持 `unassessed`。
6. 以原子写生成新快照；内容相同则复用已有 snapshot。
7. 更新 `latest.json`，并追加 `catalog_published` 台账记录。

`stable_only` 不能用于母集构建。母集必须展示全部注册因子，包括 experimental 和
retired 的历史可解析记录；是否可用由成员角色和审计决定。

---

## 6. 引用解析与策略覆盖审计

### 6.1 解析顺序

`FactorReferenceResolver.resolve(reference, context)` 严格按以下顺序执行：

1. 精确匹配当前 `FactorSpec`；
2. 精确匹配已注册的虚拟评分字段；
3. 精确匹配 `BASE_COLUMNS` 或 `ENRICHED_COLUMNS`；
4. 精确匹配 `historical_aliases.json` 中的历史别名；
5. 精确匹配字符串扩展字段，返回 `signal_field`；
6. 其他返回 `dangling_reference`。

不允许前缀匹配、模糊拼音、子串包含或“名字看起来像”。

### 6.2 历史别名文件

`catalog/historical_aliases.json` 的每条记录至少包含：

```json
{
  "id": "ext_alt_hs300_qrs_example",
  "classification": "historical_definition",
  "replacement_factor_id": null,
  "evidence_refs": [
    {"path": "data/strategies/ai/.iterations/.../evidence/example.json", "sha256": "sha256:..."}
  ],
  "note": "为什么确认它是历史定义，而不是当前注册表因子"
}
```

没有 `evidence_refs` 的别名无效。历史别名不能被新因子集成员引用。

### 6.3 策略审计

审计扫描以下真实引用来源：

- 策略 `meta.scoring` 的 key 和权重；
- 复合因子的 `members`；
- 自定义信号的字段引用；
- `overlay_filter` 等明确的字段引用；
- 因子集版本成员；
- 研究池成员（只做信息展示，不计入生产覆盖）。

每个引用输出：

```json
{
  "reference": "amount",
  "reference_kind": "physical_column",
  "resolved": true,
  "factor_eligible": false,
  "reason": "enriched physical column",
  "referenced_by": ["strategy:ai_hs300_multi_factor_rank"],
  "source_completeness": "complete"
}
```

审计结果必须分别给出：

- `registered_factor_references`
- `virtual_scoring_field_references`
- `physical_column_references`
- `historical_definition_references`
- `signal_field_references`
- `dangling_reference_references`

只有 `dangling_reference` 和参与 active 集合的不可用引用属于阻断项。物理列或历史别名
本身不是“缺失因子”。

### 6.4 审计输入隔离

- 单个损坏自定义因子文件只让该定义进入 `load_errors`，其他定义继续解析。
- 扩展配置损坏时该配置下的字段全部标记 unavailable；不得把旧缓存当作当前完整结果。
- 策略文件损坏时该策略不进入引用统计，并在报告里列出；不得静默跳过。
- 所有策略引用统计必须记录扫描文件数量、解析失败数量和 source completeness。

---

## 7. 研究池

### 7.1 池的版本化

研究池是可编辑但不可原地覆盖的候选集合。每次改动创建新的 `rNNNN.json`：

```json
{
  "schema_version": 1,
  "pool_id": "core_v1",
  "revision": 1,
  "label": "核心研究池",
  "purpose": "行业相对强弱、质量价值、现金质量和退出信号的候选集合",
  "not_an_efficacy_claim": true,
  "members": [
    {
      "factor_id": "ext_alt_hs300_stock_rel_industry_20d",
      "factor_version": 1,
      "definition_fingerprint": "sha256:<64-hex>",
      "reference_kind": "registered_factor",
      "role": "alpha",
      "added_at": "2026-11-10T08:00:00Z",
      "reason": "R360 行业相对强弱核心腿",
      "evidence_refs": []
    }
  ],
  "created_at": "2026-11-10T08:00:00Z",
  "parent_revision": null,
  "change_reason": "初始化候选池"
}
```

池修订规则：

- 同一池内 `factor_id` 唯一，且每个成员必须写入定义指纹，便于以后解释当时观察到的是
  哪一版定义；
- `revision` 单调递增，父修订必须存在；
- 成员可以重复出现在不同池；
- 池没有 `active` 生产语义；
- 池成员引用必须可解析，`dangling_reference` 直接拒绝；
- 删除成员通过新修订完成，旧修订保留；
- 修改使用 `expected_revision`；并发写一个成功，另一个返回 409。

### 7.2 初始池

首批池固定为：

- `core_v1`：R360 研究候选；
- `financial_v1`：财务质量、价值、成长、安全字段；
- `sentiment_flow_v1`：个股情绪、北向、融资、成交结构字段；
- `industry_v1`：行业动量、轮动、主线、行业内选股字段；
- `regime_v1`：市场情绪、宽度、周期、宏观条件字段。

`financial_v1`、`sentiment_flow_v1`、`industry_v1`、`regime_v1` 可以以空修订启动；
成员必须由分类审计和人工原因加入，不能按字段名或前缀批量塞入。

### 7.3 `core_v1` 种子

`core_v1` 初始成员固定为：

```text
ext_alt_hs300_stock_rel_industry_20d   0.35  alpha
ext_alt_hs300_industry_mom_20d         0.15  alpha
uf_quality_value_defensive             0.30  alpha
uf_cash_quality                        0.20  alpha
uf_industry_relative_strength_change_5d       exit_signal
```

所有成员的池状态都只是候选；`core_v1` 不对应 active 生产集。若任一 id 在当前注册表
不可解析，初始化必须失败并记录，不得静默替换为相似 id。

---

## 8. 不可变因子集

### 8.1 版本文件 schema

每个 `sets/<set_id>/versions/vNNNN.json` 至少包含：

```json
{
  "schema_version": 1,
  "set_id": "core_v1",
  "version": 1,
  "parent_version": null,
  "created_at": "2026-11-10T08:00:00Z",
  "created_by": "research",
  "change_reason": "R360 候选集合初始版本",
  "catalog_snapshot_id": "sha256:<64-hex>",
  "scope": {
    "asset_type": "stock",
    "universe": "hs300_current"
  },
  "members": [
    {
      "factor_id": "ext_alt_hs300_stock_rel_industry_20d",
      "factor_version": 1,
      "definition_fingerprint": "sha256:<64-hex>",
      "reference_kind": "registered_factor",
      "role": "alpha",
      "membership_status": "watch",
      "direction": "high",
      "weight": 0.35,
      "rationale": "R360 行业相对强弱核心腿",
      "evidence_refs": []
    },
    {
      "factor_id": "uf_industry_relative_strength_change_5d",
      "factor_version": 1,
      "definition_fingerprint": "sha256:<64-hex>",
      "reference_kind": "registered_factor",
      "role": "exit_signal",
      "membership_status": "watch",
      "direction": "low",
      "weight": null,
      "rationale": "行业相对强弱转负/走弱退出",
      "evidence_refs": []
    }
  ],
  "preflight": {
    "reference_status": "pass",
    "role_asset_scale_status": "pass",
    "weight_status": "pass",
    "catalog_snapshot_id": "sha256:<64-hex>"
  },
  "content_sha256": "sha256:<64-hex>"
}
```

`content_sha256` 不计入自身计算；它是去掉自身字段后的规范化 JSON hash，供台账和 API
校验。版本文件只保存创建时的 `preflight`，不保存会变化的审计结论；数据、PIT、参数和
可投资性 verdict 由独立 audit 记录按 `set_id + version + content_sha256` 关联。审计结论
变化时创建新的 audit 记录，不修改旧版本文件。

### 8.2 成员规则

- 每个成员必须有一个 `reference_kind`，并与 resolver 的分类一致；
- `alpha` 必须是数值型 `registered_factor`；
- `conditioner` 可以是市场级因子，但不能伪装成个股 alpha；
- `risk_filter` 可以使用已声明物理列，但必须显式写出 `reference_kind=physical_column`；
- `neutralizer` 可以使用已声明物理列，但必须显式写出 `reference_kind=physical_column`；
- 非注册因子成员的 `factor_version` 必须为 null，`definition_fingerprint` 由列 schema/
  signal field schema 计算；
- `exit_signal` 不进入常规横截面权重；
- `weight` 为 null 或有限非负数；负号只允许由 `direction=low` 在适配层显式转换；
- `watch` 版本中的 alpha 研究权重之和必须为 `1.0`（容差 `1e-9`）；
- active head 中 `membership_status=active` 的 alpha 权重之和必须为 `1.0`；
- active head 中 `membership_status=watch` 的 alpha 只能作为候选元数据，`weight` 必须为
  null，不能参与 scoring；
- `direction=high` 表示值越大越好，`low` 表示值越小越好；成员 schema 中的 `weight` 永不
  允许负数，适配层可以把 `low` 映射为有效负分；
- 同一版本内 `factor_id` 唯一；
- 任意成员的定义指纹必须与母集快照精确一致；
- `membership_status=watch` 的成员不参与策略 scoring 转换，但保留在研究版本中；
- active head 至少有一个 `alpha` 且其 `membership_status=active`；active alpha 权重必须
  单独和为一。

### 8.3 不可变与 head

- 已写入的版本文件永不修改；任何新增、删除、调权、换角色都生成 `vN+1`。
- `head.json` 只是台账的派生视图，不能成为唯一事实源。
- `head` 包含 `watch_version`、`active_version`、`paused_version`、`state` 和最后台账
  record id。
- `state` 只允许 `watch`、`active`、`paused`。
- `state=active` 时 `active_version` 非空、`paused_version` 为空；`state=paused` 时
  `active_version` 必须为空、`paused_version` 指向最后暂停的版本；`state=watch` 时
  `watch_version` 非空、`active_version` 和 `paused_version` 为空。暂停历史保存在台账，
  不回填到 watch head。所有非空版本都必须带对应的 `*_version_sha256`。
- `active_version` 必须通过强校验；校验失败时读取返回 `stale`/`blocked`，不能返回旧结果
  假装正常。
- 将 head 切到 `active` 前，必须存在与该版本 content hash 绑定的 audit，且至少满足：
  `integrity=pass`、`incremental=positive`、`parameter_status=pass`、
  `investability=paper_ready`，并且 source completeness 为 complete；`signal` 可以是
  `promising` 或 `mixed`，但不能是 `negative`。角色专属的过滤/门控策略可以在
  `policies/<set_id>.json` 中改写增量口径，但不能跳过 integrity 和 PIT。
- 生产/纸面策略必须钉住 `set_id + version`，不得自动跟随 active head；研究页面可以展示
  head 但必须显示解析到的具体版本。

### 8.4 失效判定

因子集版本在以下情况标记为 `stale`：

1. 成员 id 从当前注册表消失；
2. `factor_version` 或 `definition_fingerprint` 与当前定义不一致；
3. 扩展字段的 config/schema/dtype/mode 改变；
4. 自定义因子公式/参数/依赖/status 改变；
5. PIT 证据撤销；
6. 审计输入指纹改变但未生成新审计记录。

每日行情或扩展数据内容刷新本身不让集合定义 stale；它让上一次审计变成 `audit_stale`，
需要重新审计后才能维持 active。两者必须分开报告。

### 8.5 新增因子的标准流程

```text
注册/创建因子
  → 母集自动出现
  → 加入研究池（新 revision）
  → 创建因子集新版本，成员 watch
  → 数据完整性审计
  → 统计信号审计
  → 相对父版本的增量审计
  → 参数/成本/regime 稳定性审计
  → 新版本成员改 active，或记录 negative knowledge
```

任何阶段都可以停在 watch；没有证据时不允许写成 active。

---

## 9. 追加式研究台账

### 9.1 记录格式

`ledger/records.jsonl` 每行一个 JSON 对象。建议字段：

```json
{
  "schema_version": 1,
  "sequence": 42,
  "recorded_at": "2026-11-10T08:00:00Z",
  "actor": "research",
  "operation": "set_version_created",
  "set_id": "core_v1",
  "set_version": 2,
  "pool_id": null,
  "pool_revision": null,
  "factor_id": null,
  "factor_version": null,
  "catalog_snapshot_id": "sha256:<64-hex>",
  "evidence_refs": [
    {"path": "data/factor_research/audits/<audit_id>/manifest.json", "sha256": "sha256:<64-hex>"}
  ],
  "input_fingerprints": {
    "registry": "sha256:<64-hex>",
    "custom_factors": "sha256:<64-hex>",
    "ext_config": "sha256:<64-hex>",
    "enriched_generation": "uuid-or-null",
    "universe": "sha256:<64-hex>"
  },
  "metrics": {},
  "verdict": null,
  "reason": "新增候选并保留父版本",
  "negative_knowledge": null,
  "supersedes_record_id": null,
  "ledger_prev_hash": "sha256:<64-hex-or-64-zero>",
  "record_id": "lr_<24-hex>",
  "record_hash": "sha256:<64-hex>"
}
```

`record_hash` 计算时排除 `record_hash` 和 `record_id` 自身，包含 `ledger_prev_hash` 和
其他全部字段；`record_id` 由 `record_hash` 的前 24 位派生，并作为不参与 hash 的展示
字段写回。记录一旦追加不可修改、不可删除。

### 9.2 必须记录的操作

- `catalog_published`
- `reference_audit_completed`
- `pool_revision_created`
- `set_version_created`
- `set_head_changed`
- `audit_completed`
- `factor_verdict_recorded`
- `negative_knowledge_recorded`
- `correction_recorded`
- `ledger_reconciled`

`correction_recorded` 只能追加新记录并用 `supersedes_record_id` 指向旧记录；不能重写旧行。

### 9.3 负面知识

每次候选失败都写：

- 试验 id、因子集版本、父版本；
- 数据范围和 universe；
- 使用的参数；
- 失败在数据、统计、增量、成本、容量或样本外哪一层；
- 证据路径和 hash；
- 不重复尝试的理由，或未来允许重试的明确条件。

失败记录永久保留。之后重新测试时新增记录，不覆盖失败结论。

### 9.4 原子事务顺序

新增因子集版本的写入顺序：

1. 获取 `data/factor_research/.write.lock`；
2. 校验 `expected_head`、父版本、引用和冻结指纹；
3. 写入不可变版本文件并 fsync；
4. 追加台账记录并 fsync；
5. 写派生 `head.json` 和 `ledger/head.json`；
6. 释放锁。

崩溃后可能出现“版本文件已写但台账没写”或“台账已写但 head 没写”：

- 孤儿版本文件不被 head 采用；
- head 缺失/落后时由台账重放恢复；
- 读取发现孤儿或未完成事务时返回 `incomplete_transaction`，写操作 fail-closed；
- 不自动删除孤儿文件，避免破坏证据。

### 9.5 并发与幂等

- 同进程使用 `threading.RLock`，跨进程使用与 `enriched_generation` 类似的文件锁；
- `expected_revision`/`expected_head` 不匹配返回 409；
- 同一请求重复提交，如果内容 hash、父版本和目标完全相同，可返回已有记录；否则拒绝；
- 写锁内禁止网络、回测和长时间扫描。

---

## 10. 审计设计

### 10.1 审计分四层，不合成一个“万能分”

| 层级 | 回答的问题 | 失败结果 |
| --- | --- | --- |
| `integrity` | 定义、引用、数据、PIT、样本是否可用 | `fail` / `unknown` |
| `signal` | 单因子是否有稳定统计信号 | `promising` / `mixed` / `negative` / `inconclusive` |
| `incremental` | 加入父集合是否带来样本外增量 | `positive` / `neutral` / `negative` / `unknown` |
| `investability` | 在现实成本和约束下是否值得纸面复核 | `paper_ready` / `warning` / `failed` / `not_tested` |

最终不会输出“某因子永远有效”。输出是某个因子在某个集合版本、universe、horizon、
成本口径和证据窗口下的结论。

### 10.2 数据完整性

硬性 integrity 检查：

1. 引用可解析且类型与角色兼容；
2. 定义指纹与母集一致；
3. 资产类型兼容；
4. 数值字段 dtype 可安全转 Float64；
5. 存在非空样本；
6. 正常横截面因子在五年窗口至少有 120 个有效截面日期，近一年至少有 40 个；
7. 每个有效截面至少 30 个非空标的（稀疏事件因子走事件分支）；
8. 市场级因子在五年窗口至少有 120 个有效日期；
9. 不存在未来数据；PIT 财务因子必须有公告日联表证据；
10. 输入数据 generation/内容指纹可用；
11. 没有超过数据类别允许的最新值滞后。

默认数据完整性阈值放在 `policies/default_v1.json`，不是散落在代码里的常量：

| 数据类别 | 最大滞后交易日 | 说明 |
| --- | ---: | --- |
| `price` | 10 | 价量、技术指标 |
| `sentiment` | 10 | 个股情绪/成交结构 |
| `flow` | 10 | 北向、融资、资金流 |
| `industry` | 20 | 行业状态/轮动 |
| `macro` | 45 | 宏观/regime |
| `financial` | 120 | 季度公告与 PIT 联表 |
| `unknown` | 0 | 不允许 active |

覆盖率低于 10% 或有效截面不足的因子不能进入 normal-rank active；可以进入池或事件
研究。覆盖率阈值是“能否计算结论”的完整性边界，不是收益门槛。

`policies/default_v1.json` 的稳定键如下；数值只是默认研究策略，必须由集合 policy 明确
覆盖或继承，不得在代码中重复硬编码：

```json
{
  "schema_version": 1,
  "policy_id": "default_v1",
  "sample_sufficiency": {
    "rank_min_valid_cross_sections_5y": 120,
    "rank_min_valid_cross_sections_recent": 40,
    "rank_min_non_null_symbols_per_date": 30,
    "market_min_valid_dates_5y": 120,
    "minimum_non_null_ratio": 0.10
  },
  "staleness_trading_days": {
    "price": 10,
    "sentiment": 10,
    "flow": 10,
    "industry": 20,
    "macro": 45,
    "financial": 120,
    "unknown": 0
  },
  "cost": {
    "baseline_multiplier": 2,
    "hard_stress_multiplier": 3,
    "diagnostic_5x": {
      "hard_rejection": false,
      "warnings": ["negative_5x_annual", "negative_5x_total_return", "drawdown_deterioration", "thin_net_to_gross"]
    }
  },
  "leverage": {
    "required": "none",
    "max_weight_sum": 1.0,
    "min_target_weight": 0.0
  },
  "multiple_testing": {
    "require_trial_family": true,
    "missing_family_verdict": "inconclusive"
  },
  "parameter_audit": {
    "required_for_active": true,
    "center_point_platform_required": true,
    "correlated_grid_units_are_not_independent": true
  }
}
```

### 10.3 横截面分类

分类审计对每个因子在选定窗口（默认五年 252 个最近的共同日期，近一年 60 个）计算：

- 每日非空标的数；
- 每日非空唯一值数；
- 每日行业内唯一值数；
- 每个有效日的空值比例；
- 同一市场级值是否在所有标的上重复；
- 活跃日期比例。

分类规则：

- `market_level`：至少 80% 的有效日中，跨股票唯一值数 ≤ 1，且覆盖多个标的；
- `industry_cross_sectional`：至少 60% 的有效日中，行业内值近似常量且行业间有差异；
- `stock_cross_sectional`：至少 60% 的有效日中，非空标的 ≥30 且唯一值 ≥10；
- `event_sparse`：有效日期比例 <10%，或大部分日期非空标的 <10 但存在至少 30 个事件日；
- `text_or_signal`：原始 dtype 为字符串或不可数值化；
- `unknown`：以上都不成立。

分类证据文件 schema：

```json
{
  "schema_version": 1,
  "classification_id": "sha256:<64-hex>",
  "classifier_version": "cross_section_v1",
  "generated_at": "2026-11-10T08:00:00Z",
  "window": {"start": "2021-11-10", "end": "2026-11-10", "dates_evaluated": 252},
  "input_fingerprints": {
    "enriched_generation": "uuid",
    "universe": "sha256:<64-hex>",
    "field_taxonomy": "sha256:<64-hex>"
  },
  "factors": [
    {
      "factor_id": "ext_example",
      "cross_section_type": "stock_cross_sectional",
      "confidence": 0.97,
      "active_date_ratio": 0.94,
      "median_non_null_symbols": 286,
      "median_unique_values": 284,
      "industry_constant_ratio": 0.12,
      "reasons": ["cross-sectional uniqueness and breadth passed"]
    }
  ]
}
```

分类结果写入母集快照的摘要字段并引用该文件，不把单日结论当永久事实。分类算法版本或
输入指纹变化时必须生成新分类证据，旧集合版本保持原指纹并标记审计过期。

### 10.4 PIT 与未来数据

`pit_status` 四值：

- `verified`：有公告日/可得日联表测试或可核验证据；
- `declared`：只声明 `pit=True`，尚无公告日验证；
- `not_applicable`：价量/技术等不使用未来数据，且有测试或数据契约证明；
- `unknown`：无法确认。

内置 11 个财务因子的 `FactorSpec.pit=True` 只记为 `declared`，不能自动升级为 `verified`。
扩展财务字段默认 `unknown`；`snapshot` 模式扩展字段禁止进入历史 alpha 集合。

active 集合规则：

- 财务类 alpha/risk_filter/conditioner/exit_signal：必须 `pit_status=verified`；
- 非财务价量因子：`future_data_status=clear`；
- 任何 `unknown` PIT/未来状态的因子只能 watch 或 diagnostic；
- 不因“历史上看起来有效”而跳过 PIT 检查。

### 10.5 统计信号审计

统计信号是证据，不是一票否决。默认审计记录：

- 五年和近一年 Rank IC 均值、标准差、IR、NW t、命中率；
- 逐年 IC 符号一致率；
- 行业中性、规模中性、行业+规模中性后的 IC；
- 换手率和持有期敏感性；
- 分 regime 的 IC/收益；
- block bootstrap 和 stationary bootstrap 的路径分布；
- 参数邻域平台：默认参数必须位于平台中心，不选单点最优；
- 与父集合成员的相关系数、VIF/冗余和边际 IC；
- 多重检验记录：试过的参数/因子变体数量；无法还原完整试验族时标记 `unadjusted`。

未完成多项检验校正或试验族不可知时，不得把 `promising` 升级为 active；可以记录为
`inconclusive`。

### 10.6 增量贡献审计

因子集不是单因子排行榜。active 提升必须相对 `parent_version` 做同口径比较：

- 同一 universe、同一日期、同一交易日历；
- 同一信号时点、T+1 开盘执行、涨跌停/停牌约束；
- 同一 2× 成本基准和 3× 压力；
- 同一持仓、再平衡和风险约束；
- 分别比较 IC、IR、年化、Sharpe、MDD、换手、集中度和 fresh-start 分布；
- 至少报告一个“移除该因子后的 control set”；
- 不能只展示候选集的绝对收益。

默认增量 verdict：

- `positive`：在预注册 window 中，多个独立指标方向一致，且 bootstrap 区间没有明显反向；
- `neutral`：点估计略好但区间跨零，或收益改善来自集中度/换手恶化；
- `negative`：同口径下核心风险收益指标持续变差；
- `unknown`：缺少父版本、对照、数据或指纹。

具体数值阈值写入集合策略文件，并由审计前预注册；不允许事后看到结果再挑门槛。

### 10.7 参数与蒙特卡洛

参数审计针对因子公式参数、持有期、退出窗口、风险过滤阈值和评分权重族的邻域：

1. 冻结数据、universe、成本、执行和父版本；
2. 预注册参数网格或分布；
3. block bootstrap（例如 5/10/20/60 日块）和 stationary bootstrap；
4. 对参数邻域做 LHS/Sobol 或预注册网格；
5. 记录中心参数、P05、中位数、P95、正收益概率、最差 MDD 和平台通过率；
6. 相关格点不能当成独立发现；
7. 未覆盖的关键参数必须写成 `not_covered`，不能宣称“全部参数合格”。

R370/R371 的证据可以作为输入，但不能替代尚未覆盖的行业回看、四腿权重、过滤阈值、
黄金均线和 regime 映射。

### 10.8 成本、杠杆与 5×

- `2×` 是现实执行成本基准；
- `3×` 是严重执行成本压力，默认硬 gate；
- `4×` 是次级诊断；
- `5×` 是极端交易成本诊断，只能产生 warning，绝不能单独把候选判死；
- 杠杆与成本倍率完全分离：无杠杆条件单独检查 `max_weight_sum ≤ 1.0` 且目标权重非负；
- 5× 净收益接近零时，净贡献/净利润占比不稳定，不能作为硬集中度门槛；优先看 gross
  贡献、持仓宽度、行业暴露和单标的最大亏损。

`policies/default_v1.json` 必须显式包含 `"diagnostic_5x": {"hard_rejection": false}`。
任何模块不得把 5× 结果映射为杠杆或自动 `rejected`。

### 10.9 审计输出

每个 `audits/<audit_id>/` 至少包含：

- `manifest.json`：scope、时间、代码版本、policy hash、输入指纹、命令、源完整性；
- `metrics.json`：所有参与因子/集合的指标和缺口；
- `findings.json`：按 integrity/signal/incremental/investability 分层的 verdict；
- `report.md`：人能读的结论，明确 negative knowledge 和未覆盖项。

`audit_id` 由 scope、输入指纹、policy hash 和代码版本规范化后 hash 得到。输入不变时重跑
必须幂等；输入改变时生成新 id，不能覆盖旧审计。

---

## 11. API 与服务边界

### 11.1 模块结构（计划）

```text
backend/app/factor_research/
├── __init__.py
├── models.py          # Pydantic/dataclass 契约
├── fingerprints.py    # 规范化 JSON、hash、版本指纹
├── references.py      # 五类引用解析
├── catalog.py         # 母集构建/快照
├── pools.py           # 研究池修订
├── sets.py            # 不可变因子集版本
├── ledger.py          # 追加式台账和 head 重放
├── audit.py           # 四层审计编排
├── policies.py        # policy 读取/校验
├── locking.py         # 跨平台短写锁
└── scoring_adapter.py # 因子集 → scoring 的显式转换

backend/app/api/factor_research.py
backend/tests/factor_research/
scripts/sync_factor_research_catalog.py
scripts/run_factor_research_audit.py
```

现有 `registry.py`、`store.py`、`ext_factors.py`、`backtest/factor.py` 不在本设计中重写。
仅在已有写入口增加必要的 cache invalidation 调用；若签名检查已经足够，优先不改核心文件。

### 11.2 读接口

```text
GET /api/factor-research/catalog/latest
GET /api/factor-research/catalog/{snapshot_id}
GET /api/factor-research/pools
GET /api/factor-research/pools/{pool_id}
GET /api/factor-research/pools/{pool_id}/revisions/{revision}
GET /api/factor-research/sets
GET /api/factor-research/sets/{set_id}
GET /api/factor-research/sets/{set_id}/versions/{version}
GET /api/factor-research/ledger?after_sequence=N&limit=M
GET /api/factor-research/audits/{audit_id}
```

读接口只返回已持久化内容；不因为调用读接口而启动全量扫描。列表分页必须有上限，响应
包含 `source_completeness`、`stale` 和明确的缺失原因。

### 11.3 写接口

```text
POST /api/factor-research/pools/{pool_id}/revisions
POST /api/factor-research/sets/{set_id}/versions
POST /api/factor-research/sets/{set_id}/head
```

请求必须带 `expected_revision` 或 `expected_head`。服务端自己重新解析引用和计算指纹，
不信任客户端上传的 `definition_fingerprint`。所有成功写操作追加台账记录。

最小请求契约：

```json
POST /api/factor-research/pools/core_v1/revisions
{
  "expected_revision": 1,
  "change_reason": "加入新的候选因子",
  "members": [
    {"factor_id": "ext_example", "factor_version": 1, "role": "alpha", "reason": "研究假设"}
  ]
}
```

```json
POST /api/factor-research/sets/core_v1/versions
{
  "expected_head_sha256": "sha256:<64-hex>",
  "parent_version": 1,
  "change_reason": "新增 watch 候选，保持 active 成员不变",
  "members": []
}
```

```json
POST /api/factor-research/sets/core_v1/head
{
  "expected_head_sha256": "sha256:<64-hex>",
  "state": "watch",
  "version": 2,
  "change_reason": "证据仅达到 watch"
}
```

`expected_head_sha256` 是当前 `head.json` 规范化内容的 hash，不是集合版本的 hash。服务端
在锁内重新计算并比较，防止并发覆盖。

写接口错误码：

| 状态码 | 条件 |
| --- | --- |
| 400 | schema/JSON 不合法、ID 格式错误 |
| 404 | 池/集合/版本不存在 |
| 409 | expected 版本冲突、重复版本、head 已变化 |
| 422 | 引用悬空、角色/资产类型/PIT/权重不兼容 |
| 423 | 写锁被其他进程占用 |
| 503 | 台账/母集/数据 generation 不可用 |

API 不返回本机绝对路径、读取密钥或原始 Parquet 内容。

### 11.4 重任务

全量覆盖率、IC、bootstrap、参数邻域审计通过 CLI/worker 执行，API 只读取已生成审计。
后续若接入现有 job 框架，必须沿用 `MiningJobManager`/后台任务模式，不能在 FastAPI 请求
线程中跑长扫描。首版不新建 job 引擎。

### 11.5 评分适配

`scoring_adapter.factor_set_to_scoring(version, include_roles=("alpha",))`：

- 只把 `membership_status=active` 的 alpha 成员转成 scoring；
- `direction=low` 转成负权重；`direction=high` 保持正权重；
- 遇到 conditioner/exit_signal/risk_filter 时，默认拒绝转换，除非调用方显式要求策略层
  单独处理；
- 返回 `set_id`、`version`、`content_sha256`，调用方必须保存；
- 研究阶段可以使用 watch 成员，但输出必须标记 `research_only=true`。

---

## 12. 缓存、失效和一致性

### 12.1 缓存键

所有研究层缓存必须至少包含：

```text
schema_version
registry_fingerprint
custom_factors_fingerprint
ext_config_fingerprint
enriched_generation
universe_fingerprint
date_start/date_end
policy_sha256
code_profile/version
```

只使用文件 mtime 或单一日期的缓存键不合格。

### 12.2 失效时机

- 自定义因子保存、更新、删除、改状态、改分组：重新计算 registry/custom fingerprint；
- 扩展配置变更：扩展因子同步失效，研究快照和分类失效；
- 扩展数据 routine refresh：不清空已生成的审计文件，但把审计标记 `audit_stale`；
- enriched generation 变更：数据覆盖率/IC 缓存失效；
- 当前沪深 300 名单变更：universe 指纹变化，相关审计过期；
- policy 文件变化：旧审计保留，但新提升必须使用新 policy hash。

### 12.3 一致性规则

- 先验证指纹，再使用缓存；无法验证时重算或返回 unknown。
- active 集合读取必须同时验证版本文件、head、台账记录和当前定义指纹。
- 缓存不能跨数据 generation 复用 active 结论。
- 单个池/审计文件损坏只隔离该对象；台账损坏阻断所有新写和 active 读取。

---

## 13. 与现有代码的兼容和迁移

### 13.1 启动顺序

1. 现有应用启动逻辑加载自定义信号和动态因子；
2. `FactorRegistryAdapter` 显式加载扩展配置并记录错误；
3. 构建或读取母集快照；
4. 校验 `latest.json` 指向的快照；
5. 研究 API 初始化完成。

研究 API 初始化失败不能让因子库、策略库或 K 线接口整体不可用；但研究写接口返回 503，
研究中不能把空结果当成“无因子”。

### 13.2 旧数据

- `data/strategies/ai/.iterations/.../LEDGER.md` 和既有 evidence 保留原位，不迁移、不改写。
- 新系统通过 evidence refs 读取旧结果；路径和 SHA-256 写入新台账。
- 旧脚本 `scripts/audit_factor_strategy_coverage.py` 的结论只能作为线索，不能作为新母集
  或因子集的自动输入。
- 旧 R 轮编号不重新编号；新因子研究使用独立 audit id，不伪造 R 编号。

### 13.3 迁移步骤

1. 建立 `data/factor_research/` 和 `.gitignore` 例外；
2. 生成 `catalog/snapshots/<snapshot_id>.json`；
3. 创建五个初始池修订；
4. 创建 `core_v1` watch 版本；
5. 写入 `default_v1` 和 `core_v1` policy；
6. 对当前核心成员做一次 reference、coverage 和 PIT 审计；
7. 将结果写台账，明确 `active_version=null`；
8. 不改变现有策略配置和因子计算。

迁移脚本必须幂等：已有同内容快照/版本复用，不重复写台账；冲突时 fail-closed。

---

## 14. 测试与验收

### 14.1 单元测试

必须覆盖：

1. `amount`、`consecutive_limit_ups` 被解析为物理列，不计入 dangling；
2. 注册因子、虚拟字段、物理列、历史别名、字符串信号、悬空引用六类各自独立；
3. `stable` 不会产生 `evidence_verdict=promising`；
4. 同一 id 的不同 `factor_version`/definition fingerprint 产生 stale；
5. 研究池修订单调、重复 id 拒绝、父修订缺失拒绝；
6. 因子集版本不可修改，已存在版本冲突不覆盖；
7. alpha 只能引用数值注册因子；市场级因子不能进入个股 alpha；
8. PIT 财务因子未验证不能 active；
9. 5× 只产生 warning，不产生 hard rejection；
10. 2×/3× 成本语义和 no-leverage 独立；
11. 规范化 JSON、snapshot id、record hash 确定性；
12. ledger 记录不可覆盖、重复 record id 拒绝、correction 走新记录；
13. 单个损坏自定义因子只影响该定义；
14. 缺失 data generation、空面板、全空字段和 stale 数据都返回明确 unknown/failed；
15. 并发写同一 head/revision 一成功一 409；
16. 孤儿版本、落后 head 和 incomplete transaction 不被 active 采用。

### 14.2 集成测试

- 临时 `data_dir` 下构造扩展配置、自定义因子和策略，运行母集和引用审计；
- 验证旧审计中的 20 个“缺失”引用按新分类拆开；
- 验证 ext 字段的 106 个条目、33 个动态因子和 81 个内置因子都进入快照（数量按临时
  fixture 断言，不在生产测试写死 220）；
- 验证注册表/扩展配置改变后缓存不会复用旧分类；
- 验证 `core_v1` 只能创建 watch head；
- 验证 API 成功、空数据、冲突、损坏和 source partial 的响应；
- 验证评分适配只取 active alpha，不会静默丢弃退出信号。

### 14.3 失败路径

- 目录不存在、无权限、磁盘满、临时文件残留；
- JSON 语法错误、schema 版本不支持、hash 不匹配；
- ledger 最后一行截断、中间行损坏、prev hash 断链；
- 同一进程并发、两进程并发、写锁占用；
- 扩展配置部分加载、自定义因子部分加载；
- registry 在母集构建期间变化；
- active head 指向已删除/已退役/已改指纹成员；
- 5× 结果缺失或异常，不能影响 2×/3× 的独立结论。

### 14.4 性能与验证命令

- 母集构建只遍历注册表，不做全量行情扫描；
- 覆盖率/IC 按日期和列批量扫描一次，禁止每因子全量读 Parquet；
- API 列表只读快照和摘要，响应时间不随日分区数线性增长；
- 全量审计必须记录 elapsed_ms、行数、日期范围和 source completeness。

后端验证：

```bash
cd backend
uv run --frozen pytest tests/factor_research -q
uv run --frozen pytest tests/test_factor_registry.py tests/test_factor_store.py tests/test_factor_api.py -q
uv run --frozen ruff check app/factor_research app/api/factor_research.py tests/factor_research
```

仓库级：

```bash
git diff --check
git status --short --branch
```

前端首版不改；若后续增加只读研究页，至少执行 `cd frontend && pnpm build`。

### 14.5 验收标准

以下全部满足才认为设计实现完成：

1. 当前注册表能生成一份完整母集，且所有 220 个当前因子有唯一 `factor_id`；
2. `amount`、`consecutive_limit_ups` 不再被报告为缺失因子；
3. 106 个扩展字段、33 个动态因子、81 个内置因子分别有来源和引用类型；
4. `stable` 与 `active` 完全分离；
5. 所有集合写操作都能生成新版本并追加台账，不覆盖旧版本；
6. active 读取会检查 fingerprint、head、台账和数据 freshness；
7. 5× 在任何路径都不会单独硬拒绝；
8. 财务因子没有 PIT 证据时不能 active；
9. `core_v1` 只生成 watch 版本，active_version 为 null；
10. 损坏/并发/过期路径都有测试和明确的 fail-closed 行为；
11. 生产策略、因子计算和历史 R 证据没有被本层静默修改。

---

## 15. 实施切片（后续写详细计划）

本设计对应一个独立子系统，实施按以下顺序拆任务；每个任务都必须先写失败测试：

1. **指纹与模型**：规范化 JSON、schema 校验、ID/fingerprint/hash；
2. **引用解析器**：六类引用、历史别名、策略扫描；
3. **母集快照**：注册表适配、来源 locator、快照 id、latest 指针；
4. **池版本**：修订、head、并发冲突、核心池种子；
5. **因子集版本**：成员校验、权重、角色、head、stale 判定；
6. **台账**：哈希链、追加、重放、负面知识、孤儿/损坏隔离；
7. **审计**：分类、覆盖率、PIT、IC、增量、成本分层；
8. **API/CLI**：读接口、写接口、离线审计命令；
9. **迁移与验收**：旧证据引用、core_v1 watch、文档和回归矩阵。

首版不加入：自动因子挖掘、自动策略上线、生产订单、实时审计调度、全量前端编辑器和
新的数据库服务。

---

## 16. 已作出的关键决定

1. **永久研究资产放 `data/factor_research/`，通过精确 `.gitignore` 例外追踪。**
   运行时 Parquet/NAV/临时扫描仍忽略。
2. **母集是派生快照，不是有效性榜单。**
3. **研究池可变但通过新 revision 记录；因子集版本严格不可变。**
4. **成员状态属于因子集版本，不属于全局因子定义。**
5. **同一因子可以在不同集合中处于不同状态。**
6. **所有引用先解析类型，再做覆盖率/缺失判断。**
7. **财务 PIT 默认只算声明，证据不足时不能 active。**
8. **2× 为基准、3× 为压力、5× 只作诊断警告；杠杆与成本倍率分离。**
9. **首版以 CLI 跑重审计、API 读结果和少量写操作为主，不在请求线程跑全量扫描。**
10. **R360 核心种子只进入 watch，不进入生产 active。**

---

## 附录 A：`core_v1` 初始版本的关键值

```text
set_id: core_v1
version: 1
head: watch
active_version: null
catalog: 2026-11-10 母集快照
members:
  ext_alt_hs300_stock_rel_industry_20d   0.35  alpha       watch
  ext_alt_hs300_industry_mom_20d         0.15  alpha       watch
  uf_quality_value_defensive             0.30  alpha       watch
  uf_cash_quality                        0.20  alpha       watch
  uf_industry_relative_strength_change_5d       exit_signal watch
```

说明：这组权重来自 R360 的研究配置，不是生产仓位权重；`exit_signal` 不参与 alpha
权重和普通 scoring 转换。

## 附录 B：默认审计 verdict 示例

```json
{
  "factor_id": "ext_example",
  "set_id": "core_v1",
  "set_version": 1,
  "integrity": "pass",
  "signal": "promising",
  "incremental": "unknown",
  "investability": "not_tested",
  "overall": "watch_only",
  "reasons": [
    "PIT 已证明",
    "覆盖率通过",
    "尚未完成相对父版本的增量审计"
  ],
  "negative_knowledge": null
}
```

该示例故意在 `signal=promising` 时仍保持 `watch_only`，防止把单因子统计信号误写成
集合有效。
