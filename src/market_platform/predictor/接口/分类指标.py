"""兼容入口 —— 实现已下移到 `market_platform.predictor.分析.指标.分类指标`。

分层修正(评审 C1):纯指标计算属分析层;此前被 存储/回测存储 反向引用。
"""

from __future__ import annotations

from market_platform.predictor.分析.指标.分类指标 import *  # noqa: F401,F403
from market_platform.predictor.分析.指标.分类指标 import (  # noqa: F401
    compute_classification_metrics,
)
