# R383 财务营运效率因子预注册与实施计划

## 目标

在现有 PIT 财务快照上新增两个尚未覆盖、经济含义明确且可审计的营运效率因子：

- `inventory_turnover_ttm`：高存货周转效率；
- `receivable_turnover_ttm`：高应收账款回收效率。

本轮只验证这两个因子，不把已失败的毛利率、资产周转率、现金转换周期或营运资本变化重新命名包装。

## 查重结论

- R82 已研究 `gross_profitability`、`asset_turnover`、`asset_growth_yoy`、`inventory_growth_yoy`、`receivable_growth_yoy` 和 `cash_conversion_cycle`。
- R242 已正式研究 `working_capital_change_yoy_low`，未通过。
- R244 已正式研究 `gross_profitability`，未通过。
- `asset_turnover` 与 CCC 不作为 R383 新因子重复提交。

## 冻结公式

```text
inventory_turnover_ttm =
    TTM operating_costs
    / ((inventory_t + inventory_(t-4)) / 2)

receivable_turnover_ttm =
    TTM revenue
    / ((accounts_receivable_t + accounts_receivable_(t-4)) / 2)
```

口径：

- `income.revenue`、`income.operating_costs` 是累计值，先按同财年连续季度还原单季，再滚动四季求和。
- 必须连续四个季度；任一缺失季度直接返回 null。
- 当前资产负债表项目和一年前项目必须恰好相隔四季度。
- 平均资产分母必须大于 0；TTM 分子必须大于 0。
- 不补零、不插值、不跨缺口硬拼。
- 新因子分别使用独立 PIT 生效日：当前及一年前资产负债表 PIT、四个利润表 PIT 的最大值。
- 公告日当天仍为 null，下一交易日才生效。
- `income.revenue` 与 `metrics.revenue` 重名时，只使用利润表版本计算应收周转。
- 历史数据缺少新增列时，新增因子返回 null，但原有财务因子必须继续工作。
- 行业分类使用当前行业快照，不是历史 PIT；当前 HS300 名单回填历史仍有幸存者偏差。

## 实施步骤

1. 先写失败测试，覆盖累计值季度化、四季 TTM、四季度平均资产、季度缺口、非正分子/分母、独立 PIT、公告日 T+1、旧 Parquet 缺列兼容、Polars 面板与矩阵一致。
2. 修改 `backend/app/backtest/fundamentals.py`，以可选列方式加载 `inventory`、`accounts_receivable`、`income.revenue` 和 `income.operating_costs`，不破坏旧快照。
3. 注册两个因子到 `backend/app/factors/registry.py`，只追加目录末尾。
4. 更新因子研究 taxonomy 与 P7 财务 PIT 审计方向，使新因子必须独立通过点时审计。
5. 更新黄金快照：总因子 83、virtual 52、financial/pit 13、stock 83、ETF 70；P7 verified 由 15 变为 17。
6. 运行定向测试、P7、taxonomy、catalog、P8/P6 兼容测试。
7. 新建 R383 研究脚本并固化研究证据。若新增因子未通过 P7，不得进入组合回测。

## 研究设计

- 样本：历史 HS300 成员，排除银行、非银金融。
- Horizon：5 / 20 / 60 个交易日。
- Newey-West lag：分别等于 horizon。
- 中性化：raw、industry、size、industry+size。
- 统计门槛：至少两个中性化口径方向调整后的 IC 同号且 `p <= 0.10`。
- 增量门槛：80% 基准 + 20% 候选，`p <= 0.10` 且 BH-FDR `q <= 0.10`。
- 冗余门槛：与现有因子的平均截面相关性 `< 0.90`；`0.70~0.90` 作为冗余研究警告。
- 组合必须使用真实 T+1 成交、无杠杆、固定持仓数、明确持有期和交易成本。
- 成本：2x 为正常硬门槛，3x 为严重压力测试；5x 仅记录 warning，不自动否决。
- 必须报告全样本、近一年、弱市段、滚动一年、leave-one-out/ablation、换手、最大回撤和集中度。
- 不启用生产、不生成纸面目标或订单、不设置 `active_version`。

## 验证命令

```bash
cd backend
uv run --frozen pytest tests/test_fundamental_derived.py tests/test_factor_registry.py -q
uv run --frozen pytest tests/factor_research/test_financial_pit_p7_scan.py -q
uv run --frozen pytest tests/test_factor_research_catalog.py tests/test_factor_research_taxonomy.py -q
uv run --frozen pytest tests -q
uv run --frozen ruff check app/backtest/fundamentals.py app/factors/registry.py tests/test_fundamental_derived.py tests/test_factor_registry.py
cd ..
git diff --check
```

## 完成标准

- 两个因子有独立 PIT 数值证据和完整兼容性测试。
- P7 的 17 个内置财务因子全部 verified。
- R383 研究证据同时包含正知识、负知识和剩余风险。
- 失败因子不硬塞进策略；通过因子最多形成 watch 候选，不自动生产启用。
