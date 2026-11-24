# R537B Runtime Nesting Amendment

R537 和 R537A 都在正式回测前被 R533 runtime 的嵌套路径拦截：

- context 实际在 `runtime["study"]["context"]`；
- membership 实际在 `runtime["study"]["membership"]`；
- 顶层 `runtime["symbols"]` 和 `runtime["panel"]` 仍然有效。

R537B 只修正读取路径，不改变 13 个代表、评分、执行参数、窗口或门禁。两次失败都没有启动正式回测，也没有发布结果；原预注册和失败记录保留。
