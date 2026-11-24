# R537A Context Path Amendment

## 背景

R537 在构造扩展得分前报：

```text
KeyError: 'context'
```

没有启动任何项目回测，也没有生成 R537 结果。

## 根因

R533 的 runtime 把上下文放在：

```python
runtime["study"]["context"]
```

R537 错读为顶层 `runtime["context"]`。

## 修正

R537A 从 `runtime["study"]["context"]` 读取，不改因子、权重、执行参数或门禁。

## 边界

- R537 原预注册和失败记录保留；
- R537A 使用新的预注册和结果文件名；
- 回归测试覆盖 13 个 R536A 代表映射。
