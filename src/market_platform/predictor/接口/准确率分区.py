"""兼容入口 —— 实现已下移到 `market_platform.predictor.分析.指标.准确率分区`。

分层修正(评审 C1):该模块是"用回测记录算指标"的分析件,应属分析层;
接口层只做 HTTP 包装。此处保留同名再导出以兼容既有导入。
"""

from __future__ import annotations

from market_platform.predictor.分析.指标.准确率分区 import *  # noqa: F401,F403
from market_platform.predictor.分析.指标.准确率分区 import (  # noqa: F401
    _conf_bucket,
    _enrich_features,
    compute_accuracy_partitions,
)
