"""数据模块入口 — 组装获取、清洗、特征工程全流程"""

from market_platform.predictor.采集.拉取器 import fetch_index_daily, load_local_data, load_or_fetch_index
from market_platform.predictor.采集.清洗 import clean_daily_data
from market_platform.predictor.分析.特征.特征工程 import build_feature_pipeline
from market_platform.predictor.采集.拆分 import train_val_split, log_split_summary
from market_platform.predictor.采集.重采样 import resample_bars
from market_platform.predictor.采集.额外特征 import merge_extra_features
from market_platform.predictor.采集.基本面 import merge_fundamental_data, add_fundamental_features, is_financial_enabled
from market_platform.predictor.采集.外盘 import merge_external_data, is_external_enabled
from market_platform.predictor.采集.市场信号 import merge_market_signals, is_market_signals_enabled
from market_platform.predictor.采集.新闻 import merge_news_sentiment, is_news_sentiment_enabled
from market_platform.predictor.分析.提示词.构建器 import build_prompt_from_row, build_analysis_prompt

__all__ = [
    "fetch_index_daily",
    "load_local_data",
    "load_or_fetch_index",
    "clean_daily_data",
    "build_feature_pipeline",
    "train_val_split",
    "log_split_summary",
    "resample_bars",
    "merge_extra_features",
    "merge_fundamental_data",
    "add_fundamental_features",
    "is_financial_enabled",
    "merge_external_data",
    "is_external_enabled",
    "merge_market_signals",
    "is_market_signals_enabled",
    "merge_news_sentiment",
    "is_news_sentiment_enabled",
    "build_prompt_from_row",
    "build_analysis_prompt",
]
