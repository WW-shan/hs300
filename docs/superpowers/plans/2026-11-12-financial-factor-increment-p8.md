# 财务因子增量准入 P8 实施计划
> **Execution note (2026-11-12):** P8 已完成真实运行。11 个 P7 已验证内置财务因子均通过 P6 准入，但在当前 5 日边际 IC 门槛下 `statistical_pass_count=0`，因此没有财务候选进入组合，也没有创建新 watch 版本。Research ID 为 `sha256:448cbd37b4faefc94025243be86b496779fcf649273d2e15b1a1835a42874038`，JSON/Markdown SHA 分别为 `sha256:455f47d61e0e708b917d3fe44a28d11896f8b5568c145b469729b1498a82fa9a` 和 `sha256:9b519d5f81764d2b162d38ea6b21866670468eac6b652a8662b3d847e2a116d1`。P6 重新绑定 P8 后 Research ID 为 `sha256:9c326835be610e91cc15c21ed2b1ac0bdf2cd8b6bccf5da34e0e83da3083ac09`，`portfolio_ready_factor_count=164`、`pending_task_count=0`、`blocked_task_count=0`，`active_version=null`。
> P8 提交为 `5f230f4e`，GitHub CI 与 Docker 均成功；P8 后只读诊断记录在本文末，不改变 P8 的 no-watch 决策。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 P7 已验证的 11 个内置财务因子接入现有 P5 增量研究链路，先做单因子与边际 IC，再只让统计通过者进入 T+1 组合、拆解和滚动一年验收；最终仍然不激活策略。

**Architecture:** 新增一个 P8 研究脚本，直接复用 P5 的当前组合、marginal IC、相关性、T+1 矩阵回测、组合容差、ablation 和 rolling-year 函数；财务数据只通过 `load_fundamental_snapshot` 与 `attach_fundamental_factors` 进入面板。P7 的 `verified_factor_ids` 是唯一财务准入来源，P6 的 `portfolio_ready` 是第二道 fail-closed 门。产物写为不可变 JSON/Markdown 并绑定 ResearchLedger，结果只可能形成 watch 版本。

**Tech Stack:** Python 3.11、Polars、现有 `app.backtest.strategy`、`app.factor_research`、pytest、JSON/Markdown evidence、ResearchLedger。

**Spec:** `docs/superpowers/plans/2026-11-11-financial-pit-admission-p7.md`

## Global Constraints

- 不复制 P5 的 marginal IC、回测、ablation 或 rolling-year 实现；通过兄弟脚本加载复用。
- 只有 P7 `verified_factor_ids` 中的内置财务因子可进入 P8 候选。
- 所有候选必须先通过单因子/边际统计预检，才能跑真实组合；不得直接塞入 watch。
- 2x 是正常基准，3x 是硬压力；5x 只产生 warning，不能单独硬拒绝；不使用杠杆。
- 组合最多 8 个因子，20 仓等权，`open_t+1`，`max_exposure_pct=1.0`。
- `active_version` 始终为 null；不生成纸面目标、订单或生产启用状态。
- HS300 当前成分回填历史存在幸存者偏差，所有报告必须保留。
- 新文件先写失败测试，再实现；不修改或提交用户既有未提交研究文件。
- 精确暂存本任务文件，禁止 `git add -A`。

---

### Task 1: P8 候选与 PIT/P6 双重准入

**Files:**
- Test: `backend/tests/factor_research/test_financial_increment_p8_scan.py`
- Create: `scripts/research_financial_increment_p8.py`

**Interfaces:**
- Consumes: `BUILTIN_FINANCIAL_FACTOR_IDS`、`BUILTIN_FINANCIAL_DIRECTIONS`、P7 `latest.json` 的 run JSON、`assert_portfolio_candidates_admitted`。
- Produces: `financial_candidates(audit: Mapping[str, Any]) -> list[dict[str, Any]]`
- Produces: `load_verified_audit(data_dir: Path) -> dict[str, Any]`
- Produces: `load_financial_panel(data_dir: Path, symbols: list[str], factor_ids: list[str]) -> tuple[pl.DataFrame, dict[str, Any]]`

- [x] **Step 1: 写失败测试**

```python
def test_financial_candidates_require_p7_verified_ids():
    audit = {"verified_factor_ids": ["pb_latest"], "factors": {"pb_latest": {"status": "verified"}}}
    candidates = module.financial_candidates(audit)
    assert [item["factor_id"] for item in candidates] == ["pb_latest"]

def test_financial_candidates_fail_closed_on_unverified_id():
    with pytest.raises(ValueError, match="not P7-verified"):
        module.financial_candidates({"verified_factor_ids": [], "factors": {}})
```

- [x] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_increment_p8_scan.py -q`
Expected: FAIL because the P8 module does not exist.

- [x] **Step 3: 实现最小候选和审计读取**

候选使用固定 11 个内置因子及 P7 方向；P7 latest 必须与 run JSON 的 research ID 一致，且每个候选都在 `verified_factor_ids` 中。任何缺失都抛错，不降级。

- [x] **Step 4: 跑测试**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_increment_p8_scan.py -q`
Expected: PASS。

---

### Task 2: 复用 P5 的统计与组合链路

**Files:**
- Modify: `scripts/research_financial_increment_p8.py`
- Test: `backend/tests/factor_research/test_financial_increment_p8_scan.py`

**Interfaces:**
- Consumes: P5 `load_candidate_panel`、`score_candidates`、`_run_candidate_screen`、`_loo_contributions`、`_run_ablation`、`_ablation_candidate_gate`、`_rolling_comparison`、`_prepare_shared_window`、`_run_prepared`、`_equal_inputs`、`_current_set`、`_current_inputs`。
- Produces: `run_financial_research(data_dir: Path, *, stats_only: bool = False) -> dict[str, Any]`
- Produces: `main() -> int`

- [x] **Step 1: 写失败测试**

```python
def test_p8_does_not_run_portfolio_before_statistical_pass(monkeypatch):
    calls = []
    monkeypatch.setattr(module.p5, "_run_candidate_screen", lambda *a, **k: calls.append("screen") or {})
    result = module.run_ablation_if_passing([], {}, {}, {}, {})
    assert result == (None, None)
    assert calls == []
```

- [x] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_increment_p8_scan.py -q`
Expected: FAIL with missing `run_ablation_if_passing`。

- [x] **Step 3: 实现统计预检与组合顺序**

先构造财务日频面板，调用 P5 的 `score_candidates` 得到边际 IC/相关性；只把 `statistical_gate.pass` 且 P6 portfolio-ready 的候选交给 P5 的真实组合筛选。没有统计通过者时只跑基线并输出 `no_statistical_candidate`，不虚构组合结果。

- [x] **Step 4: 跑测试**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_increment_p8_scan.py -q`
Expected: PASS。

---

### Task 3: 组合、拆解、滚动一年和 watch 版本

**Files:**
- Modify: `scripts/research_financial_increment_p8.py`
- Test: `backend/tests/factor_research/test_financial_increment_p8_scan.py`

**Interfaces:**
- Produces: `select_final_set(candidates, screen_results, current_ids, current_directions, weakest_factor) -> tuple[list[str] | None, dict[str, str]]`
- Produces: `ensure_p8_watch_version(...) -> tuple[Any, Any, bool]`
- Consumes: P5 `_ensure_watch_version` semantics, but P8-owned evidence/version rationale.

- [x] **Step 1: 写失败测试**

```python
def test_select_final_set_never_exceeds_eight_members():
    ids, directions = module.select_final_set(
        candidates,
        screen_results,
        current_ids=[f"f{i}" for i in range(8)],
        current_directions={f"f{i}": "high" for i in range(8)},
        weakest_factor="f7",
    )
    assert ids is None or len(ids) <= 8
```

- [x] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_increment_p8_scan.py -q`
Expected: FAIL because the function is missing.

- [x] **Step 3: 实现组合验收**

统计通过且组合门槛通过后，按边际 IC 排序生成最多 8 个因子的等权集合；跑 P5 ablation，至少一个窗口有正边际且两个窗口拖累不超过 0.5pp；跑滚动一年，正收益窗口比例不低于基线且中位收益不差超过 1pp。全部通过才创建 watch 版本，`active_version` 仍为 null。

- [x] **Step 4: 跑测试**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_increment_p8_scan.py -q`
Expected: PASS。

---

### Task 4: 不可变产物、Ledger 与 P6 接线

**Files:**
- Create: `data/factor_research/research/p8_financial_increment_2026-11-12.json`
- Create: `data/factor_research/research/p8_financial_increment_2026-11-12.md`
- Modify: `scripts/research_factor_automation_p6.py`
- Modify: `backend/tests/factor_research/test_automation_p6_scan.py`

- [x] **Step 1: 写失败测试**

```python
def test_published_p8_result_keeps_active_version_null():
    payload = json.loads(P8_RESULT.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["orders_generated"] is False

def test_p6_declares_p8_research_binding():
    assert "p8" in [item["name"] for item in _latest_payload()[1]["ledger_bindings"]]
```

- [x] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_increment_p8_scan.py tests/factor_research/test_automation_p6_scan.py -q`
Expected: FAIL because P8 evidence and P6 binding are absent.

- [x] **Step 3: 实现发布与 P6 接线**

P8 研究 ID 只由方法版本、P7/P5/P6 指纹、基础 watch、输入 manifest、候选规格、参数和最终因子集合决定；同输入重跑必须不改变 JSON、Markdown、Ledger 和 latest 指针。P6 的 `RESEARCH_PATHS` 增加 P8 声明，P6 报告增加 p8 Research ID。

- [x] **Step 4: 跑测试**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_increment_p8_scan.py tests/factor_research/test_automation_p6_scan.py -q`
Expected: PASS。

---

### Task 5: 真实数据运行与验收

**Files:**
- Modify: `data/factor_research/research/p8_financial_increment_2026-11-12.json|md`
- Modify: `data/factor_research/automation/latest.json` and generated run files
- Modify: `data/factor_research/ledger/*`

- [x] **Step 1: 统计预检**

Run: `cd backend && uv run --frozen python ../scripts/research_financial_increment_p8.py --stats-only`
Expected: 11 个已验证财务因子的边际 IC、相关性、覆盖率和门槛原因；不写研究产物。

- [x] **Step 2: 完整研究**

Run: `cd backend && uv run --frozen python ../scripts/research_financial_increment_p8.py`
Expected: 只对统计通过者跑组合；没有通过者时明确 `no_statistical_candidate`，`active_version=null`。

- [x] **Step 3: P6 重跑**

Run: `cd backend && uv run --frozen python ../scripts/research_factor_automation_p6.py`
Expected: P6 绑定 P8 证据；财务因子仍 `portfolio_ready`，`active_version=null`。

- [x] **Step 4: 幂等与全量验证**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_increment_p8_scan.py tests/factor_research/test_automation_p6_scan.py -q`
Run: `cd backend && uv run --frozen pytest tests -q`
Run: `cd backend && uv run --frozen ruff check scripts/research_financial_increment_p8.py tests/factor_research/test_financial_increment_p8_scan.py`
Run: `git diff --check`
Expected: 全部通过；重跑产物指纹不变。

---

### Task 6: 精确提交、推送与 CI

**Files:**
- Stage only P8 plan/script/test and generated P8/P6/Ledger evidence.

- [x] **Step 1: 检查 diff 与工作区**
Run: `git status --short --branch && git diff --check`
- [x] **Step 2: 精确暂存并提交**
Run: `git add <P8 files> && git commit -m "feat: add financial factor incremental research P8"`
- [x] **Step 3: 推送并等待 CI/Docker**
Run: `git push origin main`
Expected: GitHub CI 与 Docker 均成功；失败则按日志修复后重跑。

---

## P8 后诊断：多周期与中性化

P8 的正式研究仍以 5 日边际 IC 为唯一准入，结论不变。为避免把“5 日无效”误写成“财务因子完全无效”，另做了只读诊断，结果只作为 P9 的假设来源，不改变 P8 决策。

### 单因子 IC

- `pb_latest`（低 PB）在 60 日原始/行业/规模/行业+规模口径下 IC 分别约为 `0.1161/0.0754/0.1120/0.0587`，同号且稳健。
- `ocf_ni` 在 60 日原始 IC 约 `0.0595`、NW t 约 `4.17`；行业+规模后 IC 约 `0.0200`，显著性明显下降。
- `accrual`（低应计）在 60 日行业+规模 IC 约 `0.0400`、NW t 约 `4.02`。
- 成长类 `revenue_yoy_latest`、`net_income_yoy_latest` 和 `roe_latest` 在多数周期为负，不支持直接加入当前组合。

### 组合反事实

| 方案 | 全周期总收益 | 近一年总收益 | 弱市一年 | 结论 |
| --- | ---: | ---: | ---: | --- |
| 当前 8 腿基线 | 92.73% | 8.44% | -4.11% | 对照 |
| 7 腿（删除股东结构） | 93.99% | 10.73% | -6.53% | 近一年改善、弱市恶化 |
| 7 腿 + 中性化 PB | 74.14% | 12.92% | 未通过全周期 | 近一年改善、全周期明显变差 |
| 7 腿 + 弱市 PB 条件腿 | 91.60% | 11.14% | -7.18% | 组合门通过但拆解拖累 -2.39pp，拒绝 |
| 7 腿 + 弱市股东/非弱市价值切换 | 73.30% | 12.31% | -7.19% | 全周期明显变差，拒绝 |
| 条件腿权重 15% | 93.51% | 11.05% | 未通过 MDD/弱市 | MDD -14.77%，稳定性不足 |

结论：**没有出现同时改善全周期、近一年和弱市一年、且拆解为正的财务组合**。这说明下一步不应继续做“单因子塞入”，而应做 P9 的期限分层与 regime 条件化组合；任何新候选必须同时通过全周期、近一年、弱市段、3x 成本、ablation 和滚动一年。

### 验收状态

- GitHub CI：`https://github.com/WW-shan/hs300/actions/runs/43061945704`，成功。
- Docker：`https://github.com/WW-shan/hs300/actions/runs/43061946010`，成功。
- 本地后端全量测试：`3353 passed, 7 skipped, 1 xfailed`。
- P8/P6 幂等：通过。
- `active_version`：null；不生成纸面目标、订单或生产启用。
