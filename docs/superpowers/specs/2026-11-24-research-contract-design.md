# 全仓研究合同设计

> 状态：已确认实施。  
> 生效日期：2026-11-24。  
> 适用范围：本仓库后续新增的研究运行器，以及合同生效后发生修改的历史研究运行器。  
> 目标部署：源码运行、CI、Docker 构建均使用同一份合同。

## 1. 目标

把当前分散在文档、脚本约定和审计器中的研究要求收敛为一个可执行合同：

1. 每项新研究在运行前冻结问题、假设、数据、样本、参数、门槛、证伪条件和随机种子；
2. 每项研究必须声明唯一研究类型，不能靠文件名、注释或人工口头分类；
3. 真实收益结论必须走项目正式回测证据链，合成或自写模拟不能冒充正式回测；
4. 结果只能新增，不得覆盖；失败和否决必须形成可追溯负知识；
5. 结果、预注册、运行器、输入和台账必须通过 SHA-256 与 append-only ledger 绑定；
6. CI 对上述要求 fail-closed，任何不符合合同的新增或修改研究均不得合并。

本设计不把历史研究改写成满足新合同的资产。合同生效前的运行器按内容哈希冻结为
`legacy_grandfathered`；若其内容发生变化，则视为新研究并必须满足合同。

## 2. 当前基线

- 现有研究运行器分布在 `scripts/research_*.py` 与
  `data/strategies/ai/.iterations/**/evidence/run_*.py`。
- 已有 `ResearchLedger` append-only 哈希链、sidecar 卫生审计、正式回测
  `run_with_evidence` 和 `require_formal_evidence`。
- 现有 `research_standard_rules_2026-11-24.json` 只有分类和禁止项，没有统一运行前
  合同、类型专用必填字段、结果 envelope 或自动豁免边界。
- 现有 conformance 审计以静态文本启发式分类，不能证明预注册、结果发布和台账绑定。
- 后端全量测试会执行合同测试，但 CI 尚未单独展示研究合同审计步骤。

## 3. 非目标

- 不自动批准 `active_version`、生产启用、纸面目标或订单；
- 不把 GBM 等合成市场结果升级为真实历史回测结论；
- 不追溯伪造历史预注册或改写历史结果；
- 不用新合同替代数据 PIT、因子审计、参数审计和策略准入；
- 不为单一研究新建通用工作流平台；只实现当前需要的合同边界与确认门禁。

## 4. 合同类型

研究类型是封闭枚举。新增类型必须同时更新规范、Pydantic schema、审计器和测试。

| 类型 | 用途 | 硬要求 |
| --- | --- | --- |
| `project_formal` | 项目真实数据上的组合收益/风险结论 | 项目正式回测；三项证据全通过；正式证据摘要 |
| `statistical_screen` | IC、IR、覆盖率、相关性和统计准入 | 项目或登记外部数据；声明方法和输入哈希 |
| `feasibility_probe` | 数据可得性与字段覆盖探测 | 明确外部/项目来源；不得宣称可交易 |
| `attribution` | 解释已冻结结果 | 引用冻结结果哈希；不得产生新收益路径 |
| `derived_analysis_only` | 对冻结结果做 bootstrap、汇总、对账 | 引用冻结结果哈希；不得产生新收益路径 |
| `synthetic_market_stress` | GBM 等合成行情下的项目撮合压力测试 | 合成模型、seed、路径数、等权对照；不得冒充真实回测 |
| `diagnostic_simulation` | 自写 ETF/overlay/收益模拟 | 明确诊断边界和限制；不得升级为正式证据 |
| `non_return_research` | 不产生收益、IC 或策略结论的研究 | 明确非收益性质 |

## 5. 预注册合同

每项新研究运行前必须生成 `preregistration.json` 与同名 `.sha256`。预注册至少包含：

```json
{
  "schema_version": 1,
  "research_id": "R-GBM-001",
  "research_type": "synthetic_market_stress",
  "question": "在固定 GBM 分布下，策略是否比同路径等权有稳定优势？",
  "hypothesis": "策略优势来自信号，而不是特定市场漂移。",
  "study_scope": "research_only",
  "runner": "scripts/research_gbm_market_stress.py",
  "runner_sha256": "<64 hex>",
  "data_source": "synthetic_gbm",
  "input_hashes": {},
  "parameters": {},
  "sample": {},
  "execution": {},
  "gates": {},
  "falsification_checks": [],
  "output_policy": {
    "active_version": null,
    "orders_generated": false,
    "paper_target_generated": false,
    "production_enabled": false
  },
  "created_at": "<ISO-8601>"
}
```

硬约束：

- `research_id`、`runner` 唯一且 runner 路径在仓库内；
- `study_scope` 只能是 `research_only`；
- `runner_sha256` 必须与当前 runner 字节一致；
- `output_policy.active_version` 必须为 `null`，其余执行型输出必须为 `false`；
- 预注册必须早于结果文件，且结果引用同一预注册哈希；
- 预注册生成后不能修改问题、假设、数据、样本、参数、门槛、证伪条件和 seed；
- 任何修订必须新增 amendment，并声明旧结果不被追溯重标。

## 6. 类型专用准入

### 6.1 project_formal

- `data_source` 为 `project`；
- `execution` 必须声明 `open_t+1`、无杠杆、最大敞口不超过 100%、2× 基线成本；
- 运行器必须出现 `run_with_evidence` 和 guard；静态审计与测试共同校验；
- 结果必须包含：

```json
{
  "formal_evidence": {
    "persistence": "saved",
    "lookahead": "passed",
    "execution_timing": "passed"
  }
}
```

### 6.2 synthetic_market_stress

- `data_source` 以 `synthetic_` 开头；
- `synthetic_model` 必须包含模型名、参数、seed 和路径数；
- 至少设置一个同路径等权对照；
- 结果不得生成订单、纸面目标或生产启用；
- 结论等级只能是 `admitted_for_research`、`watch_only`、`rejected`、
  `inconclusive`、`data_gap` 或 `diagnostic_only`；
- 必须保留多条路径的分布统计，而不是只用单一路径结论。

### 6.3 statistical_screen / feasibility_probe

- 声明 `method` 和 `input_hashes`；
- 外部数据必须声明来源；
- 不得输出组合收益作为已验证结论；
- 统计筛选的多重检验策略必须显式记录。

### 6.4 attribution / derived_analysis_only

- `source_result_hashes` 至少一项；
- 不得包含新的成交/收益生成器；
- 结果必须引用来源结果哈希。

### 6.5 diagnostic_simulation

- 必须声明 `known_limits`；
- 只能发布 `diagnostic_only`、`rejected` 或 `inconclusive`；
- 不得声明 `project_formal`。

### 6.6 non_return_research

- 不得包含收益、Sharpe、回撤或策略准入结论；
- 仍需预注册与结果 sidecar，以保持研究可追溯。

## 7. 研究 runner 声明

新研究 runner 顶层必须声明：

```python
RESEARCH_TYPE = "synthetic_market_stress"
RESEARCH_PREREGISTRATION = "data/factor_research/research/r_gbm_001_preregistration.json"
```

合同审计只扫描以下路径：

- `scripts/research_*.py`
- `data/strategies/ai/.iterations/**/evidence/run_*.py`

已存在且内容哈希命中 grandfather manifest 的运行器可以没有声明；新增或内容变化的
运行器必须同时提供两个声明，否则失败。

## 8. 结果 envelope

结果 JSON 至少包含：

```json
{
  "schema_version": 1,
  "research_id": "R-GBM-001",
  "research_type": "synthetic_market_stress",
  "runner": "scripts/research_gbm_market_stress.py",
  "runner_sha256": "<64 hex>",
  "preregistration_sha256": "<64 hex>",
  "generated_at": "<ISO-8601>",
  "conclusion": "watch_only",
  "gate_results": {},
  "metrics": {},
  "source_result_hashes": [],
  "formal_evidence": null,
  "known_limits": [],
  "negative_knowledge": [],
  "output_policy": {
    "active_version": null,
    "orders_generated": false,
    "paper_target_generated": false,
    "production_enabled": false
  }
}
```

结论等级封闭为：

- `admitted_for_research`
- `watch_only`
- `rejected`
- `inconclusive`
- `data_gap`
- `diagnostic_only`

结果必须新增，不能覆盖既有路径。发布后写入同名 `.sha256`，并绑定 append-only
`ResearchLedger`。Markdown 报告必须与 JSON 同口径，并同样生成 sidecar。

## 9. 共享代码边界

新增 `backend/app/factor_research/research_contract.py`：

- Pydantic 合同模型；
- 读取并校验 sidecar；
- runner 哈希校验；
- 类型专用校验；
- 结果发布与 ledger 绑定 helper；
- 禁止执行型输出。

新增 `scripts/audit_research_contract.py`：

- 冻结/读取 grandfather manifest；
- 发现新增或变化的 runner；
- 校验声明、预注册和结果；
- 校验历史豁免只对精确 SHA 生效；
- 输出机器可读审计与 sidecar。

不修改 `StrategyBacktestService`、`BacktestEngine` 或正式回测语义。

## 10. 历史资产处理

新增 `data/factor_research/research/research_contract_grandfather_2026-11-24.json`：

- 路径；
- 冻结 SHA-256；
- `disposition=legacy_grandfathered`；
- 原因；
- 生成时间。

规则：

- 文件内容完全一致时豁免；
- 文件变化后必须补声明和预注册；
- 不得通过删除再恢复文件绕过；
- grandfather manifest 自身有 sidecar。

## 11. CI 与测试

新增单元/合约测试：

- 预注册 schema 与 runner 哈希；
- 类型专用必填字段；
- 禁止 active/订单/生产；
- 结果覆盖 fail-closed；
- 结果引用的预注册哈希匹配；
- ledger 绑定幂等且哈希变化失败；
- grandfather 精确哈希有效、变化后失败；
- 新 runner 无声明失败；
- project_formal 缺少 guard 失败；
- synthetic_market_stress 缺 seed/路径数/等权对照失败；
- CI 工作流包含合同审计步骤。

CI 在 backend job 中增加：

```bash
python scripts/audit_research_contract.py
```

并保留现有 conformance、asset ledger 与全量 pytest 门禁。

## 12. 验收标准

1. 合同规则、模型、auditor、grandfather manifest 和测试已提交；
2. 当前历史 runner 通过精确哈希豁免；
3. 新增未声明 runner、无预注册 runner、hash 漂移 runner 均被拒绝；
4. 合同生效后的新增研究只能使用封闭类型和结论等级；
5. `project_formal` 不得绕过正式证据链；
6. synthetic/diagnostic 不得发布 active 或执行型输出；
7. ledger 与全部新产物哈希一致；
8. 后端全量、前端构建、CI 和 Docker 全部通过。

## 13. 后续研究流程

1. 在 `docs/superpowers/specs/` 写研究设计；
2. 在 `docs/superpowers/plans/` 写可执行计划；
3. 写 runner 与测试；
4. 冻结预注册及其 sidecar；
5. 执行研究，使用 helper 发布结果与 ledger；
6. 运行合同审计、全量测试和 CI；
7. 只按合同等级给结论，不自动 active。

GBM 研究将作为本合同的第一个新研究用例，类型固定为
`synthetic_market_stress`。
