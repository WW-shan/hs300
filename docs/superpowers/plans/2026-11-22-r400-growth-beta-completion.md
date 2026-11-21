# R400 防守核心 + 动量/行业相对强度 Beta 补全 Implementation Plan

> **For agentic workers:** REQUIRED SUBSCRILL: Use superpowers:executing-plans to implement this plan task-by-task.

## Goal

针对 R398/R399 暴露的“绝对收益尚可、最近一年跑输沪深300”问题，在已注册因子上做一轮预登记的防守核心 + 动量/行业相对强度组合，验证能否补 beta 而不牺牲长期风险。

## Constraints

- 只使用已注册因子，不新增公式。
- 3 个 R398 核心候选冻结；beta 腿只允许 60 日动量、趋势效率、风险调整动量、行业相对强度。
- 核心 75% / beta 25%，无杠杆，20/30/40 日持有期、10/20/30 仓。
- 2× 主成本、3× 压力；HS300 `000300.SH` 作为硬门槛之一。
- 不用检验段回头选参，不生成订单或生产配置。

## Tasks

1. 预登记与红测。
2. 跑训练、验证；只把两边都过门槛的候选带入盲测。
3. 补 HS300 对齐、逐年审计和邻域检查。
4. 提交 JSON/Markdown/sidecar、测试、Ruff、CI。
