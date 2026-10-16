"""R17 评分中性化: 行业去均值 / 市值残差化 / exclude / fail-closed 的固定样本断言。"""
from __future__ import annotations

import math

import polars as pl
import pytest

from app.strategy.scoring import (
    effective_neutralize,
    neutralize_enabled,
    neutralize_scoring_columns,
)


def _frame() -> pl.DataFrame:
    # 两个行业, 各自水平不同; 市值风格列让"低波动腿"带上规模暴露
    return pl.DataFrame(
        {
            "date": ["2024-01-02"] * 6,
            "symbol": ["a", "b", "c", "d", "e", "f"],
            "industry_l1": ["I1", "I1", "I1", "I2", "I2", "I2"],
            "leg_low_vol": [1.0, 2.0, 3.0, 11.0, 12.0, 13.0],
            "leg_small_mv": [-1.0, -2.0, -3.0, -11.0, -12.0, -13.0],
            "log_float_mv": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        }
    )


def test_industry_demean_equalizes_group_means():
    out = neutralize_scoring_columns(
        _frame(), ["leg_low_vol"], {"industry": "industry_l1"}
    )
    grouped = out.group_by("industry_l1").agg(pl.col("leg_low_vol").mean().alias("m"))
    means = grouped["m"].to_list()
    assert all(abs(m) < 1e-9 for m in means)
    # 行业内的相对差异保留
    i1 = out.filter(pl.col("industry_l1") == "I1")["leg_low_vol"].to_list()
    assert i1 == pytest.approx([-1.0, 0.0, 1.0])


def test_style_residual_removes_correlation():
    # 腿 = 2 x 规模 + 微小噪声 → 残差化后与规模的横截面相关应为 0、幅度大幅缩小
    frame = pl.DataFrame(
        {
            "date": ["2024-01-02"] * 6,
            "symbol": list("abcdef"),
            "leg_low_vol": [2.0, 4.0, 6.0, 8.0, 10.0, 12.0],
            "log_float_mv": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        }
    )
    out = neutralize_scoring_columns(frame, ["leg_low_vol"], {"styles": ["log_float_mv"]})
    resid = out["leg_low_vol"]
    # 完全线性于规模 → 残差应被清零 (常数序列的相关系数为 NaN, 故直接断言幅度)
    assert resid.abs().max() < 1e-9


def test_style_residual_keeps_orthogonal_component():
    # 规模无关的差异必须保留 (残差化不能把信号清空)
    frame = pl.DataFrame(
        {
            "date": ["2024-01-02"] * 5,
            "symbol": list("abcde"),
            "leg": [1.0, 2.0, 3.0, 2.0, 1.0],
            "log_float_mv": [1.0, 2.0, 3.0, 4.0, 5.0],
        }
    )
    out = neutralize_scoring_columns(frame, ["leg"], {"styles": ["log_float_mv"]})
    values = out["leg"].to_list()
    assert max(values) - min(values) > 0.5


def test_industry_plus_style_residual():
    out = neutralize_scoring_columns(
        _frame(),
        ["leg_low_vol"],
        {"industry": "industry_l1", "styles": ["log_float_mv"]},
    )
    # 行业内去均值 + 行业内残差: 每个行业内部与 (行业内中心化的) 规模正交
    for ind in ("I1", "I2"):
        sub = out.filter(pl.col("industry_l1") == ind)
        assert float(sub["leg_low_vol"].mean()) == pytest.approx(0.0, abs=1e-9)


def test_exclude_skips_leg():
    out = neutralize_scoring_columns(
        _frame(),
        ["leg_low_vol", "leg_small_mv"],
        {"industry": "industry_l1", "exclude": ["leg_small_mv"]},
    )
    assert out["leg_small_mv"].to_list() == _frame()["leg_small_mv"].to_list()
    assert out["leg_low_vol"].to_list() != _frame()["leg_low_vol"].to_list()


def test_universe_mask_limits_statistics():
    frame = _frame().with_columns(
        pl.Series("_u", [True, True, False, True, True, False])
    )
    out = neutralize_scoring_columns(
        frame,
        ["leg_low_vol"],
        {"industry": "industry_l1"},
        universe_col="_u",
    )
    # I1 行业: 只 a/b 参与统计 → 行业 z-score (mean 1.5, std 0.7071), 第三条 (非候选) 置空
    values = out["leg_low_vol"].to_list()
    assert values[0] == pytest.approx(-0.7071068, abs=1e-6)
    assert values[1] == pytest.approx(0.7071068, abs=1e-6)
    assert values[2] is None


def test_missing_column_fail_closed():
    with pytest.raises(ValueError, match="缺少列"):
        neutralize_scoring_columns(
            _frame(), ["leg_low_vol"], {"industry": "not_there"}
        )
    with pytest.raises(ValueError, match="缺少列"):
        neutralize_scoring_columns(
            _frame(), ["leg_low_vol"], {"styles": ["not_there"]}
        )


def test_effective_neutralize_merges_and_validates():
    spec = effective_neutralize(
        {"neutralize": {"industry": "industry_l1", "styles": ["log_float_mv"]}},
        {"neutralize": {"styles": [], "exclude": ["leg_small_mv"]}},
    )
    assert spec == {
        "industry": "industry_l1",
        "styles": [],
        "exclude": ["leg_small_mv"],
        "shrink": 1.0,
    }
    assert neutralize_enabled(spec) is True
    assert neutralize_enabled(effective_neutralize(None, None)) is False
    with pytest.raises(ValueError):
        effective_neutralize(None, {"neutralize": {"styles": ["a", "b"]}})
    with pytest.raises(ValueError):
        effective_neutralize(None, {"neutralize": {"industry": 3}})


def test_engine_apply_scoring_with_neutralize():
    from app.strategy.engine import StrategyEngine

    frame = _frame()
    out = StrategyEngine._apply_scoring(
        frame,
        {"leg_low_vol": 1.0},
        neutralize={"industry": "industry_l1"},
    )
    score = out["score"].to_list()
    assert all(0.0 <= s <= 100.0 for s in score)
    assert math.isfinite(sum(score))


def test_industry_neutrality_survives_full_cross_section_minmax():
    """R17b 回归: 中性化腿再做全截面 min-max 后, 与行业哑变量仍正交 (组合不超配高均值行业)。"""
    frame = pl.DataFrame(
        {
            "date": ["2024-01-02"] * 8,
            "symbol": list("abcdefgh"),
            "industry_l1": ["I1"] * 4 + ["I2"] * 4,
            # 行业水平/离散度都不同; 两个行业内名次相同
            "leg": [1.0, 2.0, 3.0, 4.0, 21.0, 22.0, 24.0, 28.0],
        }
    )
    neutral = neutralize_scoring_columns(frame, ["leg"], {"industry": "industry_l1"})
    v = neutral["leg"]
    col_min = v.min()
    col_max = v.max()
    normalized = (v - col_min) / (col_max - col_min)  # 下游全截面 min-max
    out = neutral.with_columns(normalized.alias("norm"))
    means = out.group_by("industry_l1").agg(pl.col("norm").mean())["norm"].to_list()
    # 关键不变量: 各行业在最终评分上的均值相等 → 组合不会系统性超配任一行业
    assert abs(means[0] - means[1]) < 1e-9, means
    # 行业内名次保留 (两行业各自单调)
    for ind in ("I1", "I2"):
        vals = out.filter(pl.col("industry_l1") == ind)["norm"].to_list()
        assert vals == sorted(vals)


def test_shrink_scales_removed_component():
    """R17c: shrink 在全量去均值+z-score 之后线性收缩; λ=0 保持原值, λ=1 行业均值相等。"""
    frame = pl.DataFrame(
        {
            "date": ["2024-01-02"] * 6,
            "symbol": list("abcdef"),
            "industry_l1": ["I1", "I1", "I1", "I2", "I2", "I2"],
            "leg": [1.0, 2.0, 3.0, 8.0, 12.0, 16.0],
        }
    )

    def means(shrink):
        out = neutralize_scoring_columns(
            frame, ["leg"], {"industry": "industry_l1", "shrink": shrink}
        )
        g = out.group_by("industry_l1").agg(pl.col("leg").mean())
        return dict(zip(g["industry_l1"].to_list(), g["leg"].to_list()))

    m0, m5, m1 = means(0.0), means(0.5), means(1.0)
    assert m0["I1"] == pytest.approx(2.0) and m0["I2"] == pytest.approx(12.0)
    # λ=1: 两个行业均值相等 (完整中性化)
    assert m1["I1"] == pytest.approx(m1["I2"], abs=1e-9)
    # λ=0.5: 高均值行业 (I2) 被拉低, 低均值行业 (I1) 也向 0 靠 → 行业差距收缩
    assert m0["I1"] > m5["I1"] > m1["I1"] - 1e-9
    assert m5["I2"] < m0["I2"]


def test_effective_neutralize_shrink_validation():
    assert effective_neutralize(None, {"neutralize": {"industry": "i", "shrink": 0.5}})[
        "shrink"
    ] == pytest.approx(0.5)
    assert effective_neutralize({"neutralize": {"industry": "i"}}, None)["shrink"] == 1.0
    for bad in (-0.1, 1.1, "x", True):
        with pytest.raises(ValueError):
            effective_neutralize(None, {"neutralize": {"industry": "i", "shrink": bad}})


def test_industry_map_fail_closed(tmp_path):
    """行业表缺失或无覆盖时必须报错, 不得静默返回空映射。"""
    from app.services.industry_map import load_industry_l1

    with pytest.raises(ValueError):
        load_industry_l1(tmp_path)  # 无 ext_hy_ths 表

    ext = tmp_path / "ext_data" / "ext_hy_ths"
    ext.mkdir(parents=True)
    pl.DataFrame(
        {
            "股票代码": ["600000.SH"],
            "symbol": ["600000.SH"],
            "code": ["600000"],
            "所属同花顺行业": ["银行-股份制银行"],
        }
    ).write_parquet(ext / "part.parquet")
    mapping = load_industry_l1(tmp_path)
    assert mapping.filter(pl.col("symbol") == "600000.SH")["industry_l1"].to_list() == ["银行"]

    frame = pl.DataFrame({"symbol": ["000001.SZ"], "date": ["2024-01-02"], "leg": [1.0]})
    from app.services.industry_map import attach_industry_l1

    joined = attach_industry_l1(frame, tmp_path)
    assert joined["industry_l1"].to_list() == [None]  # 未被行业表覆盖 → 左联为空
    out = neutralize_scoring_columns(
        joined, ["leg"], {"industry": "industry_l1"}
    )
    # 行业缺失 → 中性化值置 0 (std 缺失分支), 不会被当作"未中性化的强信号"进入选股
    assert out["leg"].to_list() == [0.0]
