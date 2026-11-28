# 标准研究验证套件设计

> 生效日期：2026-11-28。适用范围：后续新增研究运行器，以及字节发生变化的历史研究运行器。
> 目标是把 GBM 合成行情压力测试和参数 Monte Carlo 选择从“研究脚本里的可选项”升级成统一研究门禁。

## 1. 目标

1. 每项研究必须显式声明标准验证套件是否适用，不允许静默跳过。
2. 产生策略收益或参数结论的研究必须运行完整 GBM 路径压力测试。
3. 参数搜索必须使用统一选择流程：训练期选择、验证期确认、近期/测试仅披露，不允许用近期结果选参。
4. 参数选择必须同时满足风险、成本、鲁棒性和参数平台约束，并输出图表。
5. 每个标准验证结果都必须生成不可变 JSON/Markdown/PNG、sidecar，并绑定 ResearchLedger。
6. 新研究缺少标准验证回执时，合同审计失败。

## 2. 两个标准套件

### 2.1 GBM 策略压力套件

固定标准轴：

- 4 个市场情景：neutral、bull、bear、high_volatility
- 2 个市场相关性：beta=0.0、beta=0.7
- 3 个成本档：2x、3x、5x；5x 只警告，不硬否决
- 正式研究默认 100 条路径/情景/相关性；统计初筛可使用 20 条快速路径
- 同路径等权买入持有基准
- T+1 开盘成交，无杠杆，最大敞口不超过 100%
- 输出收益、超额、Sharpe、MDD、换手、平均敞口和多分位分布

硬约束：

- 所有路径必须完成
- 同 seed 重放必须一致
- 最大敞口不得超过 100%
- 2x/3x/5x 成本不得出现非预期倒挂
- 必须存在同路径等权基准
- 必须生成至少两张 PNG：路径分布图、约束/成本对比图

诊断约束：

- 中位超额收益
- p05 超额收益
- 中位 MDD
- p95 MDD
- 正超额比例
- 换手率

这些诊断项用于预警，不把合成 GBM 的 alpha 结果冒充真实因子有效性。

### 2.2 参数 Monte Carlo 套件

标准流程：

1. 冻结参数网格，不允许运行中扩展。
2. 用训练期评估每个参数点。
3. 只用训练期通过硬约束的参数点进入验证期。
4. 选择规则固定为：
   - 先满足交易数、覆盖率、MDD、成本单调性和参数平台约束
   - 再按 min(train robust score, validation robust score) 排序
   - 再按验证收益、Sharpe、MDD 和参数靠近冻结中心的顺序打破平局
5. 近期/测试结果只披露，不参与选择。
6. 对选中候选的真实权益曲线执行确定性 moving-block bootstrap，输出分位区间，并记录 block size、重采样路径数和 seed。
7. 输出参数曲线图、收益-MDD Pareto 图、bootstrap 分布图。

硬约束：

- 网格和 seed 必须预注册
- 参数点必须完整
- 训练与验证选择口径固定
- 至少 20 个交易或项目既有最低交易门槛
- 3x 成本下不能出现非预期失效
- 参数平台不能只靠单点最优
- Bootstrap 必须来自真实权益曲线；用收益和 MDD 派生出的伪区间不得进入回执
- 结果必须记录未选参数及负知识

## 3. 标准验证回执

结果 JSON 的 `metrics.standard_validation` 固定包含：

```json
{
  "schema_version": 1,
  "suite_version": "v1",
  "gbm": {
    "status": "complete",
    "path_count": 100,
    "scenarios": 4,
    "cost_multipliers": [2, 3, 5],
    "constraints": {},
    "summary": {},
    "artifacts": []
  },
  "parameter_mc": {
    "status": "complete|fixed_frozen|not_applicable",
    "selection_policy": "train_select_validation_confirm",
    "parameters": [],
    "selected": {},
    "constraints": {},
    "artifacts": []
  }
}
```

状态规则：

- `project_formal`：GBM 必须为 `complete`；参数若冻结且无搜索，`parameter_mc.status=fixed_frozen`，仍需提供参数邻域或种子分布证据。
- `statistical_screen`：若调参，参数 MC 必须为 `complete`；纯统计筛选可声明 `not_applicable` 并记录原因。
- `synthetic_market_stress`：GBM 必须为 `complete`。
- `attribution`、`feasibility_probe`、`non_return_research`：必须显式声明 `not_applicable` 原因；不得生成交易收益结论。

## 4. 图表与证据

标准套件输出：

- `*_gbm_distribution.png`
- `*_gbm_constraints.png`
- `*_parameter_paths.png`
- `*_parameter_pareto.png`
- `*_parameter_bootstrap.png`

每个 PNG 与同名 `.sha256` 进入结果附件和 ResearchLedger。图表使用 PIL 绘制，不新增运行依赖。

## 5. 代码边界

- `backend/app/backtest/synthetic_gbm.py`：路径生成、固定信号矩阵适配、项目撮合执行。
- `backend/app/factor_research/standard_validation.py`：标准回执模型、约束检查、GBM/参数 MC 汇总、图表输出。
- `backend/app/factor_research/parameter_mc.py`：保留统计原语；标准选择流程在新模块中组合，不复制计算。
- `backend/app/factor_research/research_contract.py`：增加项目正式研究的标准验证类型约束。
- `scripts/audit_research_contract.py`：新研究缺少标准回执时 fail-closed。
- 历史研究按 grandfather hash 保持可审计，不追溯伪造回执。

## 6. 验收

- 标准验证 schema、状态和约束有单元测试。
- GBM 固定信号矩阵 T+1、无杠杆、路径完整性、同路径基准有测试。
- 参数选择禁止近期样本参与有测试。
- PNG 生成和 sidecar 哈希有测试。
- R550 完成首次策略级 GBM 压力并发布图表。
- R550 完成首次标准参数 Monte Carlo 邻域筛选并发布图表。
- 全量后端、前端构建、研究审计、CI、Docker 全部通过。
