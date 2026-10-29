# R306 面板登记失败说明

R306 预注册文档及登记脚本均已在请求前冻结并保存 SHA-256。单次 GET 预检为 HTTP 200，面板 23 项，与 R305 完整读回的 ID/状态映射一致；R304、A13、A14、R305 保护候选均为 pending，未发现同名或同 `source_id` 候选。

按登记流程仅发出一次 POST。面板以 HTTP 400 拒绝，原文为“候选配置包含不允许的字段: gap_threshold, holding_sessions”。拒绝原始 body SHA-256 为 `c9362948358c54bcf7056cf2d6aea27ab8dd69736279fe81c9787a3234ce5d26`。随后的 GET 为 HTTP 200、仍 23 项、没有 R306 候选，既有状态未变。依据预注册的 no-retry 规则，没有第二次 POST。

该错误说明候选注册 API 的 factor config 白名单不接受这两个研究参数。它是登记契约不匹配，不是数据可行性失败，也不是 Alpha 结果。没有运行 R306 行情覆盖/事件量可行性审计，没有读取任何 R306 前瞻收益，也没有系统回测、收益扫描、生产数据写入或策略发布。必须保留原冻结预注册；若未来要重启该方向，应作为新版本/新轮次单独冻结，并将研究参数留在预注册文档或允许的 `factor_name` 描述中，而非篡改 R306 请求后重试。

R306 原始 GET/POST/GET、intent 和响应摘要分别见 `r306_panel_registration_preflight.raw.json`、`r306_panel_registration_intent.json`、`r306_panel_registration_post.raw.json`、`r306_panel_registration_post.json`、`r306_panel_registration_readback.raw.json`、`r306_panel_registration_readback.json`；各文件及 `r306_gap_retention_preregistration.json`、`register_r306_panel_once.py` 均有 SHA-256 sidecar。
