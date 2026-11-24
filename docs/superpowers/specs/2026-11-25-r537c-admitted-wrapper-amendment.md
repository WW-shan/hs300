# R537C Admitted Wrapper Amendment

R537、R537A、R537B 都在正式回测前失败：

- R537：context 嵌套路径；
- R537A：membership 嵌套路径；
- R537B：R531 `_core_frame` 需要 `Admitted` 包装，而 R536 给的是裸 `Candidate`。

R537C 只把每个 R536 finalist 包成：

```python
r531.Admitted(candidate, selected)
```

再由 `_core_frame` 构造 13 因子 rank。没有启动正式回测，没有发布结果；原预注册和失败记录全部保留。
