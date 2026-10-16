# HS300 多维度因子研究执行计划（R22–R30）

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans 按任务逐轮执行本计划。
> 本计划是**研究型计划**（不是功能开发计划）：每个 Task = 一轮实验，步骤用 `- [ ]` 勾选跟踪；
> 每轮的“测试”是**先写死门槛、后跑数**（预注册），验收物 = 预注册门槛 + 证据脚本 + 证据 JSON + 台账结论 + 提交。

**Goal:** 在已证伪方向上不重复试错的前提下，把财报、质量、成长、安全、情绪、周期、行业、宏观、组合、风控等维度系统实验一遍，用统一口径给出“通过 / 不通过”的结论；通过者升级为部署候选，不通过者记为负知识。研究结论不等于实盘承诺。

**Architecture:**
- 复用系统现有链路，不平行造轮子：因子筛选用 `/api/backtest/factor/batch`，策略验证用 `/api/backtest/strategy/run`，财务数据走 `backend/app/services/financial_sync.py` + AkShare provider（custom 路径），情绪/周期用 `data/regime_history/part.parquet`，行业用 `data/ext_data/ext_hy_ths` + `backend/app/services/industry_map.py`。
- 财务因子分两级：**先离线预筛**（Polars 面板，严格按 `announce_date` 次日生效），只有过预筛的因子才**进引擎**（`backend/app/backtest/fundamentals.py` + `backend/app/factors/registry.py`，带 pytest），再过 API 全门槛。避免为每个候选因子改引擎。
- 全部轮次继承 R2 起的判定纪律：先写门槛后跑、相位平均而非单路径、2× 成本、负结果入账。

**Tech Stack:** Python 3.11+ / uv、FastAPI（本地 3018）、Polars、AkShare provider、pytest、Polars/DuckDB 日K enriched 管线。

**Spec:** 本文件 §2 维度清单（2026-10-17 用户确认）+ `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/LEDGER.md`（R2 门槛体系、R19–R21 B80 部署候选）。

---

## 0. 全局约束（Global Constraints）

1. **点时性（PIT）**：财务因子必须按 `announce_date` 次日生效（公告多在盘后发布，保守 T+1），无数据一律为 null，**绝不填 0**。已有契约见 `backend/app/backtest/fundamentals.py` 头部注释与 `backend/tests/test_fundamental_factors.py`。
2. **无未来函数**：任一交易日只能使用当时已公开的数据；市场情绪/宽度类闸门用 T 日收盘前可得数据、T+1 开盘成交（与 R11 宽度闸门同口径）。
3. **统一判定口径**：长周期 10 个起始相位（2018-06-01 起的前 10 个交易日，终点 2026-10-14）；近一年 5 个 fresh-start 相位（2025-10-14 起的前 5 个交易日）；2× 成本 = 佣金 4bp + 滑点 10bp；2018H2 熊市段；逐年超额；与现有腿相关性。财务维度数据上限为 2021-03-31 起（AkShare provider `_MIN_HISTORY_PERIOD`），财务轮的“长窗口”= 2021-06-01 起 5 相位，不得伪称 2018 起。
4. **预注册纪律**：每轮先把门槛写进 LEDGER（标题 + 门槛表 + 采纳规则 + 纪律），再跑数；结果出来后不得修改门槛、不得事后加网格。追加 LEDGER 前必须断言：
   ```python
   s = LEDGER.read_text(encoding="utf-8")
   assert s.count("# LEDGER") == 1, "LEDGER 文件被重复粘贴过"
   assert "# R22 · " not in s, "该轮标题已存在"
   ```
5. **负结果入账**：未通过门槛的方向写入 LEDGER「负知识」小节；已注册未采用的因子 `status: retired` 并写 `description` 残留说明（参照 `data/user_data/custom_factors/uf_rev_5d.json`）。
6. **数据不提交**：`data/` 运行期数据（kline、financials、cache、preferences、current_members）不入 Git。已版本化的例外仅：`data/strategies/ai/.iterations/.../LEDGER.md`、`evidence/` 脚本与精简 JSON、`data/user_data/custom_factors/*.json`。
7. **提交纪律**：不 `git add -A`；只显式加本轮文件；提交前 `git diff --check` + 对新研究脚本 `uv run ruff check`；提交信息用 `research(rNN): ...`（引擎改动用 `feat(backtest): ...`）。
8. **幸存者偏差标注**：所有回测用“当前”HS300 名单回填历史，结论必须带此标注。
9. **HS300 成分口径**：唯一来源为官方实时接口 + 6h TTL 缓存（fail-closed），不回退固定池。
10. **单轮时间预算**：单轮脚本执行 ≤ ~40 分钟；超预算先缩窗口（先 metrics 单表、先单相位），再补齐，不允许长时间无输出的裸跑（放 tmux 并留日志）。
11. **环境**：本地服务在 tmux 会话 `hs300`（后端 3018 / 前端 3011）；研究脚本需 `TF_TOKEN`（API Token）或本地会话；脚本内相对路径、净化凭据、`compact()` 存摘要不存净值曲线。

---

## 1. 数据现状盘点（2026-10-17 实测）

| 层级 | 数据 | 路径 | 实测状态 | 用途 |
| --- | --- | --- | --- | --- |
| 一级 | 日K enriched | `data/kline_daily_enriched/date=*/part.parquet` | HS300 面板约 58.9 万行（2018-01-02 ~ 2026-10-14 本地重建过） | 量价、波动、流动性、情绪因子 |
| 一级 | 市场情绪/周期 | `data/regime_history/part.parquet` | 2129 天 × 31 列，至 2026-10-16；含 `state/score/phase/limit_up/seal_rate/up_ratio/above_ma20_pct/profit_score/speculation_score/resilience_score/trend_score/ge2_count/promo_rate` | 情绪阶段、闸门、事件研究 |
| 一级 | 行业主线 | `data/mainline_history/part.parquet` | 127 天 × 11 列（`kind/member/limit_up_count/ge2_count/max_boards/score/rank/...`），尾行 2026-08-04 | 主线/概念热度（样本短，仅近期） |
| 一级 | 行业分类 | `data/ext_data/ext_hy_ths/part.parquet` | 同花顺一级行业（`所属同花顺行业`），快照语义 | 行业中性（已有）、行业轮动 |
| 一级 | 财报 | `data/financials/` | `shares` 15,449 行；`metrics/income/balance_sheet/cash_flow` **为空** | R22 先补 |
| 一级 | 财务数据源 | preferences | `financial_data_provider=akshare`（custom 路径，绕过 TickFlow 套餐门控）；历史期间 2021-03-31 起，按报告期一次性拉全市场再按标的裁剪 | R22 同步 |
| 一级 | 因子平台 | `backend/app/factors/registry.py` | 77 个注册因子，其中 7 个 PIT 财务因子（`pb_latest/roe_latest/gross_margin_latest/net_margin_latest/revenue_yoy_latest/net_income_yoy_latest/debt_ratio_latest`） | 直接扫描 |
| 二级 | 北向/两融/龙虎榜/宏观 | 未本地化 | AkShare 有接口可试 | R27 试探并登记缺口 |
| 三级 | 新闻/研报/基金持仓/ESG | 无稳定源 | — | 不阻塞主线，登记缺口 |

**AkShare metrics 表可得列**：`eps_basic, revenue, revenue_yoy, revenue_qoq, net_income, net_income_yoy, net_income_qoq, bps, roe, gross_margin, net_margin, operating_cash_per_share, debt_to_asset_ratio`。
**income/balance/cash_flow 可映射字段**：`operating_profit, total_profit, financial_expenses, total_operating_costs`；`total_assets, total_liabilities, total_equity, cash_and_equivalents, accounts_receivable, inventory, accounts_payable`；`net_operating_cash_flow, net_investing_cash_flow, net_financing_cash_flow`。

---

## 2. 维度清单与轮次映射（全量覆盖）

| # | 维度 | 轮次 | 主要因子/数据 | 备注 |
| --- | --- | --- | --- | --- |
| 1 | 财报价值 | R22 | PB、E/P（ROE/PB 合成）、S/P、CF/P、估值分位 | 股息率缺数据 → 缺口登记 |
| 2 | 财报质量 | R23 | ROE、ROA（无）、毛利率、净利率、OCF/净利、应计项、周转率（缺） | 单季/滚动 8 期稳定性 |
| 3 | 财报成长 | R23 | 营收/净利同比、环比加速度、毛利率同比、PEAD | 公告日事件研究 |
| 4 | 财报安全 | R23 | 资产负债率、利息保障倍数、现金流/负债 | 银行/地产/非银口径单独看 |
| 5 | 动量/趋势 | R28 | 20/60/120 日、隔夜、残差动量、52 周高点距离 | 已有部分结论，只做组合增量 |
| 6 | 反转/超卖 | R28 | 5/10 日反转、RSI 变化、KDJ 差、布林位置 | R10 已证伪单腿，只做条件化 |
| 7 | 波动/尾部 | R28 | 下行波动、偏度、VaR/CVaR、波动状态 | v2 已含低波 |
| 8 | 流动性/交易结构 | R25 | 换手 z、量比、Amihud、量价相关、OBV | 低换手/Amihud 已证伪，避免重复 |
| 9 | 个股情绪/关注度 | R25 | 跳空、收盘位置、涨停基因、炸板率、放量上涨、拥挤度 | × 市场情绪交互 |
| 10 | 市场周期/情绪 | R24 | regime 31 列全量、phase 六态、涨停生态 | 阶段最优风格 + 闸门 |
| 11 | 行业/概念/主线 | R26 | 行业 RPS、行业内选股、主线强度、行业拥挤度 | 行业内中性已有基础设施 |
| 12 | 宏观/日历/事件 | R27 | 利率、PMI、CPI、M1/M2、社融、汇率、财报季、调仓、节假日 | 能拿到才做，否则缺口 |
| 13 | 外部/另类 | R27 | 北向、两融、龙虎榜、ETF 流 | 同上 |
| 14 | 组合/风控 | R28–R30 | 等权/评分权重/波动率加权、行业与市值暴露、止损、动态仓位 | 因子与风控分层调，不再混调 |

---

## 3. 统一判定口径与默认门槛模板

所有轮次默认使用下列结构；每轮 Task 内的门槛表是**该轮最终值**（预注册后不可改）。门槛只允许加严，不允许因结果放宽。

- **因子层默认门槛**（F 系列）：
  - F0 覆盖：目标窗口内截面覆盖率 ≥ 60%（财务因子 ≥ 70%），否则先修数据，不出因子结论。
  - F1 IC：月频 Spearman IC 均值 |IC| ≥ 0.03，且逐年（自然年）IC 同号 ≥ 4/5（财务轮）/ ≥ 6/8（量价轮）。
  - F2 分层：5 分组多空年化 > 0 且分组均值单调（|Spearman| ≥ 0.7）。
  - F3 成本：2× 成本下多空年化仍 > 0。
  - F4 增量：与已有交付腿（v1/v2/P2/V20）的截面相关 |ρ| < 0.7。
- **策略/组合层默认门槛**（G 系列，继承 R19–R21）：
  - G1 长周期相位平均 Sharpe ≥ 对照 - 0.05；
  - G2 相位平均 MDD 不深于对照 + 5pp；
  - G3 近一年 5 fresh-start 相位 5/5 为正且中位 ≥ +5%；
  - G4 2× 成本 Sharpe ≥ 0.90（且 ≥ 对照同口径 - 0.05）；
  - G5 逐完整年正超额 ≥ 7/8（长窗）/ ≥ 4/5（2021 起的财务窗）；
  - G6 机制门槛：与相关腿日收益相关 < 0.8，且组合日波动低于单腿；
  - G7 稳健性：相邻参数（权重 ±5pp / 阈值 ±10%）不出现断崖式翻转。
- **采纳规则**：全过门槛者中取事前指定排序指标（每轮预注册写明）写入部署建议；无全过者如实记录，保留现行 B80/P2 为默认。
- **报告项**（不参与判定但必须记录）：覆盖率、逐年 IC/收益、换手、单笔最差、行业偏斜、幸存者偏差影响。

---

## 4. 已证伪方向（禁止重复实验，只允许作为对照）

低换手因子；Amihud 非流动性；低彩票效应；低振幅；反转腿并入 v1（M1/M2）；60 日波动；20 日均成交额；市场宽度闸门（R11，不采纳）；单纯市值中性（R17，机制门槛未过）；v1+v2 各半（R8）；低风险 + 反转；行业中性单独使用（R17c，未过收益门槛，R18 仅作部署候选的组件）；权重网格超过 B80/B75（R20/R21 已定）。

---

## 5. 数据缺口登记（不阻塞主线）

股息率、ROA/ROIC、流动比率/速动比率、净负债/权益、有息负债、存货/应收周转、分析师预期与评级、新闻情绪、基金持仓、ESG、北向实时净买（2024-08 起披露口径变化）。R22–R27 遇到即登记到 LEDGER「数据缺口」小节，**不**用代理字段硬凑口径。

---

## 6. 交付物与目录约定

| 产物 | 路径 | 规则 |
| --- | --- | --- |
| 台账 | `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/LEDGER.md` | 每轮预注册 + 结果，追加不重写 |
| 证据脚本 | `.../evidence/script_r22*.py` 等 | 可复现、相对路径、`TF_TOKEN` 环境变量、不打印凭据 |
| 证据 JSON | `.../evidence/r22_*.json` | `compact()` 去掉 raw 曲线；含 `gates` 布尔字典与 `all_pass` |
| 新因子定义 | `data/user_data/custom_factors/<id>.json` | L1：引用已注册因子的合成；过预筛才 `status: active` |
| 引擎扩展 | `backend/app/backtest/fundamentals.py`、`backend/app/factors/registry.py` | L3：先加 pytest（PIT 生效、null、符号），再登记因子 |
| 策略文件 | `data/strategies/ai/*.py` | 只在候选通过全部门槛时新增/更新 docstring 部署建议 |

---

## 7. 任务分解（R22 → R30）

### Task R22：财报数据补齐 + 财务价值因子初筛

**Files:**
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r22_sync.py`
- Create: `.../evidence/script_r22_scan.py`
- Create: `.../evidence/r22_value_factors.json`
- Modify: `.../LEDGER.md`（预注册 + 结果）
- Test: `cd backend && uv run pytest tests/test_fundamental_factors.py -q`（引擎未改也要跑，证明 PIT 契约未破）

**Interfaces:**
- Consumes: `data/hs300/current_members.json`（`members[].symbol`，300 只）；`app.services.financial_sync._sync_history_table_for_symbols(table, symbols, data_dir, capset)`；`app.tickflow.capabilities.CapabilitySet`（akshare 为 custom provider，空 capset 即可绕过 TickFlow 门控）；`/api/backtest/factor/batch`。
- Produces: `data/financials/metrics/part.parquet`（列见 §1）；`r22_value_factors.json` 的 `gates/all_pass`；LEDGER「R22」两节（预注册 + 结果）。

- [ ] **Step 1: 写同步脚本**（HS300 300 只，四表 + metrics 先行）
  ```python
  # script_r22_sync.py 要点（相对路径 / settings.data_dir）
  from app.config import settings
  from app.tickflow.capabilities import CapabilitySet
  from app.services import financial_sync as fs
  members = json.loads((settings.data_dir / "hs300" / "current_members.json").read_text())["members"]
  symbols = [m["symbol"] for m in members]
  for table in ("metrics", "income", "balance_sheet", "cash_flow"):
      rows = fs._sync_history_table_for_symbols(table, symbols, settings.data_dir, CapabilitySet())
      print(table, rows, flush=True)
  ```
  说明：AkShare 按报告期拉全市场再按标的裁剪，300 只不会比全市场少网络请求；首次全量历史预计单表 3–10 分钟。
- [ ] **Step 2: 跑同步并在 tmux 留日志**
  Run: `cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r22_sync.py`
  Expected: 四表行数 > 0；`metrics` 覆盖 HS300 ≥ 280 只、每只 ≥ 8 期；空则查 `_financial_is_custom()` 与 provider 日志。
- [ ] **Step 3: 预注册 R22 门槛（结果前写死）**
  R22 在 2021-06-01 ~ 2026-10-14 窗口判定（财务数据上限），门槛：
  | # | 门槛 | 阈值 |
  | --- | --- | --- |
  | D0 | metrics 覆盖 | ≥ 280/300 只且每只 ≥ 8 期 |
  | F1 | 月频 IC | \|IC\| ≥ 0.03 且 2022–2026 逐年同号 ≥ 4/5 |
  | F2 | 5 分组多空 | 年化 > 0 且分组单调 \|Spearman\| ≥ 0.7 |
  | F3 | 2× 成本 | 多空年化 > 0 |
  | F4 | 与 v2 三腿相关 | \|ρ\| < 0.7 |
  | F5 | 价值组合增量 | PB 低 + ROE 高（各取截面 1/3）多头年化 > 纯 PB 低 |
  候选因子：7 个已注册 PIT 因子 + 合成 E/P（`roe/(100*pb)`，custom factor 引用已注册因子）+ 估值分位（离线，PB 自身 3 年分位）。
- [ ] **Step 4: 写扫描脚本并小样本预跑**
  Run: `cd backend && uv run python .../evidence/script_r22_scan.py --limit-dates 6`（先 6 个月度截面验证 PIT 与 IC 计算）
- [ ] **Step 5: 全量扫因子（API + 离线）**
  API 部分：`POST /api/backtest/factor/batch`，`factor_names=[7 个财务因子]`，`start=2021-06-01`，`end=2026-10-14`，`n_groups=5`，`rebalance=monthly`，`hs300=true`，`fees_pct=0.0004`，`slippage_bps=10`。
  离线部分：估值分位、E/P 合成、PB×ROE 组合（同一月度截面，PIT asof join，生效日 = `announce_date + 1d`）。
- [ ] **Step 6: 门槛判定并写结果**：JSON 存 `r22_value_factors.json`（`compact()` 去曲线、含 `gates/all_pass`）；LEDGER 追加「R22 结果」+ 负知识；未过因子若已建 custom factor 则 `status: retired`。
- [ ] **Step 7: 验证与提交**
  Run: `cd backend && uv run ruff check ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r22_*.py && uv run pytest tests/test_fundamental_factors.py -q`；`git diff --check`
  Commit: `git add <本轮 4 个文件> && git commit -m "research(r22): 财报数据补齐与价值因子初筛"`

**Acceptance:** D0 通过；每个候选因子有明确 gates 结论；负知识入账；`data/financials/metrics` 有效且行数稳定；无未来函数（PIT 测试绿）。

---

### Task R23：质量 / 成长 / 安全 / 稳定性组合（含 PEAD）

**Files:**
- Create: `.../evidence/script_r23_screen.py`、`.../evidence/r23_quality_growth.json`
- Modify: `.../LEDGER.md`
- Test: `backend/tests/test_fundamental_factors.py`（仅当把因子提升进引擎时扩展）

**Interfaces:**
- Consumes: R22 的 `data/financials/{metrics,income,balance_sheet,cash_flow}/part.parquet`；`industry_l1`（`app.services.industry_map.load_industry_l1`）。
- Produces: 质量/成长/安全/应计/PEAD 候选的 IC/分层结论；进入引擎的因子清单（若有）。

- [ ] **Step 1: 预注册 R23 门槛**（同 R22 F1–F4，另加：质量合成因子行业内中性化前后 IC 差 > 0.01；PEAD 事件 CAR(T+1..T+60) 中位数 > 0 且 t 统计 > 2）。
- [ ] **Step 2: 构建离线 PIT 面板**：metrics/income/balance/cash_flow 各自按 `announce_date+1d` asof join 到日频 enriched 面板；滚动 8 期稳定性（ROE/净利率 std）、同比加速度（本期 yoy − 上期 yoy）、应计（`(net_income − net_operating_cash_flow)/total_assets`）、利息保障（`total_profit / |financial_expenses|`，分母过小置 null）。
- [ ] **Step 3: 因子预筛**（月度 5 分组 + IC；银行/地产/非银单独列示）。
- [ ] **Step 4: PEAD 事件研究**：公告日为事件日，T+1 开盘进场、T+60 收盘出场，按超预期代理（净利 yoy 与 revenue yoy 分组）看 CAR 分布。
- [ ] **Step 5: 组合验证**：质量+价值（PB 低 + ROE 高 + 应计低）、成长+质量、安全边际（低负债 + 高利息保障）三组，跑 2021-06 起 5 相位 + 近一年 5 fresh-start + 2× 成本。
- [ ] **Step 6: 结果入账 + 提交**（未过门槛的因子记负知识；过 F 门槛的因子走「进引擎」流程：先加 pytest 再登记，然后 API 复验）。

**Acceptance:** 每个维度最多 3 个候选，全部有结论；引擎改动（若有）`pytest tests/test_fundamental_factors.py` 绿 + API 复验一致。

---

### Task R24：市场情绪 / 周期阶段因子与事件研究

**Files:**
- Create: `.../evidence/script_r24_regime.py`、`.../evidence/r24_regime_stats.json`
- Modify: `.../LEDGER.md`

**Interfaces:**
- Consumes: `data/regime_history/part.parquet`（31 列，T 日收盘后可得；用于 T+1 交易）；enriched 面板。
- Produces: 六阶段（ice/launch/advance/climax/ebb/repair）未来 5/20 日收益分布；各阶段最优风格；情绪闸门候选。

- [ ] **Step 1: 预注册 R24 门槛**（阶段样本 ≥ 10 段才出结论；闸门候选必须：近一年 5/5 正 + 长周期 Sharpe 劣化 ≤ 0.05 + 滚动一年最差不深于 B80 -2pp）。
- [ ] **Step 2: 阶段统计**：每阶段未来 5/20 日 HS300 中位/均值收益、胜率、波动；`score/phase` 单调性检验（严格用 T 日数据、T+1 成交）。
- [ ] **Step 3: 阶段 × 风格**：在每阶段内重跑 v2 三腿、v1 动量、反转、质量的截面 IC（离线），得出“阶段 → 风格”表。
- [ ] **Step 4: 闸门候选**（≤3 个）：情绪分/phase 开仓闸门、涨停生态闸门（`seal_rate/ge2_count/promo_rate`）、宽度闸门复核（对照 R11）。
- [ ] **Step 5: 策略回测**（5 个近一年相位 + 10 个长周期相位 + 2× 成本），按门槛判定并入账。

**Acceptance:** 阶段统计表完整；闸门候选 ≤3 且逐个有准入/否决结论；不重复 R11 已证伪的宽度闸门结论。

---

### Task R25：个股情绪 × 市场情绪交互

**Files:**
- Create: `.../evidence/script_r25_sentiment.py`、`.../evidence/r25_sentiment_interaction.json`
- Modify: `.../LEDGER.md`

**Interfaces:**
- Consumes: 已注册量价因子（`turnover_z_60d, limit_up_count_20d/60d, gap_return, intraday_return, close_position, vol_ratio_5d/10d, vol_price_corr_20d, obv_trend_20d, max_ret_20d, up_days_20d`）；R24 的 phase 序列；离线补算：个股炸板率、连板高度、异常放量。
- Produces: 情绪因子 IC 表（全样本 + 分阶段）；交互因子（情绪因子 × 阶段）结论。

- [ ] **Step 1: 预注册门槛**（情绪因子过 F1–F4；交互项必须在 ≥1 个阶段内 \|IC\| ≥ 0.03 且分组单调，否则记“无交互”）。
- [ ] **Step 2: 单因子批次扫描**（`/api/backtest/factor/batch`，月度 5 分组，2018-06-01 起）。
- [ ] **Step 3: 分阶段 IC 分解**（每阶段内截面 IC 均值），避免“全样本有效、阶段集中”的假象。
- [ ] **Step 4: 拥挤度风险检验**：高换手/高成交占比组合在情绪高潮阶段的回撤是否显著更深（分组最大回撤对比）。
- [ ] **Step 5: 结论入账 + 提交**。

**Acceptance:** 每个情绪因子有全样本 + 分阶段 IC；拥挤度风险有明确数字；不把已证伪的低换手/低振幅重复当新结论。

---

### Task R26：行业 / 主线 / 概念轮动

**Files:**
- Create: `.../evidence/script_r26_industry.py`、`.../evidence/r26_industry_rotation.json`
- Modify: `.../LEDGER.md`

**Interfaces:**
- Consumes: `industry_l1`（快照语义）、`mainline_history`（127 天，仅近期）、enriched 面板；`scoring.py` 已有行业中性的 `neutralize.industry` 契约。
- Produces: 行业 RPS/动量、行业内选股、主线和行业拥挤度的增量结论。

- [ ] **Step 1: 预注册门槛**（行业内选股相对全市场选股 Sharpe ≥ +0.05；行业倾斜不得使单行业暴露 > 25%；主线因子因样本 127 天只做报告项，不做采纳）。
- [ ] **Step 2: 构造行业日收益**（HS300 成分等权，按 `industry_l1` 聚合）、行业 RPS（20/60 日）、行业拥挤度（成交占比分位）。
- [ ] **Step 3: 行业内选股 vs 全市场选股**（同一因子、同一风控模板，2021-06 起更稳妥、附 2018 起对照）。
- [ ] **Step 4: 行业动量倾斜 + 中性化组合**（P2 加行业 RPS 倾斜 vs 仅行业中性）。
- [ ] **Step 5: 主线报告项**（`mainline_history`：主线内个股 T+1..T+5/T+20 CAR），标注 127 天样本限制。
- [ ] **Step 6: 结论入账 + 提交**。

**Acceptance:** 行业轮动有增量或明确否决；行业暴露约束有数字；快照行业偏差标注。

---

### Task R27：宏观 / 日历 / 外部数据试探

**Files:**
- Create: `.../evidence/script_r27_macro_probe.py`、`.../evidence/r27_macro_calendar.json`
- Modify: `.../LEDGER.md`（含「数据缺口」小节更新）

**Interfaces:**
- Consumes: AkShare：SHIBOR/DR007/LPR/国债收益率、PMI、CPI/PPI、M1/M2、社融、汇率；融资融券余额；龙虎榜（`stock_lhb_detail_em`）；ETF 流。北向：披露口径 2024-08 后变化，先验证可得性。
- Produces: 每个数据集“可用/不可用 + 历史长度”登记；宏观状态分层与日历效应的描述性 + 判定结论。

- [ ] **Step 1: 预注册门槛**（数据历史 < 5 年只做报告项；日历效应必须 2× 成本后仍显著且相位平均稳健，单次事件不算；多重比较用 Benjamini–Hochberg 控制 FDR 10%）。
- [ ] **Step 2: 可用性探测**：每个接口拉一次，记录字段、频率、历史起点；失败/权限不足 → 记缺口，不中断本轮。
- [ ] **Step 3: 宏观状态分层**（利率/PMI/社融分位组合）→ 各状态下 P2/v1/质量腿表现。
- [ ] **Step 4: 日历效应**（财报季、指数调仓 June/Dec、月末、长假前后 T-3..T+3）。
- [ ] **Step 5: 外部资金流**（两融/龙虎榜/ETF，若可得）：变化率因子或闸门，过门则进 R28 组合池。
- [ ] **Step 6: 结论与缺口入账 + 提交**。

**Acceptance:** 每类数据有“可用/不可用”的明确登记；无数据不伪造；任何采纳候选都有 2× 成本与相位平均证据。

---

### Task R28：跨维度组合（价值/质量/成长/动量/情绪/低风险）

**Files:**
- Create: `.../evidence/script_r28_combos.py`、`.../evidence/r28_cross_dimension_combos.json`
- Modify: `.../LEDGER.md`

**Interfaces:**
- Consumes: 通过 R22/R23 的财务腿（若有）、`script_r19.py` 的相位/组合工具（`run_phase_set/curve_metrics/blend_curve`）、P2/V20/B80 基线。
- Produces: ≤6 个预注册组合的相位级结论；升级为部署候选的清单（若有）。

- [ ] **Step 1: 预注册 6 个组合**（只做这 6 个，不事后加）：低风险+质量、低风险+价值、动量+质量、动量+情绪、低风险+质量+动量、低风险+价值+质量+情绪。
- [ ] **Step 2: 权重固定**（腿内等权；腿间先固定 50/50 与 70/30 两档；不网格化）。
- [ ] **Step 3: 长窗口判定**（量价类腿用 2018-06 起 10 相位；含财务腿的组合用 2021-06 起 5 相位 + 同窗口 B80/P2 对照）。
- [ ] **Step 4: 近一年 5 fresh-start + 2× 成本 + 逐年超额 + 相关性/边际贡献**（G1–G7）。
- [ ] **Step 5: 结论入账**：全过者写入部署建议候选；不通过者记负知识（含“哪一条腿没有边际贡献”）。

**Acceptance:** 每个组合有 G1–G7 对照表；升级候选有相邻权重 ±5pp 稳健性（G7）；不修改现行 B80 默认建议，除非新候选全过。

---

### Task R29：周期条件化与动态仓位

**Files:**
- Create: `.../evidence/script_r29_dynamic.py`、`.../evidence/r29_regime_positioning.json`
- Create/Modify（仅当候选有效）: `data/strategies/ai/ai_hs300_lowrisk_*dynamic*.py`（仿 `ai_hs300_lowrisk_gated.py`：闸门只阻止开新仓，已有持仓按原规则退出）
- Modify: `.../LEDGER.md`

**Interfaces:**
- Consumes: R24 阶段表；R28 最优组合；`/api/backtest/strategy/run`（`params` 传阈值）。
- Produces: 动态仓位/闸门/离场规则的判定结论。

- [ ] **Step 1: 预注册门槛**：滚动一年正收益率与最差窗口不得差于静态 B80；长周期 Sharpe 劣化 ≤ 0.05；2× 成本后仍全正；换手增加不超过 30%。
- [ ] **Step 2: 三类规则**（入场闸门 / 动态仓位 0–100% / 情绪转弱离场），阈值来自 R24 阶段统计而非拟合。
- [ ] **Step 3: 策略回测**（10 相位 + 5 fresh-start + 滚动一年分布 + 2× 成本）。
- [ ] **Step 4: 与静态 B80 对照**（R21 同口径），按门槛判定；通过才写策略文件并加回归测试（`tests/test_strategy_ai_*`，验证闸门只影响开仓、离场不变）。
- [ ] **Step 5: 结论入账 + 提交**。

**Acceptance:** 动态规则有明确增量或否决；若写策略文件，回归测试覆盖“闸门不改变卖出路径”。

---

### Task R30：组合构建 / 风控 / 成本 / 终审

**Files:**
- Create: `.../evidence/script_r30_final.py`、`.../evidence/r30_final_audit.json`
- Modify: `.../LEDGER.md`（终审结论）；如通过则更新策略 docstring 的部署建议段
- Test: `cd backend && uv run pytest tests -q -x --timeout=600` 或至少受影响域全量（见 §8）

**Interfaces:**
- Consumes: R28/R29 的最优候选 + 现行 B80。
- Produces: 最终采纳/不采纳结论；与风险清单。

- [ ] **Step 1: 预注册终审门槛**：R21 G1–G6 全过 + 3× 成本（佣金 6bp + 滑点 15bp）下 2 年滚动窗口仍正 + 单笔最差/最大行业暴露限额。
- [ ] **Step 2: 组合构建对照**：等权 vs 评分权重 vs 波动率目标；持仓数 10/20；最大行业暴露 25%/35%；止损 -15%/-20%/无。
- [ ] **Step 3: 成本压力**：1× / 2× / 3×；换手与容量估计（按 HS300 成交额占比）。
- [ ] **Step 4: 终审统计**：滚动一年分布、两段连续分段、逐年超额、与现有腿相关矩阵、边际贡献（剔除任一腿后的增量）。
- [ ] **Step 5: 结论入账**：写明“研究结论/未验证项/剩余风险”；更新 `ai_hs300_lowrisk_alpha.py` 与 `ai_hs300_multi_factor_rank.py` docstring 的部署建议段（仅当结论变化）。

**Acceptance:** 最终结论可复现、门槛对照完整、未过门槛项如实标注；LEDGER 是唯一权威结论来源。

---

## 8. 验证矩阵与提交规范

| 改动 | 最低验证 |
| --- | --- |
| 研究报告 / 台账 | `git diff --check`；LEDGER 断言（§0.4） |
| 研究脚本 | `cd backend && uv run ruff check <scripts>`；小样本预跑 + 全量跑通 |
| 引擎因子扩展 | `cd backend && uv run pytest tests/test_fundamental_factors.py tests/test_factor_api.py tests/test_factor_dsl.py -q` + API 复验 |
| 策略文件新增/修改 | 定向 `pytest tests/test_strategy*.py -q` + 5 相位回测复现 |
| 数据同步 | 行数/覆盖率核对 + `uv run pytest tests/test_financial*.py -q`（存在时） |
| 提交 | 不 `git add -A`；显式文件清单；`research(rNN): ...`；推送后确认 CI 绿 |

## 9. 人工复核点与剩余风险

- 财务历史仅到 2021-03-31（provider 上限）：财务轮不能声称覆盖 2018 熊市；需在结论中标注。
- 行业分类为**快照**回填历史；主线历史仅 127 天；HS300 用当前成分回填历史（幸存者偏差）。
- 组合日再平衡摩擦未计；同一 2018–2026 样本内选权重仍有选择偏差（R20/R21 已标注）。
- 宏观/外部数据可用性未验证，R27 先探测再决定。
- 所有“通过”均为研究证据下的候选，不是实盘承诺；最终需模拟盘验证。
