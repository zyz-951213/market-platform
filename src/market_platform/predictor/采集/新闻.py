"""
宏观新闻情感特征 — 数值汇总供特征工程，摘要仍由 external_sources 提供给 LLM
"""

from __future__ import annotations


import re
from typing import List, Optional

import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.配置 import get_config
from market_platform.predictor.采集.外盘 import _load_cache, get_external_config

_POSITIVE_WORDS = (
    "增长", "回升", "利好", "上调", "超预期", "扩张", "改善", "强劲", "复苏",
    "创新高", "净流入", "宽松", "降息", "降准", "突破", "上涨", "企稳",
)
_NEGATIVE_WORDS = (
    "下滑", "回落", "利空", "下调", "不及预期", "收缩", "恶化", "疲软", "衰退",
    "创新低", "净流出", "收紧", "加息", "违约", "下跌", "承压", "风险",
)

_EMBEDDER = None


def get_news_sentiment_config() -> dict:
    return get_config().get("features", {}).get("news_sentiment", {})


def is_news_sentiment_enabled() -> bool:
    return bool(get_news_sentiment_config().get("enabled", False))


def _lexicon_score(text: str) -> float:
    if not text:
        return 0.0
    pos = sum(1 for w in _POSITIVE_WORDS if w in text)
    neg = sum(1 for w in _NEGATIVE_WORDS if w in text)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total


def _get_embedder():
    global _EMBEDDER
    cfg = get_news_sentiment_config()
    if not cfg.get("use_embeddings", False):
        return None
    if _EMBEDDER is not None:
        return _EMBEDDER
    try:
        from sentence_transformers import SentenceTransformer

        model_name = cfg.get("embedding_model", "shibing624/text2vec-base-chinese")
        _EMBEDDER = SentenceTransformer(model_name)
        logger.info(f"📰 新闻情感嵌入模型已加载: {model_name}")
        return _EMBEDDER
    except ImportError:
        logger.warning("⚠️ sentence-transformers 未安装，新闻情感回退为词典法")
        return None
    except Exception as e:
        logger.warning(f"⚠️ 嵌入模型加载失败，回退词典法: {e}")
        return None


def _embedding_sentiment(texts: List[str]) -> List[float]:
    """用与基准句的余弦相似度差估计情感（无监督近似）"""
    embedder = _get_embedder()
    if embedder is None or not texts:
        return [_lexicon_score(t) for t in texts]

    anchor_pos = "经济数据向好，市场信心增强，政策支持力度加大"
    anchor_neg = "经济数据走弱，市场担忧加剧，风险事件频发"
    try:
        vecs = embedder.encode(texts + [anchor_pos, anchor_neg], normalize_embeddings=True)
        pos_vec = vecs[-2]
        neg_vec = vecs[-1]
        scores = []
        for vec in vecs[:-2]:
            sim_pos = float(np.dot(vec, pos_vec))
            sim_neg = float(np.dot(vec, neg_vec))
            scores.append(sim_pos - sim_neg)
        return scores
    except Exception as e:
        logger.debug(f"嵌入情感计算失败: {e}")
        return [_lexicon_score(t) for t in texts]


def _load_macro_news_df() -> pd.DataFrame:
    cfg = get_external_config().get("macro_news", {})
    cache_name = cfg.get("cache_file", "external_macro_news.parquet")
    cached = _load_cache(cache_name)
    if cached is None or cached.empty:
        return pd.DataFrame(columns=["date", "event", "region", "importance"])
    return cached


def _event_text(row: pd.Series) -> str:
    parts = [
        str(row.get("region", "")),
        str(row.get("event", "")),
        str(row.get("actual", "")),
        str(row.get("forecast", "")),
    ]
    text = " ".join(p for p in parts if p and p != "nan")
    imp = str(row.get("importance", ""))
    if imp in {"高", "3", "★★★"}:
        text = f"重要 {text}"
    return text.strip()


def aggregate_news_sentiment_daily(news: pd.DataFrame) -> pd.DataFrame:
    """按交易日汇总新闻情感"""
    if news is None or news.empty:
        return pd.DataFrame(columns=[
            "date", "macro_news_sentiment_mean", "macro_news_sentiment_pos_ratio",
            "macro_news_sentiment_strength",
        ])

    cfg = get_news_sentiment_config()
    news = news.copy()
    news["date"] = pd.to_datetime(news["date"]).dt.normalize()

    rows: list[dict] = []
    for day, grp in news.groupby("date"):
        texts = [_event_text(r) for _, r in grp.iterrows()]
        scores = _embedding_sentiment(texts)
        if not scores:
            continue
        arr = np.array(scores, dtype=float)
        rows.append({
            "date": day,
            "macro_news_sentiment_mean": float(np.mean(arr)),
            "macro_news_sentiment_pos_ratio": float((arr > 0).mean()),
            "macro_news_sentiment_strength": float(np.mean(np.abs(arr))),
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    smooth = int(cfg.get("smooth_window", 3))
    if smooth > 1:
        for col in ("macro_news_sentiment_mean", "macro_news_sentiment_pos_ratio", "macro_news_sentiment_strength"):
            out[col] = out[col].rolling(smooth, min_periods=1).mean()
    return out.sort_values("date").reset_index(drop=True)


def merge_news_sentiment(df: pd.DataFrame) -> pd.DataFrame:
    """将新闻情感数值特征合并到行情表"""
    if not is_news_sentiment_enabled():
        return df

    news = _load_macro_news_df()
    if news.empty:
        logger.warning("⚠️ 宏观新闻缓存为空，跳过情感特征（请先拉取 external macro_news）")
        return df

    start = pd.Timestamp(df["date"].min()).normalize()
    end = pd.Timestamp(df["date"].max()).normalize()
    news = news[(pd.to_datetime(news["date"]) >= start) & (pd.to_datetime(news["date"]) <= end)]

    daily = aggregate_news_sentiment_daily(news)
    if daily.empty:
        return df

    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.merge(daily, on="date", how="left")
    ffill_limit = int(get_news_sentiment_config().get("ffill_limit", 5))
    for col in daily.columns:
        if col != "date":
            out[col] = out[col].ffill(limit=ffill_limit)
    logger.info(
        f"🔗 新闻情感: {out['macro_news_sentiment_mean'].notna().sum()}/{len(out)} 日有数据"
    )
    return out
