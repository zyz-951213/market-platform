from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from market_platform.agents.settings import AppConfig
from market_platform.agents.分析.特征.分钟 import compute_minute_features, summarize_minute_to_daily
from market_platform.agents.分析.特征.日频 import build_daily_feature_matrix
from market_platform.agents.核心.工具.读写 import ensure_dir, load_parquet, save_parquet
from market_platform.agents.核心.路径.数据 import resolve_data_dir, symbol_data_dir
from market_platform.agents.采集.拉取器 import MarketDataFetcher
from market_platform.agents.采集.数据源 import expand_daily_to_minute_proxy
from market_platform.agents.采集.新闻 import NewsFetcher, generate_demo_news

logger = logging.getLogger(__name__)


class DataPipeline:
    """数据管线：拉取 → 特征 → 合并 → 持久化。"""

    def __init__(self, config: AppConfig):
        self.config = config
        self.fetcher = MarketDataFetcher(config.data)
        self.news_fetcher = NewsFetcher(config.data)

    @property
    def symbol_dir(self) -> Path:
        d = self.config.data
        existing = resolve_data_dir(d.root, d.symbol)
        if existing is not None:
            return ensure_dir(existing)
        return ensure_dir(
            symbol_data_dir(
                d.root,
                d.symbol,
                sector=d.sector,
                name=d.name,
                group_by_sector=d.group_by_sector,
                group_by_l2=d.group_by_l2,
                name_as_folder=d.name_as_folder,
                data_root=d.root,
            )
        )

    def run_fetch_news(self, demo: bool = False) -> Path:
        if demo:
            minute_path = self.symbol_dir / "minute_bars.parquet"
            if minute_path.exists():
                minute = load_parquet(minute_path)
                dates = minute["date"]
            else:
                dataset = self.fetcher.fetch_demo_dataset()
                dates = dataset["minute"]["date"]
            news = generate_demo_news(dates, symbol=self.config.data.symbol)
        else:
            news = self.news_fetcher.fetch_stock_news()

        path = self.symbol_dir / "news.parquet"
        save_parquet(news, path)
        logger.info("Saved %d news rows -> %s", len(news), path)
        return path

    def run_fetch(
        self,
        use_chunked_minute: bool = True,
        demo: bool = False,
        fallback_daily_proxy: bool = True,
    ) -> dict[str, Path]:
        """拉取并缓存原始数据，返回各文件路径。"""
        paths: dict[str, Path] = {}

        if demo:
            dataset = self.fetcher.fetch_demo_dataset()
            name_map = {"minute": "minute_bars.parquet", "daily": "daily_bars.parquet"}
            for key, df in dataset.items():
                filename = name_map.get(key, f"{key}.parquet")
                path = self.symbol_dir / filename
                save_parquet(df, path)
                paths[key] = path
                logger.info("Saved demo %s (%d rows) -> %s", key, len(df), path)
            paths["news"] = self.run_fetch_news(demo=True)
            return paths

        if use_chunked_minute:
            try:
                minute = self.fetcher.fetch_minute_by_date_range(
                    start=self.config.data.start_date,
                    end=self.config.resolved_end_date(),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Minute fetch failed: %s", exc)
                if fallback_daily_proxy:
                    daily = self.fetcher.fetch_daily_bars()
                    minute = expand_daily_to_minute_proxy(daily, self.config.data.symbol)
                    logger.info("Using daily-expanded minute proxy (%d bars)", len(minute))
                else:
                    raise
        else:
            minute = self.fetcher.fetch_minute_bars()

        minute_path = self.symbol_dir / "minute_bars.parquet"
        save_parquet(minute, minute_path)
        paths["minute"] = minute_path
        logger.info("Saved %d minute bars -> %s", len(minute), minute_path)

        daily_path = self.symbol_dir / "daily_bars.parquet"
        daily = self.fetcher.fetch_daily_bars()
        save_parquet(daily, daily_path)
        paths["daily"] = daily_path

        fund_flow = pd.DataFrame()
        try:
            fund_flow = self.fetcher.fetch_individual_fund_flow(daily=daily, minute=minute)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fund flow fetch failed, continue with empty frame: %s", exc)
        fund_path = self.symbol_dir / "fund_flow.parquet"
        save_parquet(fund_flow, fund_path)
        paths["fund_flow"] = fund_path

        north = self.fetcher.fetch_northbound_flow()
        north_path = self.symbol_dir / "northbound.parquet"
        save_parquet(north, north_path)
        paths["northbound"] = north_path

        margin = self.fetcher.fetch_margin_summary()
        margin_path = self.symbol_dir / "margin.parquet"
        save_parquet(margin, margin_path)
        paths["margin"] = margin_path

        try:
            paths["news"] = self.run_fetch_news(demo=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("News fetch skipped: %s", exc)

        logger.info(
            "Data sources: daily=%s minute=%s fund_flow=%s",
            self.fetcher.last_daily_source or "unknown",
            self.fetcher.last_minute_source or "unknown",
            self.fetcher.last_fund_flow_source or "unknown",
        )

        return paths

    def run_features(self, minute_path: Path | None = None) -> pd.DataFrame:
        """从分钟线生成日频特征矩阵（NMF 输入 X）。"""
        minute_path = minute_path or (self.symbol_dir / "minute_bars.parquet")
        minute = load_parquet(minute_path)
        minute = compute_minute_features(minute, self.config.features.minute)
        minute_daily = summarize_minute_to_daily(minute)

        minute_daily_path = self.symbol_dir / "minute_daily_features.parquet"
        save_parquet(minute_daily, minute_daily_path)

        fund_flow = load_parquet(self.symbol_dir / "fund_flow.parquet")
        north = load_parquet(self.symbol_dir / "northbound.parquet")
        margin = load_parquet(self.symbol_dir / "margin.parquet")

        feature_matrix = build_daily_feature_matrix(
            minute_daily=minute_daily,
            fund_flow=fund_flow,
            northbound=north,
            margin=margin,
        )

        matrix_path = self.symbol_dir / "daily_feature_matrix.parquet"
        save_parquet(feature_matrix, matrix_path)
        logger.info("Feature matrix shape: %s -> %s", feature_matrix.shape, matrix_path)
        return feature_matrix

    def load_or_build_feature_matrix(self, refresh: bool = False) -> pd.DataFrame:
        matrix_path = self.symbol_dir / "daily_feature_matrix.parquet"
        if matrix_path.exists() and not refresh:
            return load_parquet(matrix_path)

        minute_path = self.symbol_dir / "minute_bars.parquet"
        if refresh or not minute_path.exists():
            self.run_fetch()
        return self.run_features(minute_path)
