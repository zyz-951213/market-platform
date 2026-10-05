"""模拟盘：统一模式下应写入各袖口贡献。"""

from __future__ import annotations

import pandas as pd


def test_compute_contrib_fields_roundtrip():
    from market_platform.predictor.分析.资金.统一夏普配置 import compute_unified_period_pnl

    weights = {
        "csi300": 0.2, "nasdaq": 0.2, "gold": 0.2,
        "bond": 0.2, "mm": 0.1, "repo": 0.1,
    }
    day = pd.Series({
        "csi300_ret": 0.01,
        "nasdaq_ret": -0.02,
        "gold_ret": 0.005,
        "bond_ret": 0.001,
        "mm_ret": 0.0001,
        "repo_daily_ret": 0.00005,
    })
    total, offense, defensive, cost = compute_unified_period_pnl(
        weights, day, prev_weights=weights, commission=0.0, slippage=0.0,
    )
    gross = (
        0.2 * 0.01 + 0.2 * (-0.02) + 0.2 * 0.005
        + 0.2 * 0.001 + 0.1 * 0.0001 + 0.1 * 0.00005
    )
    assert abs(cost) < 1e-15
    assert abs(total - gross) < 1e-12
    assert abs(offense - (0.2 * 0.01 + 0.2 * (-0.02))) < 1e-12
    assert abs(defensive - (total - offense)) < 1e-12


def test_strategy_summary_transformer_status():
    from market_platform.predictor.配置 import reload_config
    from market_platform.predictor.接口.策略摘要 import build_strategy_summary

    reload_config()
    s = build_strategy_summary()
    assert "transformer_enabled" in s
    assert "transformer_status" in s
    assert "transformer_checkpoint" in s
    # 本仓库应已有 checkpoint；enabled 已打开
    assert s["transformer_checkpoint"] is True
    assert s["transformer_enabled"] is True
    assert "启用" in s["transformer_status"]
