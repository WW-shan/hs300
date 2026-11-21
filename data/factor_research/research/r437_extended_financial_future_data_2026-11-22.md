# R437 扩展财务因子未来数据审计

| 因子 | 法定截止日 | 截止日后生效 | backward join | 无 announce 混用 | R436 PIT | 结论 |
| --- | :---: | :---: | :---: | :---: | :---: | --- |
| ext_alt_hs300_cash_flow_stability_4q_low | 是 | 是 | 是 | 是 | 通过 | clear |
| ext_alt_hs300_ocf_margin_4q | 是 | 是 | 是 | 是 | 通过 | clear |

- 判定：**通过**

## 边界

- Static code audit plus builder PIT evidence; not a provider timestamp audit.
- Current HS300 membership applied to history retains survivorship bias.
- Metadata transition requires a separate reviewed change.
