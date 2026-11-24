# R534A Runtime Helper Amendment

## 失败

R534 的第一个 always-on 训练正式回测已完成，证据为 saved/passed/passed，但在训练 bootstrap 前报：

```text
KeyError: 'r533'
```

未发布 R534 结果，也未读取验证、近一年或压力结果。

## 根因

`load_runtime` 返回：

```python
{"r533": helper, "runtime": r533.load_runtime_inputs(...), "p5": p5}
```

训练函数只拿到了内层 `runtime` 字典，却调用 `runtime["r533"].bootstrap_curve(...)`。helper 未被附加到内层 runtime。

## 修正

R534A 把 `r533` helper 直接附加到 runtime：

```python
runtime = r533.load_runtime_inputs(root)
runtime["r533"] = r533
```

回归测试：

`test_runtime_bundle_exposes_r533_helper_to_training`

## 不变项

- 六因子、10 仓/60 日、规则集合、窗口、bootstrap、门禁全部不变；
- 失败回测和 R534 原预注册保留；
- R534A 使用新预注册和结果文件名。
