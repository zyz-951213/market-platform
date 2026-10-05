"""随机性统一入口 —— 全仓唯一的随机种子设置。

为什么需要:评审发现 torch/numpy/random 全仓没有任何 seed(仅集合元模型里有一处
硬编码 `seed=0`),导致同一份数据跑两次结果不同;Transformer 训练还用
`shuffle=True` 的 DataLoader,进一步放大不可复现性。

用法:
    from market_platform.core.随机 import 置随机种子, 取种子
    置随机种子()                     # 用配置默认种子(42)
    置随机种子(取种子() + 块序号)      # 走查重训时按块派生,保证逐块可复现

注意:本模块只做"显式设定",不改动算法;CUDA 上仍可能因非确定性算子产生微小差异
(故只开 cudnn 的 deterministic 开关,不启用 use_deterministic_algorithms,
以免个别算子直接报错)。
"""

from __future__ import annotations

import os
import random

默认种子 = 42
_环境变量 = "MARKET_SEED"


def 取种子() -> int:
    """取全局种子:环境变量 MARKET_SEED 优先,其次默认 42。"""
    raw = os.environ.get(_环境变量, "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return 默认种子


def 置随机种子(seed: int | None = None) -> int:
    """设定 python / numpy / torch 的随机种子,返回实际使用的种子。"""
    实际 = 取种子() if seed is None else int(seed)

    random.seed(实际)

    try:
        import numpy as np

        np.random.seed(实际 % (2**32))
    except ImportError:  # pragma: no cover - numpy 是硬依赖
        pass

    try:
        import torch

        torch.manual_seed(实际)
        if torch.cuda.is_available():  # pragma: no cover - 本机无 CUDA
            torch.cuda.manual_seed_all(实际)
        # cudnn 确定性(不启用 use_deterministic_algorithms,避免个别算子直接报错)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
    except ImportError:  # pragma: no cover - torch 为可选重依赖
        pass

    return 实际


def 取生成器(seed: int | None = None):
    """给 DataLoader(shuffle=True) 用的确定性生成器(torch 存在时)。"""
    try:
        import torch

        g = torch.Generator()
        g.manual_seed(取种子() if seed is None else int(seed))
        return g
    except ImportError:  # pragma: no cover
        return None


__all__ = ["置随机种子", "取种子", "取生成器", "默认种子"]
