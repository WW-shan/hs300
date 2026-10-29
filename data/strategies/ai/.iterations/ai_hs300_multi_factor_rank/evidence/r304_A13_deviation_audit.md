# R304-A13 请求偏差与官方成分查询静态审计

记录时间（UTC）：2026-10-29T08:13:27.215505+00:00

## 请求偏差

A13 静态 chunk 下载在发出请求前没有单独预注册。先前 PowerShell `Invoke-WebRequest` 报 `NullReferenceException`，现存记录无法确认请求是否已 dispatch；随后 Python 获取到该静态 JS 文件 HTTP 200、16,208 字节，SHA-256 `102121a3f8726caa4f00d7fab6434ff8072b367ba878a03fc1e88bf94f8098dc`。因此至少一次请求已确认成功，总请求数为 1 次已确认、最多可能 2 次；不得表述为“确定只请求一次”。下载文件时间戳为 2026-10-29T07:57:48.011377+00:00，它不是精确的网络 dispatch 时间。此次是页面静态资源，不是成分查询 API；没有调用页面所列 API。

原 `r304_A13_local_route_probe.json` 记录的是下载 chunk **之前**对主页 bundle 的本地解析，`network_requests=0` 只适用于那一步，不覆盖后续静态资源请求。原件与其 SHA sidecar均保留。由于 chunk 和探查脚本当时没有 sidecar，本次只在事后追加哈希 sidecar；它们证明当前文件内容，不证明请求发生时的时间顺序。

## 静态调用契约

- 主页 bundle SHA-256：`232cafcfdb4da07c7b2ce24efae30318b59a8168edd023566364579445f2d998`；chunk SHA-256：`102121a3f8726caa4f00d7fab6434ff8072b367ba878a03fc1e88bf94f8098dc`。
- 页面把 `sampleTable` 映射为 POST `/indexInfo/index-sample-information`。页面调用时只传 `searchInput`、`pageNum`、`pageSize`、`sortField`、`sortOrder`。代码中未发现日期/生效日参数。
- 页面另行 GET `/csindex-home/indexInfo/index-sample-information-trade-date-new`，结果只赋给显示用的 `updateDate`；没有传入成分查询请求。
- 这说明已观察页面的查询契约没有历史日期字段；不能据此断言服务端不接受额外参数，也不能断言没有其他官方历史接口。未发 API 请求，不猜参数、不继续探测。

## A12 证据哈希更正

A12 final record 的 29 项记录哈希中，28 项仍匹配，唯一不匹配的是 `r304_preregistration_amendment_A12.json`：record/旧 sidecar 记录 `50b386858f7e2a81bdcf71cbaae4b2ded9a82501d8720f959e121fce10d94567`，当前文件为 `82e38f23f30ebb806fde5e906390cbc74c396d22936d1b1705de44e3e29dab29`。A12 dispatch 在 07:45:25 UTC 记录的 amendment 哈希仍是旧值；该 amendment 后来被追加 `next_discovery_scope`，但没有同步更新原 sidecar。保留旧 sidecar和原 final record，不倒填或覆盖。

另外，`r304_A12_final_record_addendum.py` 当前哈希 `c8bee77c797290f8028575f6a22cbe745dcb7d3347ebafaea32ca14a5c68e085`，旧 sidecar 为 `b99cf1d7b9c6d82f46501ba992045692e3c53da146c2f0a39b9534fddf9631eb`；该脚本也在最终记录之后被修改。A12 可读报告当前哈希 `7093d665f7f0c0c30b420e67e0b91881ddc0ee8b1f1e7d252a8af8bfee65df75` 与其 sidecar `7093d665f7f0c0c30b420e67e0b91881ddc0ee8b1f1e7d252a8af8bfee65df75` 相符。A12 final record JSON 自身及 sidecar相符。详细逐项回核见本文件旁的机器记录。

## 结论与边界

没有得到按历史日期取成分的官方证据；2018-01-02 完整种子与 PIT 事件链仍未闭合，继续 no-go。没有做收益回测、Alpha 搜索或生产数据/策略写入。沿用已登记的 R304 面板候选 `1ed2c75285b74fc6a521cc73f75f7320`；最近一次 A11 读回为唯一匹配、`pending`。本轮不重复注册、不改候选状态。后续任何官方网络请求必须先单独预注册、留好 dispatch intent，再按一次性请求规则执行。
