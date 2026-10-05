"""统一夏普：手续费与降换手相关单测。"""

from __future__ import annotations

import pandas as pd

from market_platform.predictor.分析.资金.统一夏普配置 import (
    FEE_SLEEVES,
    UnifiedSharpeAllocator,
    compute_unified_period_pnl,
)


def _day(**rets) -> pd.Series:
    base = {
        "csi300_ret": 0.0,
        "nasdaq_ret": 0.0,
        "gold_ret": 0.0,
        "bond_ret": 0.0,
        "mm_ret": 0.0,
        "repo_daily_ret": 0.0,
    }
    base.update(rets)
    return pd.Series(base)


def test_fee_charges_all_tradable_sleeves_not_only_csi():
    """黄金↔国债轮动也应计费（不再只扣 CSI300）。"""
    prev = {"csi300": 0.3, "nasdaq": 0.2, "gold": 0.2, "bond": 0.2, "mm": 0.05, "repo": 0.05}
    cur = {"csi300": 0.3, "nasdaq": 0.2, "gold": 0.3, "bond": 0.1, "mm": 0.05, "repo": 0.05}
    commission, slippage = 0.0003, 0.0005
    rate = commission + slippage
    total, _, _, cost = compute_unified_period_pnl(
        cur, _day(gold_ret=0.01, bond_ret=0.0),
        prev_weights=prev, commission=commission, slippage=slippage,
    )
    # |Δgold|+|Δbond| = 0.2
    assert abs(cost - 0.2 * rate) < 1e-12
    assert abs(total - (0.3 * 0.01 - cost)) < 1e-9


def test_repo_not_in_fee_base():
    prev = {k: 0.0 for k in ("csi300", "nasdaq", "gold", "bond", "mm", "repo")}
    prev["mm"] = 0.1
    prev["repo"] = 0.9
    cur = dict(prev)
    cur["mm"] = 0.0
    cur["repo"] = 1.0
    _, _, _, cost = compute_unified_period_pnl(
        cur, _day(), prev_weights=prev, commission=0.0003, slippage=0.0005,
    )
    # 仅 mm 变动 0.1 计费；repo 不计
    assert abs(cost - 0.1 * 0.0008) < 1e-12
    assert "repo" not in FEE_SLEEVES


def test_blend_deadband_keeps_weights():
    alloc = UnifiedSharpeAllocator(params={
        "rebalance_step": 1.0,
        "trade_deadband": 0.05,
        "weight_ema_span": 0,
        "group_caps": {
            "offense": {"min": 0.2, "max": 0.8, "anchor": 0.5},
            "defense": {"min": 0.1, "max": 0.5, "anchor": 0.3},
            "cashflow": {"min": 0.05, "max": 0.3, "anchor": 0.2},
        },
        "risk_off": {"enabled": False},
        "risk_on": {"enabled": False},
        "llm_tilt": {"enabled": False},
    })
    from market_platform.predictor.分析.资金.统一夏普配置 import PortfolioWeights

    prev_w = {"csi300": 0.25, "nasdaq": 0.25, "gold": 0.2, "bond": 0.15, "mm": 0.075, "repo": 0.075}
    alloc._last_weights = PortfolioWeights(weights=prev_w, group_weights={
        "offense": 0.5, "defense": 0.35, "cashflow": 0.15,
    })
    target = dict(prev_w)
    target["csi300"] = 0.26
    target["nasdaq"] = 0.24
    out = alloc._blend_toward_prev(target, risk_off=False)
    assert out == prev_w


def test_risk_off_bypasses_partial_step():
    alloc = UnifiedSharpeAllocator(params={
        "rebalance_step": 0.1,
        "trade_deadband": 0.0,
        "weight_ema_span": 0,
        "group_caps": {
            "offense": {"min": 0.2, "max": 0.8, "anchor": 0.5},
            "defense": {"min": 0.1, "max": 0.5, "anchor": 0.3},
            "cashflow": {"min": 0.05, "max": 0.3, "anchor": 0.2},
        },
    })
    from market_platform.predictor.分析.资金.统一夏普配置 import PortfolioWeights

    prev_w = {"csi300": 0.4, "nasdaq": 0.2, "gold": 0.15, "bond": 0.15, "mm": 0.05, "repo": 0.05}
    alloc._last_weights = PortfolioWeights(weights=prev_w, group_weights={
        "offense": 0.6, "defense": 0.3, "cashflow": 0.1,
    })
    target = {"csi300": 0.1, "nasdaq": 0.05, "gold": 0.35, "bond": 0.3, "mm": 0.1, "repo": 0.1}
    out = alloc._blend_toward_prev(target, risk_off=True)
    assert out == target
