# 全库增量筛选 P5 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 汇总 P1-P4 的参数稳定推荐，去除同一经济含义的重复变体，对剩余候选执行与 `multi_dimension_v1` 的边际 IC、相关性、真实 T+1 组合回测和拆解；只有全部增量门槛通过时才建立新的 watch 版本，`active_version` 始终为 null。

**Architecture:** P5 先把推荐参数写入独立研究目录并运行时物化，或明确标记为未注册事件变体，再按预注册规则去重。研究定义不进入全局因子注册表。边际 IC 沿用日频截面 Rank IC、20% 候选权重和 Newey-West 检验；组合回测复用 `StrategyBacktestService` 的 `open_t+1`、100% 最大仓位、无杠杆路径。最终集合不超过现有研究策略 8 个因子的容量。

**Tech Stack:** Python 3.11、Pydantic/Polars、现有因子 DSL、StrategyBacktestService、FactorSetStore、ResearchLedger、pytest、JSON/Markdown evidence。

**Spec:** `docs/superpowers/specs/2026-11-11-factor-parameter-screening-monte-carlo-design.md`

> **Execution note (2026-11-11):** P5 已完成。9 个 DSL 研究定义放在
> `data/factor_research/candidates/p5`，只做运行时直接物化，不进入全局因子注册表，
> 避免污染 223 因子 catalog。13 个推荐候选按经济含义去重为 12 个，
> 其中 `vol_of_vol_20d_low` 已存在于基线集合。只有 `amount_mean_5` 通过边际 IC、
> FDR 和相关性门槛；其真实 T+1 组合全周期 Sharpe 从 1.05 降至 0.96，弱窗口从
> -4.11% 降至 -7.26%，因此被 `portfolio_rejected`。最终不创建新 watch 版本，
> `multi_dimension_v1` 保持 v1 watch，`active_version=null`。Research ID 为
> `sha256:172e4378a449778e692d180761c11fcde237651d54219d7e8c94cd1e03dc818e`，
> JSON/Markdown SHA 分别为
> `sha256:5b5de9840b99caead971bd6ae9391f3b67622d0bb1d203c00f28c8e6498797c8` 和
> `sha256:3e7cd1627d4953430014f59446c3229eb447f455857dfebaa21e71a2f4da502a`；
> 幂等重跑保持 research ID、输出 SHA 和 ledger 记录数不变。
> 同时修复 catalog 动态因子加载把成功 ID 误记为错误的问题；新 snapshot 为
> `sha256:ea0642e2c74326752bc109dcc61d72abe7f65776e107d42654f6b865e30dd79a`，
> 223 个因子、`source_completeness=complete`、`load_errors=[]`。
> 干净检出下的扩展依赖编译问题已用临时占位修复并通过 CI；最终通过提交为
> `3bd26de6`，CI 与 Docker 均成功。

> **Revalidation note (2026-11-12):** P7 财务 PIT 验证重建了 catalog，因此 P5 在
> `sha256:6cb4e69da0a5a3097459e32ddd662979c4602f7327c3b737c4130de147b9b3f6`
> 上重跑。新 Research ID 为
> `sha256:641f0df5849264ea133ea0a8ccb58b057816afd47d916014f0d5baabb93cb6c7`，
> 13 个候选、2 个经济含义去重、`portfolio_pass=[]`，结论仍为
> `no_new_watch_version`，`multi_dimension_v1` 保持 v1 watch、`active_version=null`。
> 同时修复了 P5 相对输出路径含 `..` 时计算成功但 ledger 路径投影失败的问题。

## Global Constraints

- 全周期 `2021-06-01..2026-11-05`，近一年 `2025-10-14..2026-11-05`。
- 基准集合固定为 `multi_dimension_v1` watch v1；不得修改 v1。
- 候选只能来自 P1-P4 的 `recommended` 结论，不根据增量结果回头补参数。
- 同一经济含义只保留一个代表：先最大化 `min(train IC, test IC)`，再最大化决赛主 block CI，最后优先更简单的变换。
- 边际 IC：现有组合或替换槽位组合 80% + 候选 20%，NW lag=5；要求方向增量为正、`p <= 0.10` 且族间 BH-FDR `q <= 0.10`。
- 相关性：候选与现有非替换成员的日截面 Spearman 最大绝对值不得超过 0.90。
- 组合：20 仓等权、`open_t+1`、无杠杆、2x 为基准、3x 为硬压力；不允许使用 5x 作为拒绝门槛。
- 组合不恶化采用正常容差：年化/近一年收益和 Sharpe 回撤不超过 1 个百分点、最大回撤不超过 2 个百分点，并额外要求全周期与近一年不能同时恶化。
- 每个被拒绝候选必须写入原因；高相关、边际 IC 不显著或组合恶化都不能靠凑数进入 watch。
- 新版本最多 8 个成员；先应用通过的同义替换，再加入排名最高的新经济含义候选；组合后的最终版本必须再次通过回测门槛。
- 对最终候选集执行 leave-one-out 和 single-factor 拆解。
- 只有最终组合和个人增量门槛都通过时才创建新 watch 版本；否则保留 v1 并记录负知识。
- `active_version` 保持 null，不生成纸面目标或订单。

## Candidate Families

| 来源 | 经济含义 | 代表参数 | 处理 |
| --- | --- | --- | --- |
| P1 | 残差波动 | 20 日 | 已注册因子，替换 40 日版本 |
| P1 | Corwin-Schultz 价差 | 60 日 | 已注册因子，新增 |
| P1 | 波动率稳定性 | 20 日 | 与现有成员相同，跳过重复测试 |
| P2 | 年化波动 | 10 日 | 研究目录 draft 定义 |
| P2 | ATR/价格 | 5 日 | 研究目录 draft 定义 |
| P2 | 布林带宽 | 10 日 | 研究目录 draft 定义 |
| P2 | Amihud 非流动性 | 120 日 | 研究目录 draft 定义 |
| P2 | 成交额水平 | 5 日 | 研究目录 draft 定义 |
| P2 | 换手率水平 | 5 日 | 研究目录 draft 定义 |
| P2 | 最大单日涨幅 | 60 日 | 研究目录 draft 定义 |
| P3/P4 | 回购事件 | P3 线性衰减 20 日 | 与 P4 常值持有 20 日去重；当前无法 DSL 登记，保留为研究变体 |
| P4 | 股息率 | 绝对阈值 0.015 | 与分位阈值 0.40 去重；研究目录 draft 定义 |
| P4 | 融资变化 | 分位阈值 0.50 | 替换现有融资变化腿；研究目录 draft 定义 |

## Tasks

### Task 1: 增量统计纯函数

**Files:**
- Create: `backend/app/factor_research/incremental.py`
- Create: `backend/tests/factor_research/test_incremental.py`

- [x] 实现日截面 rank score、组合分数、逐日 Spearman 相关性、20% 边际 IC、NW/BH-FDR 和组合门槛。
- [x] 覆盖空截面、最低样本、低方向、相关性拒绝、边际 IC 符号和正常容差边界。

### Task 2: 推荐候选去重与研究因子物化

**Files:**
- Create: `scripts/research_factor_increment_p5.py`
- Create: `backend/tests/factor_research/test_increment_p5_scan.py`
- Create: 9 个 `data/factor_research/candidates/p5/uf_p5_*.json`

- [x] 从 P1-P4 evidence 提取推荐并校验参数与预期一致。
- [x] 对回购和股息率执行经济含义去重，记录被放弃的替代变体及原因。
- [x] 生成 Java/Python 无关的 DSL 定义，所有研究因子保持 `draft`。
- [x] 研究定义只在运行时物化，不加载进全局因子注册表，防止污染正式 catalog。
- [x] 对未注册的回购参数变体 fail-closed 标记 `registration_required`，不得伪装成已注册因子。

### Task 3: 边际 IC、相关性与候选排序

**Files:**
- Modify: `scripts/research_factor_increment_p5.py`

- [x] 一次性加载价格、行业、规模、现有扩展因子和候选依赖。
- [x] 计算候选与现有成员的截面相关性矩阵。
- [x] 计算逐候选边际 IC、NW p 和 BH-FDR q。
- [x] 记录通过者和每一条拒绝原因。

### Task 4: 真实 T+1 组合增量

**Files:**
- Modify: `scripts/research_factor_increment_p5.py`

- [x] 跑 v1 基准及每个现有成员的 leave-one-out，识别最弱替换槽位。
- [x] 对通过统计数据门槛的已注册候选跑 full/recent 2x 和 full 3x。
- [x] 每个新经济含义候选替换最弱现有成员，同义候选替换对应成员。
- [x] 组合通过的替换和排名最高的新候选，重跑最终组合门槛。
- [x] 失败时回退到排名最高的单个通过候选；仍失败则保留 v1。

### Task 5: 最终拆解、watch 版本与证据发布

**Files:**
- Modify: `scripts/research_factor_increment_p5.py`
- Create: `data/factor_research/research/p5_incremental_2026-11-11.json`
- Create: `data/factor_research/research/p5_incremental_2026-11-11.md`

- [x] 对最终版本执行 full/recent/弱窗口 leave-one-out 和 single-factor；本轮没有候选通过组合门槛，拆解在最终组合生成前即按规则终止并记录负知识。
- [x] 只有最终组合与拆解门槛通过才创建 `multi_dimension_v1` v2 watch；本轮未创建，`active_version=null`。
- [x] 发布不可变 JSON/Markdown 证据并绑定 ResearchLedger。
- [x] 幂等重跑保持 research ID、输出 SHA、台账和 set head 不变。

### Task 6: 全链路验收

- [x] 定向测试、后端全量、改动文件 Ruff、前端构建和 `git diff --check`。
- [x] 干净 `linux/amd64` Python 3.12 全量测试（GitHub `ubuntu-latest` + Python 3.12）。
- [x] 精确提交 P5 文件，不触碰既有 R359-R371、custom factors/signals、周报和 `.gitignore`。
- [x] GitHub CI 与 Docker 均成功：
  CI `https://github.com/WW-shan/hs300/actions/runs/43036112466`；
  Docker `https://github.com/WW-shan/hs300/actions/runs/43036112561`。

## Self-Review

- P5 只使用 P1-P4 已发布推荐，不引入结果驱动的新参数。
- 重复经济含义、高相关、统计不显著和组合恶化均有明确拒绝路径。
- 新 watch 版本最多 8 个成员并保持 `active_version=null`。
- 参数变体只有在真实注册后才允许进入 watch；未注册事件变体不会绕过引用校验。
