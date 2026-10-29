# R298 证据索引

本轮结论为未通过预注册收益门槛。候选在 2022-05-05 至 2023-04-28（243 个交易日）收益 -21.51%，匹配对照收益 -7.25%；候选 4x/5x 成本收益 -23.17%/-24.78%。首版报告的集中度金额百分比格式有误，已保留首版并新增口径更正和 v2 报告。R257 继续 `research-only`，生产继续 `pause_production`。

| 文件 | SHA-256 |
| --- | --- |
| `r298_preregistration.json` | `cb7b6e39c8274d5a4f3e8c04d3ece4f17cc298dadb0bf93ebb01bdedbcf35838` |
| `r298_input_manifest.jsonl` | `3258acc6cf8288d806911259f6345bb86535224110d92e54a26d6f9a5992bb4e` |
| `r298_manifest_execution_addendum.json` | `a7d92c94124f9c3781524adca39cf98501c359c4870c85e343a3235bc98f73d8` |
| `r298_temporal_holdout_backtest.json` | `2b981c03f8b47528cffa0b82ed6d03b5d7dcf9ed6bc32742268faea6bc7b6810` |
| `r298_result.md` (首版，保留) | `2eb9766e1837cd71b8ccc7396839fa7b5f15079c06a4485020db1a69e70e0633` |
| `r298_concentration_semantics_erratum.json` | `a18abfbd2790b4663750b449d7f6eccbd7f9e55115c278a2546e769c588cef27` |
| `r298_result_v2.md` | `d4ec8a74bea5b95c4bf111c7f17c489d107183175e78e4feec5802ae4134e05e` |
| `r298_final_integrity_audit.json` | `8e288e7409192e86e058b1c239e4e762e86ac98a997b9cb7d15190b7365393ca` |
| `r298_panel_final_readback.json` | `aa61f0c4bf09669f21dd6d054e02a443f8dc17abed884639050f30f04bc905f1` |

最终面板 GET 为 HTTP 200，候选共 15 条；R298 仅匹配一条，ID `01aae1c6f0314612bec472bdd3140a69`、状态 `pending`；R288–R297 十条保护候选均保持 `pending`。最终冻结输入复核为 10,710/10,713 项原样一致，3 项执行脚本例外全部与 addendum 相符，无其他差异。表列证据均有对应 `.sha256` sidecar。
