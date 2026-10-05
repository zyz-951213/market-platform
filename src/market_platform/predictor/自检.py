"""配置自检 —— 把"看起来生效、实际失效"的设置显式列出来(评审 R12/R13/R9 的披露要求)。

为什么需要:统一夏普(unified_sharpe)主路径在 `continue` 之前就决定了仓位并结算,
`stop_loss` / `take_profit` / 波动目标上限 / 双频闸门等 legacy 风控**不会执行**;
而 legacy 路径上又存在若干"用未来数据"的开关。这些都属于"配置与实际行为不一致",
必须能被一眼看见,而不是靠读代码发现。

用法:
    from market_platform.predictor.自检 import 运行自检
    报告 = 运行自检()          # {检查项: [问题...]}

CLI:`market-platform 自检`
"""

from __future__ import annotations

from typing import Any

from market_platform.predictor.配置 import get_config

# unified_sharpe 主路径(引擎.py 在 unified 分支 continue/return 之前完成结算)下
# 不会执行的风控/闸门项 —— 键名来自 回测/引擎.py 的 strategy 段
UNIFIED_失效项 = {
    "stop_loss": "止损(统一账本无止损)",
    "take_profit": "止盈",
    "trailing_stop": "移动止损",
    "vol_target": "波动目标上限",
    "dual_freq_gate": "双频闸门",
    "disagreement_gate": "分歧闸门",
    "selective_size": "选择性加仓",
}


def _strategy_cfg() -> dict:
    return (get_config().get("backtest", {}) or {}).get("strategy", {}) or {}


def 检查统一夏普失效风控() -> list[str]:
    """unified_sharpe 模式下仍配置了 legacy 风控 → 列出(它们不会生效)。"""
    配置 = get_config()
    模式 = str((配置.get("portfolio", {}) or {}).get("allocation_mode", "legacy")).strip().lower()
    if 模式 not in {"unified_sharpe", "unified", "sharpe"}:
        return []
    策略 = _strategy_cfg()
    问题 = []
    for 键, 说明 in UNIFIED_失效项.items():
        值 = 策略.get(键)
        if 值 in (None, False, 0, 0.0, {}, []):
            continue
        # 字典型开关以 enabled 为准(避免把"写着但关着"的项也报出来)
        if isinstance(值, dict) and not 值.get("enabled", False):
            continue
        摘要 = f"{值}" if not isinstance(值, dict) else f"enabled={值.get('enabled')}"
        问题.append(f"backtest.strategy.{键} = {摘要} 已配置,但 unified_sharpe 主路径不执行 → {说明}")
    return 问题


def 检查前视开关() -> list[str]:
    """列出当前配置里**主动打开**的前视/非因果开关。"""
    配置 = get_config()
    问题: list[str] = []

    分桶 = (_strategy_cfg().get("partition_overlay_scale") or {})
    if 分桶.get("enabled") and str(分桶.get("source", "causal")).lower() == "global":
        问题.append(
            "backtest.strategy.partition_overlay_scale.source=global:使用全历史命中率调仓(前视)"
        )
    if _strategy_cfg().get("stop_loss_uses_future_label"):
        问题.append("backtest.strategy.stop_loss_uses_future_label=true:止损使用未来一期收益(前视)")

    切分 = ((配置.get("model", {}) or {}).get("split", {}) or {})
    if str(切分.get("split_method", "")).lower() == "cycle":
        问题.append(
            "model.split.split_method=cycle:验证集取历史上零散周期段,训练集含验证期之后数据(时间泄漏)"
        )

    变换 = ((配置.get("model", {}) or {}).get("transformer", {}) or {})
    if 变换.get("export_features") and not 变换.get("export_walk_forward", True):
        问题.append(
            "model.transformer.export_walk_forward=false:tf_proba 仅按 checkpoint 训练结束日切分,"
            "若 checkpoint 无训练区间元数据则无法保证样本外"
        )

    if int((配置.get("data", {}) or {}).get("macro_publish_lag_days", 15)) <= 0:
        问题.append("data.macro_publish_lag_days<=0:月频宏观按月初可见(前视)")

    return 问题


def 检查口径一致性() -> list[str]:
    """两套引擎/两个口径不可比之类的提醒。"""
    配置 = get_config()
    问题: list[str] = []
    模式 = str((配置.get("portfolio", {}) or {}).get("allocation_mode", "legacy")).strip().lower()
    if 模式 in {"unified_sharpe", "unified", "sharpe"}:
        问题.append(
            "提示:回测按 t→t+1 收益结算,模拟盘按当日已实现收益结算,两者净值/夏普**不可直接比较**"
        )
    return 问题


def 运行自检() -> dict[str, list[str]]:
    """返回分组问题清单;空列表表示该组通过。"""
    返回 = {
        "统一夏普下失效的风控": 检查统一夏普失效风控(),
        "前视开关": 检查前视开关(),
        "口径提示": 检查口径一致性(),
    }
    return 返回


def 打印自检(输出: Any = print) -> int:
    """打印自检结果;有"失效风控"或"前视开关"时返回 1。"""
    报告 = 运行自检()
    有问题 = 0
    for 组, 条目 in 报告.items():
        if not 条目:
            输出(f"✓ {组}:正常")
            continue
        输出(f"⚠️ {组}:")
        for 条 in 条目:
            输出(f"    - {条}")
        if 组 != "口径提示":
            有问题 = 1
    return 有问题


__all__ = [
    "运行自检",
    "打印自检",
    "检查统一夏普失效风控",
    "检查前视开关",
    "检查口径一致性",
]
