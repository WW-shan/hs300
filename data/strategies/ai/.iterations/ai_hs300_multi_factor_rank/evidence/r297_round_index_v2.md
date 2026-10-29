# R297 最终证据索引

权威可读结论见 r297_result_v5.md，机器证据见 r297_final_audit_v5.json。R297 是成分覆盖与装配反事实审计，不是 alpha 检验。

| 文件 | 用途 | SHA-256 |
| --- | --- | --- |
| r297_preregistration.json | 原始预注册，未修改 | 66c938113d29176ac579348c841ad8fb66a525118e016d9ba693c7627f37b97a |
| r297_input_manifest.jsonl | 原始冻结输入清单，未修改 | 1b011b54d04bc9f9dd4c55647279a404b4a323e529c09acc45cbe8b6e32a55f4 |
| r297_formal_factor_baseline.json | 正式三层因子 6,396 分区基线 | cfa217f90ddacc0af8aa52dd60489c9fef353ab13bfefbb6837d84be0dc063b2 |
| r297_scope_correction.json | TTL 缓存原始哈希的透明范围更正 | 7bc5d6cb3e2886caf64f80637b1832fd04bc1ebf7b629b95e0b6ac522106559b |
| r297_execution_erratum_v4.json | 方法/失败更正及 v4 脚本哈希 | 03911d80653eaa0e92b01f55572a7801e0fdbd23e3ee04b333b48ce4f011cd11 |
| audit_r297_counterfactual_membership_v4.py | 隔离因子构建和真实系统装配脚本 | 92683059447fe723d8d8b3b515ebbd88ff2a66f3563044d113cf8f1040d01211 |
| run_r297_scoped_audit_v4.py | 输入/面板校验 runner | 7440264220af035bd9127584a6caaa8da2fdfa5652d685d792e358044ece030b |
| finalize_r297_evidence_v5.py | 最终证据和副作用复核脚本 | 925d4eaf9a9243fdb0dbb379e21506fab8835d07e554906c8e5fd8019828efe1 |
| r297_panel_registration.json | 单次登记与 GET 读回证明 | 06342e37bba2a9c5d584686ee26a58509bdda7c9ef1db56715c7f467f2f85e9c |
| r297_panel_status_recheck_v4.json | v4 执行前只读 GET 证明 | 3813b1e86f6fe7fa5bd6e98012efa9ba12c010e4a698cd71caaba2e5a56aba97 |
| r297_counterfactual_membership_audit_v4.json | 原 v4 机器结果，保留未覆盖 | b996d12d1c34702374db8af3e1c89078052a02a5227258f3b3a2b0ecf8089a0d |
| r297_result_v4.md | 原 v4 报告，保留追溯 | a665d12ca553dbf5a2430f3693865d6019a9667f484051adcaffb068bf864109 |
| r297_final_audit_v5.json | 最终机器证据链与副作用复核 | 651b933f31f8f2408d7df27d5c9b5fdd29ac540a6c0289aa3c9c445b9da5bac4 |
| r297_result_v5.md | 最终可读结果与边界 | f553a456324fc50ff871cd3c24a19cceaec3b4e800c8de549a16e4902d20adb2 |

冻结核验：2,639/2,639 不可变输入一致，6,396/6,396 正式分区一致。面板候选 25d66a13bc214247a93c233b2a32a7cd 为 pending。R257 继续 research-only，生产 pause_production。
