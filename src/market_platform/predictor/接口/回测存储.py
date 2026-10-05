"""兼容入口 —— 实现已下移到 `market_platform.predictor.存储.回测存储`。

分层修正(评审 C1):回测存储只依赖 核心 + pandas/json,本质是**存储层**,
放在接口层会让分析层反向依赖接口层。此处保留同名再导出,避免破坏既有调用与外部脚本。
"""

from __future__ import annotations

from market_platform.predictor.存储.回测存储 import *  # noqa: F401,F403
from market_platform.predictor.存储.回测存储 import (  # noqa: F401 — 显式再导出常用符号
    load_backtest_for_provider,
    load_latest_backtest,
    merge_backtest_signals,
    strategy_fingerprint,
)
