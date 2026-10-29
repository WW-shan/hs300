# R294 证据索引

## R293 证据闭环补充

| 文件 | 用途 | SHA-256 |
| --- | --- | --- |
| r293_scope_addendum.json | 原 R293 冻结后补录的源码与本地扩展配置清单、哈希及 pull 状态 | 375094694aedfd93771db71e8068fed73910dca9795344c65f7536e5311311d9 |
| r293_scope_addendum_search.txt | 对 backend/app、backend/tests、scripts 的 builder 名称搜索补充，128 个命中 | be90d50846a5b3f59e90946517713dcb22bbb1c75b8ccc729b1be5de41254838 |
| r293_capture_scope_addendum.py | 补充源码/config 冻结和引用搜索脚本 | 见同名 .sha256 sidecar |
| r293_result.md | 已补充缓存失效边界和冻结时间后的范围修订 | b0665d3736dd98498fc96b9c31536483de6f134441dc5512bc3cce6511fe8ad6 |
| r293_freeze_inputs.py | 原 R293 冻结脚本；sidecar 已补齐 | c1d1fba0227f665af3168ac932a50451840c3c38d4c358b3ded6c0c95ace2a1c |
| r293_register_panel_once.py | 原 R293 面板单次登记脚本；sidecar 已补齐 | 9af759f490cb48864da9ae8af6116981573e22551d5cf6b808e09a9db369d01d |
| r293_capture_scope_addendum.py | R293 补充范围冻结脚本；sidecar 已补齐 | 68fa0fb6a351c5b8f2b666290fd9dc651289158f190029aab20856a228b37bcc |

R293 原输入、预注册、面板证明及搜索结果均保留原样；本补充在原报告冻结时刻之后产生，不伪称为事前预注册内容。

## R294 冻结与执行

| 文件 | 用途 | SHA-256 |
| --- | --- | --- |
| freeze_r294.py | 固化预注册、输入源码哈希、本地分区状态、探针范围 | fb97d182fa06d8985c1cff959b599d934d77fc5405b845c6c41e0ce7605a5911 |
| r294_preregistration.json | 探针前冻结的研究问题、假设、判据和排除范围 | 3eb43c621aa8473a6f2b59c51ab4ec11b821b4a70c62ba853d9889890efe8c5c |
| r294_input_snapshot.json | 21 个源码/配置文件哈希和本机分区边界 | 350c6441f042aa8ead29ccd8f95beac8b5051d782b41c726acfdfe1df4a6583c |
| register_r294_panel_once.py | 3018 面板候选单次登记及读回脚本 | 986674b0c39a9ef70567712b8f99ed95750713119a1cbe1771cf3705b9512384 |
| r294_panel_registration_intent.json | POST 前 GET、guard、候选请求体和意图证明 | sidecar 随文件保存 |
| r294_panel_registration_post.json | 唯一 POST 的状态和响应证明，不重试 | sidecar 随文件保存 |
| r294_panel_registration.json | POST 后 GET，候选数 10→11、ID 和 pending 状态 | 7c99c64aa8036f3e9ce467905ce2b3e475e60c44acc7d027af48e8821374689b |
| probe_r294_directory_publication.py | 仅在 data/.staging 下对合成目录做同卷切换及异常回滚探针 | 34289819afd439f78f9c9c76f927e8a8462ebf9ac9b21b3fbc82fba6c8eb06b6 |
| r294_publication_probe.json | 探针逐文件哈希、成功/回滚断言和限制 | 07b665ef7584c200d6622d9804f581fe617e06f3afe077855f1497e159dbf83d |
| r294_result.md | 研究结论、缓存调用链及后续安全门槛 | a47cd234b67f147a5a5514dc8066cb9e7f50257efbbb9278f358dfe5c423b6af |
| capture_r294_partition_inventory.py | 对预注册中“两份分区”的执行偏差做透明更正并补采最早/最晚分区 | cf56c7250faa324255ac591c1e9f68c771b89e10ac8fab8d9f59e0b31855db81 |
| r294_partition_inventory_erratum.json | 三层各首尾分区共六个哈希；事后补充，仅证明文件身份 | 3752e31273433d5dd24a7fa0c32ccfb7bd748c7760cf2dd15feb457574440d77 |
| r294_round_index.md | 本轮证据索引；准确哈希见文件旁置 sidecar | 外置校验值，避免索引自引用 |

所有证据侧车格式为 SHA256 两空格加文件名。本机 API base 为 http://127.0.0.1:3018；面板只登记 pending 审计候选，没有启动回测。

## 结论边界

- 本地合成探针支持单目录同卷 rename/捕获异常回滚的进程级可行性。
- 不支持三因子目录作为一个事务、断电恢复、并发读者安全或业务数据发布安全的结论。
- 缓存源码审计发现 builder 不清活动 repository 内存缓存，也不刷新 API DuckDB 视图；自动发布必须补齐受控刷新/恢复流程。
- 初始 R294 快照的分区样本比预注册写明的少一份/层；原件未改，已用事后 erratum 为每层补充首尾文件哈希。
- 没有执行完整重建或评估历史值、PIT、收益、alpha。R257 为 research-only，生产为 pause_production。
