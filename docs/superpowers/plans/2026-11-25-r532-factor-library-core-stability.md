# R532 Factor Library Core Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 冻结 R531 的 6 个核心代表，验证其跨时间稳定性、滚动窗口和留一因子综合能力。

**Architecture:** 新增独立 runner，只导入 R530/R531 已验证的加载与核心构造函数；不修改历史 runner。预注册冻结输入哈希、窗口、bootstrap seed 规则和全部门槛，结果只通过不可覆盖的 research contract 发布。

**Tech Stack:** Python 3.11、Polars、现有 `parameter_mc`/`signal_scan` 统计原语、ResearchLedger、Pytest。

**Spec:** `docs/superpowers/specs/2026-11-25-r532-factor-library-core-stability-design.md`

---

### Task 1: 冻结输入链与合同声明

**Files:**
- Create: `backend/tests/test_r532_factor_library_core_stability.py`
- Create: `scripts/research_r532_factor_library_core_stability.py`

**Interfaces:**
- Produces: `load_frozen_core(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]`
- Produces: `RESEARCH_TYPE = "statistical_screen"`
- Produces: `RESEARCH_PREREGISTRATION = "data/factor_research/research/r532_factor_library_core_stability_preregistration.json"`

- [ ] 写失败测试，断言 R531 chain 可验证、恰好 6 个代表且家庭集合固定。
- [ ] 运行测试确认因 runner 缺失而失败。
- [ ] 实现声明、sidecar 校验和 R531 结果解析。
- [ ] 运行聚焦测试通过。

### Task 2: 滚动窗口和稳定性门槛

**Files:**
- Modify: `scripts/research_r532_factor_library_core_stability.py`
- Test: `backend/tests/test_r532_factor_library_core_stability.py`

**Interfaces:**
- Produces: `rolling_ic_windows(ic: pl.DataFrame, *, window: int, step: int) -> list[tuple[date, date, pl.DataFrame]]`
- Produces: `summarise_rolling_windows(...) -> dict[str, Any]`
- Produces: `core_conclusion(...) -> tuple[str, dict[str, bool], list[str]]`

- [ ] 写失败测试覆盖窗口数、中位数、正比例、最差窗口和失败原因映射。
- [ ] 实现确定性窗口切片、年度统计和冻结门槛。
- [ ] 增加测试确保少于 24 个窗口返回 `inconclusive`。
- [ ] 运行聚焦测试通过。

### Task 3: 5000 次蒙特卡洛与留一贡献

**Files:**
- Modify: `scripts/research_r532_factor_library_core_stability.py`
- Test: `backend/tests/test_r532_factor_library_core_stability.py`

**Interfaces:**
- Produces: `bootstrap_periods(gate_ic, p3) -> dict[str, dict[str, Any]]`
- Produces: `leave_one_out_diagnostics(...) -> list[dict[str, Any]]`

- [ ] 写失败测试固定同一 window/block/count 的 seed 完全一致。
- [ ] 实现 full/validation/recent 的 10/20/40 × 5000 次 bootstrap。
- [ ] 实现每个完整滚动窗口 block-20 × 5000 次 bootstrap。
- [ ] 复用 R531 核心构造，生成 6 个留一核心并只做诊断。
- [ ] 运行聚焦测试通过。

### Task 4: 预注册、发布和审计

**Files:**
- Create: `data/factor_research/research/r532_factor_library_core_stability_preregistration.json` + sidecar
- Create: `data/factor_research/research/r532_factor_library_core_stability_result.json` + sidecar
- Create: `data/factor_research/research/r532_factor_library_core_stability_result.md` + sidecar
- Modify: `data/factor_research/ledger/head.json` + sidecar
- Modify: `data/factor_research/ledger/records.jsonl` + sidecar
- Modify: `backend/tests/test_research_hygiene.py`
- Modify: `scripts/audit_research_research_assets.py`
- Create: `data/factor_research/research/research_asset_ledger_2026-11-25_v24.json` + sidecar
- Modify: `data/factor_research/research/research_conformance_audit_2026-11-23.json` + sidecar
- Modify: `data/factor_research/research/research_contract_audit_2026-11-24.json` + sidecar

- [ ] 运行 `--preflight`，确认 6 个代表和 R530 冻结参数。
- [ ] 运行 `--freeze-preregistration`，校验 sidecar 和 runner hash。
- [ ] 运行一次研究，拒绝覆盖历史文件并写入 ResearchLedger。
- [ ] 校验 result/conclusion/no-execution policy 和全部 sidecar。
- [ ] 更新资产台账及 hygiene 引用，运行 contract/conformance/hygiene/assets 审计。

### Task 5: 全量验证与提交

- [ ] 运行聚焦测试、Ruff、`git diff --check`。
- [ ] 运行后端全量测试和前端构建。
- [ ] 检查最终 diff 与磁盘占用，删除本轮临时缓存。
- [ ] 使用 `git commit -s` 提交并推送 `main`。
- [ ] 轮询 GitHub CI 与 Docker，任一失败先修复再继续。
