# R475 业绩预告/快报 PEAD 独立 sleeve 确认 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 检验项目已有但尚未通过完整研究门槛的 PEAD 因子，单独作为 sleeve 时是否有稳定收益，并区分预告、快报和两者组合。

**Architecture:** 新增 append-only R475 runner。复用 R100 的真实 T+1 回测和 `ext_alt_hs300_forecast_surprise_60d` / `express_surprise_60d`，对仓数、持有期、入场/退出阈值做小网格。训练+验证选样，检验+近一年确认；5× warning-only。

**Tech Stack:** Python 3.11+、pytest、R453 bootstrap、R468 runner。

## Global Constraints

- 因子：forecast surprise、express surprise、combined PEAD。
- 方向 high；仓数 10/20；持有期 10/20/30；入场 60/70/80；退出 30/40/50。
- 训练+验证选样；检验+近一年确认；2× 主成本、3× 压力、5× warning；无杠杆。
- 不覆盖 R100；不生成订单或生产配置。
