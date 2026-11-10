#!/usr/bin/env python3
"""Build and validate the explicit research taxonomy for extension fields.

The extension table only describes dtypes. This script keeps the research
classification (data class, cross-section semantics, PIT hint and zero
semantics) explicit, source-linked and fail-closed when a new numeric field is
added without a classification.
"""
from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SOURCE = ROOT / "scripts" / "build_alt_factor_data.py"
TAXONOMY = DATA / "factor_research" / "catalog" / "field_taxonomy.json"
CUSTOM_EVIDENCE = DATA / "factor_research" / "catalog" / "classification_evidence" / "custom_core_watch_v1.json"


def _digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


@dataclass(frozen=True)
class Group:
    fields: tuple[str, ...]
    data_class: str
    cross_section_type: str
    pit_hint: str
    zero_is_missing: bool
    direction: str
    note: str


GROUPS = (
    Group(
        (
            "insider_buy_20d", "insider_net_buy_20d", "insider_sell_20d_low",
        ),
        "flow", "event_sparse", "declared", False, "high",
        "董监高竞价交易按公告日下一交易日生效，20日线性衰减。",
    ),
    Group(
        (
            "holder_top10_free_float_share", "holder_top10_share_chg_qoq",
            "holder_top10_share_chg_yoy",
        ),
        "flow", "stock_cross_sectional", "declared", False, "high",
        "前十大流通股东数据按公告日下一交易日生效。",
    ),
    Group(
        ("holder_net_buy_ratio_90d", "holder_net_buy_count_90d"),
        "flow", "stock_cross_sectional", "declared", False, "high",
        "重要股东净增持按公告日生效，90日窗口统计。",
    ),
    Group(
        (
            "fund_count_low", "fund_count_chg_qoq_low", "fund_holding_ratio_low",
            "fund_holding_ratio_chg_qoq_low", "fund_holding_value_chg_qoq_low",
        ),
        "flow", "stock_cross_sectional", "declared", False, "high",
        "基金持仓拥挤度使用季度披露，并按可得日下一交易日生效。",
    ),
    Group(
        ("lhb_net_buy_ratio_20d", "lhb_inst_net_buy_ratio_20d", "lhb_event_count_20d_low"),
        "flow", "event_sparse", "declared", True, "high",
        "龙虎榜事件按公告日下一交易日生效；连续0表示当日无事件而不是连续观测。",
    ),
    Group(
        ("pledge_ratio_low", "pledge_ratio_chg_1m_low"),
        "flow", "stock_cross_sectional", "declared", False, "high",
        "股权质押快照按快照日下一交易日生效。",
    ),
    Group(
        ("repurchase_event_60d",),
        "flow", "event_sparse", "declared", True, "high",
        "回购计划按公告日下一交易日生效；连续0表示无事件。",
    ),
    Group(
        ("block_trade_ratio_20d_low", "block_premium_20d"),
        "flow", "event_sparse", "declared", True, "high",
        "大宗交易按事件日下一交易日生效；连续0表示无事件。",
    ),
    Group(
        ("analyst_rating_score_20d",),
        "sentiment", "stock_cross_sectional", "declared", False, "high",
        "分析师评级按研报日下一交易日生效，20日滚动。",
    ),
    Group(
        ("analyst_rating_change_20d",),
        "sentiment", "event_sparse", "declared", True, "high",
        "评级调整是稀疏事件，按研报日下一交易日生效。",
    ),
    Group(
        ("forecast_surprise_60d", "express_surprise_60d"),
        "financial", "event_sparse", "declared", True, "high",
        "业绩预告/快报按公告日下一交易日生效；连续0表示无事件。",
    ),
    Group(
        ("industry_mom_20d", "industry_mom_60d", "industry_rps_20d", "industry_breadth_20d"),
        "industry", "industry_cross_sectional", "not_applicable", False, "high",
        "一级行业等权聚合值，在同日行业内重复、跨行业变化。",
    ),
    Group(
        ("stock_rel_industry_20d", "hs300_stock_rel_industry_top3_flag"),
        "industry", "stock_cross_sectional", "not_applicable", False, "high",
        "个股相对所属行业强弱，保留个股横截面差异。",
    ),
    Group(
        ("mainline_top1_flag", "mainline_strength"),
        "industry", "event_sparse", "unknown", True, "high",
        "主线行业字段按行业广播；源数据同日直连，PIT 与历史完整性尚未独立证明。",
    ),
    Group(
        ("mainline_leader_flag",),
        "industry", "event_sparse", "unknown", True, "high",
        "主线龙头为稀疏个股标记；源数据同日直连，PIT 尚未独立证明。",
    ),
    Group(
        ("etf_share_chg_1m", "etf_share_chg_3m"),
        "flow", "market_level", "declared", False, "high",
        "510300份额快照日下一交易日生效。",
    ),
    Group(
        ("qvix_z_252",),
        "sentiment", "market_level", "not_applicable", False, "low",
        "300ETF QVIX 日度观测，252日扩张标准化。",
    ),
    Group(
        ("m1_m2_scissors", "m1_m2_scissors_chg_1m", "m2_yoy"),
        "macro", "market_level", "declared", False, "high",
        "货币供应同比按月份对应可得日延后45天生效。",
    ),
    Group(
        ("shibor_3m", "shibor_3m_chg_20d"),
        "macro", "market_level", "declared", False, "low",
        "SHIBOR 日度利率与20日变化。",
    ),
    Group(
        ("pmi", "cpi_yoy", "ppi_yoy"),
        "macro", "market_level", "declared", False, "none",
        "宏观活动/通胀指标按数据可得日生效，超过60天自动置空。",
    ),
    Group(
        (
            "market_breadth_above_ma20", "market_profit_score", "market_trend_score",
            "market_speculation_score",
        ),
        "sentiment", "market_level", "declared", False, "high",
        "市场宽度与赚钱效应指标来自 regime_history。",
    ),
    Group(
        ("northbound_net_buy_20d",),
        "flow", "market_level", "declared", False, "high",
        "北向20日净买额按成交可得日生效。",
    ),
    Group(
        ("margin_chg_1m_low", "short_ratio_low"),
        "flow", "stock_cross_sectional", "declared", False, "high",
        "个股两融余额与融券余量按快照日下一交易日生效。",
    ),
    Group(
        ("if_net_short_chg_1m",),
        "flow", "market_level", "declared", False, "high",
        "IF前20席位净空头月度变化，市场级广播。",
    ),
    Group(
        ("bond_mom_20d", "bond_tf_mom_20d"),
        "macro", "market_level", "not_applicable", False, "low",
        "国债期货日度收盘计算的20日动量，市场级广播。",
    ),
    Group(
        ("ah_premium_z", "ah_premium_chg_1m"),
        "macro", "market_level", "declared", False, "low",
        "AH溢价月末记录下一交易日生效，45天陈旧截断。",
    ),
    Group(
        ("hsi_mom_20d", "hsciei_mom_20d", "hsi_rel_hs300_20d"),
        "macro", "market_level", "not_applicable", False, "high",
        "港股指数日度动量，市场级广播。",
    ),
    Group(
        ("margin_safety_z", "margin_safety_chg_20d"),
        "flow", "market_level", "declared", False, "high",
        "两融维持担保比例月末记录下一交易日生效。",
    ),
    Group(
        ("nh_nl_diff_z", "nh_nl_diff_20d"),
        "sentiment", "market_level", "declared", False, "high",
        "HS300新高减新低比例，月末记录下一交易日生效。",
    ),
    Group(
        ("shibor_tightness_z",),
        "macro", "market_level", "declared", False, "low",
        "SHIBOR 3M扩张z-score，月末记录下一交易日生效。",
    ),
    Group(
        ("turnover_hhi_z", "top30_amount_share_z"),
        "flow", "market_level", "declared", False, "low",
        "HS300成交集中度，月末记录下一交易日生效。",
    ),
    Group(
        ("hs300_turnover_z", "hs300_turnover_chg_20d"),
        "flow", "market_level", "declared", False, "low",
        "HS300换手率水平与变化，月末记录下一交易日生效。",
    ),
    Group(
        ("hs300_turnover_z_ext2017", "hs300_turnover_chg_20d_ext2017"),
        "flow", "market_level", "declared", False, "low",
        "扩展2017历史的HS300换手率因子。",
    ),
    Group(
        ("hs300_turnover_gate_flag_ext2017",),
        "flow", "market_level", "declared", False, "high",
        "换手率z-score小于0时为1，0是有效闸门状态。",
    ),
    Group(
        (
            "idio_vol_20d_low", "idio_vol_40d_low", "idio_vol_60d_low",
            "vol_of_vol_20d_low", "vol_of_vol_60d_low", "market_down_beta_60d_low",
        ),
        "price", "stock_cross_sectional", "not_applicable", False, "high",
        "HS300市场模型衍生的个股风险因子，已按“高好”方向转换。",
    ),
    Group(
        ("price_delay_60d", "cs_spread_60d"),
        "price", "stock_cross_sectional", "not_applicable", False, "low",
        "价格延迟与估计买卖价差，数值越低越好。",
    ),
    Group(
        ("beta_asymmetry_120d", "trend_efficiency_60d", "risk_adjusted_momentum_60d"),
        "price", "stock_cross_sectional", "not_applicable", False, "high",
        "个股价格趋势、beta不对称与风险调整动量。",
    ),
    Group(
        ("market_skew_60d_low",),
        "price", "market_level", "not_applicable", False, "high",
        "HS300 60日已实现偏度反向，市场级广播。",
    ),
    Group(
        ("cash_flow_stability_4q_low",),
        "financial", "stock_cross_sectional", "declared", False, "high",
        "现金流/总资产四季稳定性，按法定披露截止日后一日生效。",
    ),
    Group(
        ("ocf_margin_4q", "cash_to_assets"),
        "financial", "stock_cross_sectional", "declared", False, "high",
        "TTM经营现金流利润率与现金资产占比。",
    ),
    Group(
        ("net_financing_4q_low",),
        "financial", "stock_cross_sectional", "declared", False, "high",
        "净筹资现金流四季强度反向。",
    ),
    Group(
        (
            "sga_ratio_4q_low", "sga_ratio_4q_industry_neutral",
            "sga_ratio_4q_qoq_improvement",
        ),
        "financial", "stock_cross_sectional", "declared", False, "high",
        "销售+管理费用率、行业中性分位与环比改善。",
    ),
    Group(
        ("management_expense_ratio_4q_low", "sga_ratio_stability_4q_low"),
        "financial", "stock_cross_sectional", "declared", False, "high",
        "管理费用率和销售+管理费用率稳定性。",
    ),
    Group(
        ("gross_margin_stability_4q_low", "gross_margin_yoy_improvement_q"),
        "financial", "stock_cross_sectional", "declared", False, "high",
        "毛利率稳定性与单季同比改善。",
    ),
    Group(
        ("goodwill_ratio_low", "intangible_ratio_low", "rd_intensity", "capex_intensity_low"),
        "financial", "stock_cross_sectional", "declared", False, "high",
        "商誉、无形资产、研发和资本开支强度。",
    ),
    Group(
        ("equity_supply_ratio_180d_low",),
        "financial", "stock_cross_sectional", "declared", False, "high",
        "股权融资供给180日强度反向。",
    ),
    Group(
        (
            "dividend_growth_ttm", "dividend_ttm_cash", "dividend_yield",
            "dividend_initiation_flag",
        ),
        "financial", "stock_cross_sectional", "declared", False, "high",
        "现金分红TTM、增长、收益率与首次分红标记。",
    ),
    Group(
        ("equity_risk_premium", "equity_risk_premium_chg3m"),
        "macro", "market_level", "not_applicable", False, "high",
        "沪深300盈利收益率减10年国债，市场级估值条件。",
    ),
    Group(
        ("credit_spread_5y_low", "term_spread_10y_2y"),
        "macro", "market_level", "declared", False, "high",
        "信用利差与期限利差，市场级宏观条件。",
    ),
    Group(
        ("market_emotion_score",),
        "sentiment", "market_level", "declared", False, "none",
        "市场情绪分数，作为周期条件而非个股排序因子。",
    ),
)

STALENESS_DAYS_OVERRIDES = {
    "if_net_short_chg_1m": 45,
    "margin_safety_z": 45,
    "margin_safety_chg_20d": 45,
    "nh_nl_diff_z": 45,
    "nh_nl_diff_20d": 45,
    "turnover_hhi_z": 45,
    "top30_amount_share_z": 45,
    "hs300_turnover_z": 45,
    "hs300_turnover_chg_20d": 45,
    "hs300_turnover_z_ext2017": 45,
    "hs300_turnover_chg_20d_ext2017": 45,
}

SOURCE_FUNCTION_GROUPS = (
    (("insider_buy_20d", "insider_net_buy_20d", "insider_sell_20d_low"), "_insider_daily"),
    (("holder_top10_free_float_share", "holder_top10_share_chg_qoq", "holder_top10_share_chg_yoy"), "_holder_daily"),
    (("holder_net_buy_ratio_90d", "holder_net_buy_count_90d"), "_holder_net_buy_daily"),
    (("fund_count_low", "fund_count_chg_qoq_low", "fund_holding_ratio_low", "fund_holding_ratio_chg_qoq_low", "fund_holding_value_chg_qoq_low"), "_fund_holding_daily"),
    (("lhb_net_buy_ratio_20d", "lhb_inst_net_buy_ratio_20d", "lhb_event_count_20d_low"), "_lhb_daily"),
    (("pledge_ratio_low", "pledge_ratio_chg_1m_low"), "_pledge_daily"),
    (("repurchase_event_60d",), "_repurchase_daily"),
    (("block_trade_ratio_20d_low", "block_premium_20d"), "_block_trade_daily"),
    (("analyst_rating_score_20d", "analyst_rating_change_20d"), "_analyst_daily"),
    (("forecast_surprise_60d",), "_forecast_daily"),
    (("express_surprise_60d",), "_express_daily"),
    (("industry_mom_20d", "industry_mom_60d", "industry_rps_20d", "industry_breadth_20d", "stock_rel_industry_20d", "hs300_stock_rel_industry_top3_flag"), "_industry_daily"),
    (("mainline_top1_flag", "mainline_strength", "mainline_leader_flag"), "_mainline_daily"),
    (("etf_share_chg_1m", "etf_share_chg_3m"), "_etf_flow_daily"),
    (("qvix_z_252",), "_qvix_daily"),
    (("m1_m2_scissors", "m1_m2_scissors_chg_1m", "m2_yoy", "shibor_3m", "shibor_3m_chg_20d", "pmi", "cpi_yoy", "ppi_yoy"), "_macro_extra_daily"),
    (("market_breadth_above_ma20", "market_profit_score", "market_trend_score", "market_speculation_score"), "_regime_extra_daily"),
    (("northbound_net_buy_20d",), "_northbound_daily"),
    (("margin_chg_1m_low",), "_margin_daily"),
    (("short_ratio_low",), "_short_ratio_daily"),
    (("if_net_short_chg_1m",), "_if_position_daily"),
    (("bond_mom_20d", "bond_tf_mom_20d"), "_bond_momentum_daily"),
    (("ah_premium_z", "ah_premium_chg_1m"), "_ah_premium_daily"),
    (("hsi_mom_20d", "hsciei_mom_20d", "hsi_rel_hs300_20d"), "_hk_risk_daily"),
    (("margin_safety_z", "margin_safety_chg_20d"), "_margin_safety_daily"),
    (("nh_nl_diff_z", "nh_nl_diff_20d"), "_breadth_daily"),
    (("shibor_tightness_z",), "_shibor_tightness_daily"),
    (("turnover_hhi_z", "top30_amount_share_z"), "_concentration_daily"),
    (("hs300_turnover_z", "hs300_turnover_chg_20d"), "_turnover_level_daily"),
    (("hs300_turnover_z_ext2017", "hs300_turnover_chg_20d_ext2017", "hs300_turnover_gate_flag_ext2017"), "_turnover_extended_daily"),
    (("idio_vol_20d_low", "idio_vol_40d_low", "idio_vol_60d_low"), "_idio_vol_daily"),
    (("price_delay_60d",), "_price_delay_daily"),
    (("cs_spread_60d",), "_corwin_schultz_spread_frame"),
    (("vol_of_vol_20d_low", "vol_of_vol_60d_low"), "_vol_of_vol_daily"),
    (("market_down_beta_60d_low",), "_market_down_beta_daily"),
    (("beta_asymmetry_120d",), "_beta_asymmetry_daily"),
    (("trend_efficiency_60d",), "_trend_efficiency_frame"),
    (("risk_adjusted_momentum_60d",), "_risk_adjusted_momentum_frame"),
    (("market_skew_60d_low",), "_market_skew_daily"),
    (("cash_flow_stability_4q_low",), "_cash_flow_stability_daily"),
    (("ocf_margin_4q",), "_ocf_margin_4q_daily"),
    (("cash_to_assets",), "_cash_to_assets_daily"),
    (("net_financing_4q_low",), "_net_financing_4q_daily"),
    (("sga_ratio_4q_low", "sga_ratio_4q_industry_neutral", "sga_ratio_4q_qoq_improvement"), "_sga_ratio_4q_daily"),
    (("management_expense_ratio_4q_low",), "_management_expense_ratio_4q_daily"),
    (("sga_ratio_stability_4q_low",), "_sga_ratio_stability_4q_daily"),
    (("gross_margin_stability_4q_low",), "_gross_margin_stability_4q_daily"),
    (("gross_margin_yoy_improvement_q",), "_gross_margin_yoy_improvement_q_daily"),
    (("goodwill_ratio_low", "intangible_ratio_low", "rd_intensity", "capex_intensity_low"), "_goodwill_daily"),
    (("equity_supply_ratio_180d_low",), "_equity_supply_daily"),
    (("dividend_growth_ttm", "dividend_ttm_cash", "dividend_yield", "dividend_initiation_flag"), "_dividend_daily"),
    (("equity_risk_premium", "equity_risk_premium_chg3m"), "_erp_daily"),
    (("credit_spread_5y_low", "term_spread_10y_2y"), "_macro_daily"),
    (("market_emotion_score",), "_emotion_daily"),
)


def _field_lines() -> dict[str, tuple[int, int]]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    out: dict[str, tuple[int, int]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "FIELDS" for target in node.targets):
            for item in node.value.elts:
                if isinstance(item, ast.Call) and item.args and isinstance(item.args[0], ast.Constant):
                    out[str(item.args[0].value)] = (item.lineno, item.end_lineno or item.lineno)
    return out


def _function_lines() -> dict[str, tuple[int, int]]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return {
        node.name: (node.lineno, node.end_lineno or node.lineno)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _custom_evidence() -> str:
    payload = {
        "schema_version": 1,
        "purpose": "Stable classification evidence for the core watch custom factors.",
        "factors": [
            {
                "factor_id": "uf_quality_value_defensive",
                "formula": "roe_latest / (100 * pb_latest) - debt_ratio_latest / 100",
                "data_class": "financial",
                "pit_hint": "declared",
                "note": "PIT financial fields are gated by announcement dates.",
            },
            {
                "factor_id": "uf_cash_quality",
                "formula": "ocf_ni - accrual",
                "data_class": "financial",
                "pit_hint": "declared",
                "note": "PIT financial fields are gated by announcement dates.",
            },
            {
                "factor_id": "uf_industry_relative_strength_change_5d",
                "formula": "ts_delta(ext_alt_hs300_stock_rel_industry_20d, 5)",
                "data_class": "industry",
                "pit_hint": "unknown",
                "note": "Inherits the source factor's industry-relative PIT contract.",
            },
        ],
    }
    CUSTOM_EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM_EVIDENCE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return _digest(CUSTOM_EVIDENCE)


def _existing_extra_records() -> list[dict[str, Any]]:
    evidence_hash = _custom_evidence()
    definitions = (
        ("uf_quality_value_defensive", "financial", "declared", 0.85),
        ("uf_cash_quality", "financial", "declared", 0.85),
        ("uf_industry_relative_strength_change_5d", "industry", "unknown", 0.8),
    )
    return [
        {
            "factor_id": factor_id,
            "data_class": data_class,
            "cross_section_type": "stock_cross_sectional",
            "pit_hint": pit_hint,
            "asset_types": ["stock"],
            "source_field": next(
                item["formula"]
                for item in json.loads(CUSTOM_EVIDENCE.read_text(encoding="utf-8"))["factors"]
                if item["factor_id"] == factor_id
            ),
            "zero_is_missing": False,
            "direction": "high",
            "classification_confidence": confidence,
            "evidence_refs": [
                {"path": str(CUSTOM_EVIDENCE.relative_to(ROOT)), "sha256": evidence_hash},
            ],
        }
        for factor_id, data_class, pit_hint, confidence in definitions
    ]


def main() -> int:
    source_hash = _digest(SOURCE)
    field_lines = _field_lines()
    function_lines = _function_lines()
    group_by_field = {field: group for group in GROUPS for field in group.fields}
    function_by_field = {field: function for fields, function in SOURCE_FUNCTION_GROUPS for field in fields}
    configs = sorted((DATA / "ext_data").glob("*/config.json"))
    numeric_fields: dict[str, tuple[str, str]] = {}
    for config_path in configs:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        ext_id = str(config["id"])
        for field in config.get("fields", []):
            if field.get("dtype") not in {"int", "float"}:
                continue
            name = str(field["name"])
            numeric_fields[f"ext_{ext_id}_{name}"] = (ext_id, name)

    missing_groups = sorted(
        factor_id for factor_id, (ext_id, name) in numeric_fields.items()
        if ext_id == "alt_hs300" and name not in group_by_field
    )
    if missing_groups:
        raise SystemExit("unclassified numeric extension fields: " + ", ".join(missing_groups))
    missing_functions = sorted(
        field for field in group_by_field if field not in function_by_field
    )
    if missing_functions:
        raise SystemExit("missing source function mapping: " + ", ".join(missing_functions))

    records: list[dict[str, Any]] = []
    for factor_id, (ext_id, name) in numeric_fields.items():
        if ext_id != "alt_hs300":
            raise SystemExit(f"unsupported numeric extension config without taxonomy rules: {ext_id}")
        group = group_by_field[name]
        line_start, line_end = field_lines[name]
        function = function_by_field[name]
        function_start, function_end = function_lines[function]
        record = {
            "factor_id": factor_id,
            "data_class": group.data_class,
            "cross_section_type": group.cross_section_type,
            "pit_hint": group.pit_hint,
            "asset_types": ["stock"],
            "source_field": name,
            "source_function": function,
            "zero_is_missing": group.zero_is_missing,
            "direction": group.direction,
            "classification_confidence": 0.95 if group.pit_hint != "unknown" else 0.8,
            "classification_note": group.note,
            "evidence_refs": [
                {
                    "path": str(SOURCE.relative_to(ROOT)),
                    "line_start": line_start,
                    "line_end": line_end,
                    "sha256": source_hash,
                },
                {
                    "path": str(SOURCE.relative_to(ROOT)),
                    "line_start": function_start,
                    "line_end": function_end,
                    "sha256": source_hash,
                },
            ],
        }
        if name in STALENESS_DAYS_OVERRIDES:
            record["staleness_days_override"] = STALENESS_DAYS_OVERRIDES[name]
        records.append(record)
    records.extend(_existing_extra_records())
    records.sort(key=lambda item: item["factor_id"])
    payload = {
        "schema_version": 1,
        "generated_by": "scripts/sync_factor_taxonomy.py",
        "source": {
            "path": str(SOURCE.relative_to(ROOT)),
            "sha256": source_hash,
            "numeric_extension_fields": len(numeric_fields),
        },
        "records": records,
    }
    TAXONOMY.parent.mkdir(parents=True, exist_ok=True)
    TAXONOMY.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "taxonomy": str(TAXONOMY.relative_to(ROOT)),
        "records": len(records),
        "extension_fields": len(numeric_fields),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
