# R301 轮次索引

- 日期：2026-10-29
- 研究方向：营收增长减应收账款增长（`collection_quality_spread`）可行性
- 结论：预注册门槛 F1 失败，停止，不进入收益筛选或策略回测。
- 关键结果：全样本覆盖 87.52%；发现期 92.54%；验证期 80.33%；最近 252 个交易日 75.61%。有有效值的日期占比依次为 93.16%、93.10%、93.24%、97.22%。F2、F3、F4 通过，但不能抵消 F1 失败。
- 机器结果：`r301_feasibility_result.json`，SHA-256 `607a4d862bfb687dda16d71a380810346b2c122aa014126a11d52425a7ebf622`。
- 可读报告：`r301_result.md`，SHA-256 见 `r301_result.md.sha256`。
- 冻结输入：`r301_input_manifest.jsonl`，SHA-256 `f096ee66de45e63131f6f141739225a72000e245d4278d7116ef0f937edff058`。
- 面板：候选 `f75cfc9b919c4b769bd374765ea7dab3`，唯一一次登记后已读回，仍为 `pending`、指标为空。
- 边界：未读取 R301 前向收益或策略/交易结果；未跑收益筛选、回测、provider 或生产 builder；候选不构成 alpha，生产保持 `pause_production`。本轮属于已披露既有 R82、R242、R248 结果背景的可行性审计，不是盲审。
- 首次执行因 M1-M5 amendment 路径解包错误而在数据读取前失败。失败记录为 `r301_attempt_1_failure.json`；M6 追加修复保留旧 manifest 字节，当前清单哈希由 M6 校验。
- 计算方法和限制见 `r301_result.md`、`r301_feasibility_result.json`、`r301_preanalysis_deviation.json`。
