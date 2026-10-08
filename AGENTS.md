# AI 开发入口

修改、调试或审查本仓库前，必须完整阅读并遵循根目录的 [`CONTRIBUTING.md`](CONTRIBUTING.md)。其中定义了项目架构、数据契约、数据源插件化、缓存与性能要求、测试矩阵以及 PR 复审和合并标准。

涉及代码二次开发、前端插槽、后端可替换策略、扩展注册或上游升级兼容时，还必须阅读 [`docs/secondary-development.md`](docs/secondary-development.md)。该文档区分当前已实现能力与目标扩展契约；不得根据设计示例虚构尚不存在的 API。

## 仓库定位

- 本仓库是 HS300 upstream-derived 项目：完整应用基线来自 `shy3130/tick-stock-panel`，后续 HS300 能力必须优先复用其数据源插件、K 线、指标、因子和回测链路。
- HS300 成分不使用固定池或月度快照归档：唯一来源是中证指数官方接口（`index_stock_cons_csindex`，东方财富降级）实时解析的当前成分，缓存见 `data/hs300/current_members.json`（6 小时 TTL，fail-closed）。
- 主要结构：`backend/`（FastAPI 与数据服务）、`frontend/`（React/Vite）、`docs/`（项目文档）、`vendor/`（第三方数据与工具）。
- 当前已实现 HS300 当前成分服务（官方动态名单，非固定池）、`/api/hs300/current` 与 `/api/hs300/sync` API、AkShare 内置 provider（覆盖日K/除权/财报/股本/实时），以及筛选器/策略/因子回测对当前 HS300 名单的应用（回测整段按当前名单过滤，存在幸存者偏差）。修改时仍必须搜索真实调用链和测试；设计/计划中的其他能力不能仅凭文档假设为已实现。
- AkShare 提供股票/指数/ETF 原始日K、除权因子、财报与股本；复权仍由本地 `adj_factor` provider 和 enriched 管道统一处理。同步使用显式单次 provider 参数，不要临时覆盖全局数据源偏好。

## 常用验证命令

后端基线：

```bash
cd backend && uv sync --extra dev --frozen
cd backend && uv run pytest tests -q
```

前端基线：

```bash
cd frontend && pnpm install --frozen-lockfile
cd frontend && pnpm build
```

聚焦前端测试：

```bash
cd frontend && pnpm vitest run src/<test-file>
```

空白与补丁检查：

```bash
git diff --check
```

## 执行纪律

- 先理解调用链、数据契约和现有测试，再进行修改。
- 保持实现简单、改动范围最小，不处理无关问题。
- 不覆盖工作区已有修改，不虚构测试或审查结果。
- 不并行重写图表、指标、因子、回测、存储或数据源框架。
- 以实际验证结果作为完成标准。
