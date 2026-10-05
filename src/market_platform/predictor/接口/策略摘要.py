"""兼容入口 —— 实现已下移到 `market_platform.predictor.分析.指标.策略摘要`。

分层修正(评审 C1):该模块由 config + 分析层(资金/闲置管理)组装策略口径,
属分析件;放在接口层会让提示词构建器等分析件反向依赖接口层。
"""

from __future__ import annotations

from market_platform.predictor.分析.指标.策略摘要 import *  # noqa: F401,F403
from market_platform.predictor.分析.指标.策略摘要 import (  # noqa: F401
    build_strategy_summary,
)
