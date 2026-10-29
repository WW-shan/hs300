# R305 机器结果范围注记

对 r305_scope_deviation_addendum.md/json 的补充：原机器结果 r305_member_bar_gap_audit.json 的 limits 字段也用了“没有读取 OHLCV 数值”的宽泛措辞。它准确描述的是冻结审计脚本所读的列，不涵盖预注册之前的探索命令。

请将该字段与先前报告一起按以下范围解释：预注册前探索确实读取过 OHLC 列做空值/有效值检查并显示过辅助行情样例；冻结脚本只读取日期、代码和辅助来源交易状态。没有计算前瞻收益、没有运行回测或 alpha 扫描。原机器 JSON 和哈希不修改，完整偏差仍以 r305_scope_deviation_addendum.json 为准。本补充不改变任何计数或研究判定。
