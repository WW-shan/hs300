# R406 状态化回撤防守 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

## Goal
R405 显示 80/20 的下尾集中在宽度走弱/状态偏弱的月份。R406 用 T-1 状态做简单仓位防守，验证能否把 5000 次配对 bootstrap 的 p05 拉正。

## Constraints
- 只作用于冻结 80/20 的日收益；不改因子、不改 70/80/90 比例。
- 状态规则预登记；最多 5 条，使用 T-1。
- 防御仓位最多降到 50%，现金收益 0；切换成本 5bp。
- 2× 主口径，3× 不重新调参；5× 不硬否决。
