"""
额外因子数据 — 财务因子 + 外盘/新闻 + 市场信号 + 新闻情感
"""

from market_platform.predictor.采集.基本面 import merge_fundamental_data, is_financial_enabled
from market_platform.predictor.采集.外盘 import merge_external_data, is_external_enabled
from market_platform.predictor.采集.市场信号 import merge_market_signals, is_market_signals_enabled
from market_platform.predictor.采集.新闻 import merge_news_sentiment, is_news_sentiment_enabled


def merge_extra_features(df):
    """合并所有非 OHLCV 外部因子"""
    if is_financial_enabled():
        df = merge_fundamental_data(df)
    if is_external_enabled():
        df = merge_external_data(df)
    if is_market_signals_enabled():
        df = merge_market_signals(df)
    if is_news_sentiment_enabled():
        df = merge_news_sentiment(df)
    return df
