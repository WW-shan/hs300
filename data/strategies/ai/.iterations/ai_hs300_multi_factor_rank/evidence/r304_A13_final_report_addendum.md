# R304-A13 面板登记收尾

## 结果

已将 A13 事后偏差审计单独登记到研究面板。预检、POST、最终 GET 均 HTTP 200；候选数 20→21。新候选 ID ecd1805dde7a4a8d971718116bd773f6，状态 pending，名称明确标记为事后审计、非预注册/非回测。原 R304 ID 1ed2c75285b74fc6a521cc73f75f7320 仍唯一且 pending；A11 基线已有候选状态全部未改变。登记只记录审计，不会追溯修复 chunk 获取未预注册的问题。

POST 回包保存在 r304_A13_panel_registration_post.json。最终 GET 的原始响应 body 未单独保存，仅保留响应 SHA-256 d5d5ec9ab2bf13d91cf7f31fcff13a14d1d8eb711399633e86cf62c8b89c410d 和读回候选摘要；没有重发请求。

## 研究结论

离线静态 bundle 显示，成分列表 POST /indexInfo/index-sample-information 的页面请求体只含搜索词、页码、页长和排序字段。单独的最新交易日 GET 只更新页面显示字段。本页面代码没有历史日期参数；服务端是否接受额外参数、其他官方接口是否支持历史查询，本轮均未验证，也没有调用成分查询 API。

A13 chunk 请求没有事前单独预注册。PowerShell 调用报错后 dispatch 状态无法确认；Python 随后成功取得 HTTP 200。因此成功请求至少一次，实际总次数可能为一次或两次。该偏差已如实留档。A12 final record 的 29 项证据哈希有 28 项匹配；唯一差异是事后追加内容后的 A12 amendment 与旧 sidecar 不一致。旧文件、旧哈希和 dispatch 记录均保留。

PIT 成分重建仍为 no-go：缺少 2018-01-02 完整官方种子及闭合的有效日事件链。本轮没有收益回测、因子收益或 Alpha 搜索，没有修改生产数据或策略。pending 不代表结果通过。

机器最终记录 r304_A13_final_record_addendum.json 覆盖本轮材料哈希；逐项 sidecar 均匹配。
