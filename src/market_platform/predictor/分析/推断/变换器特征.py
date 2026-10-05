"""
Transformer 衍生特征 — 将短周期动量 Transformer 概率输出并入特征表
"""

from __future__ import annotations


import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.配置 import get_config


def is_transformer_features_enabled() -> bool:
    cfg = get_config().get("model", {}).get("transformer", {})
    return bool(cfg.get("export_features", cfg.get("as_lgb_features", False)))


def add_transformer_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """若存在已训练 checkpoint，批量生成 Transformer 概率特征列"""
    if not is_transformer_features_enabled():
        return df

    try:
        from market_platform.predictor.分析.推断.变换器 import TransformerPredictor
    except ImportError as e:
        logger.warning(f"⚠️ Transformer 模型模块不可用: {e}")
        return df

    try:
        predictor = TransformerPredictor()
    except Exception as e:
        logger.warning(f"⚠️ TransformerPredictor 初始化失败: {e}")
        return df

    if not predictor.try_load():
        logger.warning(
            "⚠️ Transformer 特征跳过：无 checkpoint，请先运行 "
            "market-predictor pipeline -- --step train --model transformer"
        )
        return df

    try:
        proba, pred = predictor.predict_proba_aligned(df)
    except Exception as e:
        logger.warning(f"⚠️ Transformer 推理失败: {e}")
        return df

    out = df.copy()
    out["tf_proba_down"] = proba[:, 0]
    out["tf_proba_flat"] = proba[:, 1]
    out["tf_proba_up"] = proba[:, 2]
    out["tf_pred_direction"] = pred
    valid = int(np.isfinite(proba[:, 1]).sum())
    logger.info(f"🔗 Transformer 衍生特征: {valid}/{len(out)} 行有效")
    return out
