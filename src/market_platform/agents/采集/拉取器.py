from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import pandas as pd
from market_platform.agents.settings import DataConfig
from market_platform.agents.采集.数据源 import (
    expand_daily_to_minute_proxy,
    fetch_daily_with_fallback,
    fetch_fund_flow_with_fallback,
    fetch_minute_with_fallback,
)
from market_platform.agents.采集.演示 import (
    generate_demo_fund_flow,
    generate_demo_margin,
    generate_demo_minute_bars,
    generate_demo_northbound,
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BACKOFF = 2.0


def _normalize_symbol(symbol: str) -> str:
    return symbol.replace(".SH", "").replace(".SZ", "").replace("sh", "").replace("sz", "")


def _to_em_symbol(symbol: str, market: str) -> str:
    code = _normalize_symbol(symbol)
    prefix = "sh" if market.lower() == "sh" else "sz"
    return f"{prefix}{code}"


class MarketDataFetcher:
    """多数据源行情拉取（东方财富 → 腾讯/新浪降级）。"""

    def __init__(self, config: DataConfig, sleep_seconds: float = 0.5):
        self.config = config
        self.sleep_seconds = sleep_seconds
        self.last_daily_source: str = ""
        self.last_minute_source: str = ""
        self.last_fund_flow_source: str = ""

    def _throttle(self) -> None:
        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)

    def _call_with_retry(self, fn, *args, **kwargs):
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                wait = RETRY_BACKOFF ** attempt
                logger.warning("API call failed (attempt %d/%d): %s", attempt + 1, MAX_RETRIES, exc)
                time.sleep(wait)
        raise RuntimeError(f"API call failed after {MAX_RETRIES} retries: {last_exc}") from last_exc

    def fetch_minute_bars(self, period: str = "1") -> pd.DataFrame:
        """拉取分钟 K 线（多源降级）。"""
        logger.info("Fetching minute bars for %s", self.config.symbol)
        self._throttle()
        df, source = self._call_with_retry(fetch_minute_with_fallback, self.config)
        self.last_minute_source = source
        if df is None or df.empty:
            raise RuntimeError(f"No minute data returned for {self.config.symbol}")
        return df

    def fetch_daily_bars(self) -> pd.DataFrame:
        """拉取日线行情（多源降级）。"""
        logger.info("Fetching daily bars for %s", self.config.symbol)
        self._throttle()
        df, source = self._call_with_retry(fetch_daily_with_fallback, self.config)
        self.last_daily_source = source
        if df is None or df.empty:
            raise RuntimeError(f"No daily data returned for {self.config.symbol}")
        return df

    def fetch_individual_fund_flow(
        self,
        daily: pd.DataFrame | None = None,
        minute: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """个股资金流向（多源降级 + 量价 proxy 补洞）。"""
        logger.info("Fetching fund flow for %s.%s", self.config.symbol, self.config.market)
        self._throttle()
        try:
            df, source = self._call_with_retry(
                fetch_fund_flow_with_fallback,
                self.config,
                daily_df=daily,
                minute_df=minute,
            )
            self.last_fund_flow_source = source
            return df
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fund flow unavailable: %s", exc)
            self.last_fund_flow_source = "none"
            return pd.DataFrame()

    def fetch_northbound_flow(self) -> pd.DataFrame:
        """北向资金净流入（市场级，日频）。"""
        import akshare as ak

        logger.info("Fetching northbound flow")
        self._throttle()

        try:
            df = ak.stock_hsgt_hist_em(symbol="北向资金")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Northbound EM failed: %s", exc)
            return pd.DataFrame()

        if df is None or df.empty:
            logger.warning("Northbound flow empty")
            return pd.DataFrame()

        rename = {
            "日期": "date",
            "date": "date",
            "当日成交净买额": "north_net_inflow",
            "NET_DEAL_AMT": "north_net_inflow",
            "value": "north_net_inflow",
        }
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
        if "north_net_inflow" not in df.columns:
            return pd.DataFrame()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        start = pd.to_datetime(self.config.start_date).date()
        df = df[df["date"] >= start]
        return df.sort_values("date").reset_index(drop=True)

    def fetch_margin_summary(self) -> pd.DataFrame:
        """两融余额（市场级）。"""
        import akshare as ak

        logger.info("Fetching margin summary")
        self._throttle()

        try:
            df = ak.stock_margin_sse()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Margin fetch failed: %s", exc)
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        df = df.rename(columns={"信用交易日期": "date", "融资余额": "margin_balance"})
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df["margin_balance_change"] = df["margin_balance"].diff()
        start = pd.to_datetime(self.config.start_date).date()
        df = df[df["date"] >= start]
        return df[["date", "margin_balance", "margin_balance_change"]].sort_values("date")

    def fetch_minute_by_date_range(
        self,
        start: str | None = None,
        end: str | None = None,
        period: str = "1",
    ) -> pd.DataFrame:
        """按日期范围拉分钟线；优先配置的数据源顺序。"""
        preferred = (self.config.minute_sources or ["baostock", "sina"])[0]
        cfg = DataConfig(
            root=self.config.root,
            symbol=self.config.symbol,
            market=self.config.market,
            start_date=start or self.config.start_date,
            end_date=end or self.config.resolved_end_date(),
            daily_sources=self.config.daily_sources,
            minute_sources=self.config.minute_sources,
        )

        if preferred in {"sina", "baostock"}:
            try:
                df, source = fetch_minute_with_fallback(cfg)
                self.last_minute_source = source
                return df
            except Exception as exc:  # noqa: BLE001
                logger.warning("Preferred %s minute failed: %s", preferred, exc)

        start_dt = pd.to_datetime(start or self.config.start_date)
        end_dt = pd.to_datetime(end or datetime.now().strftime("%Y%m%d"))
        chunks: list[pd.DataFrame] = []

        cursor = start_dt
        while cursor <= end_dt:
            chunk_end = min(cursor + timedelta(days=20), end_dt)
            chunk_cfg = DataConfig(
                root=self.config.root,
                symbol=self.config.symbol,
                market=self.config.market,
                start_date=cursor.strftime("%Y%m%d"),
                end_date=chunk_end.strftime("%Y%m%d"),
                daily_sources=self.config.daily_sources,
                minute_sources=["eastmoney"],
            )
            try:
                from market_platform.agents.采集.数据源 import fetch_minute_eastmoney

                part = fetch_minute_eastmoney(chunk_cfg, period=period)
                if not part.empty:
                    chunks.append(part)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Minute chunk %s-%s failed: %s", cursor.date(), chunk_end.date(), exc)
                break
            cursor = chunk_end + timedelta(days=1)

        if chunks:
            df = pd.concat(chunks, ignore_index=True)
            df = df.drop_duplicates(subset=["datetime"]).sort_values("datetime")
            self.last_minute_source = f"eastmoney_{period}m_chunked"
            return df.reset_index(drop=True)

        logger.info("Eastmoney minute unavailable, falling back to alternate sources")
        try:
            df, source = fetch_minute_with_fallback(
                cfg,
                sources=[s for s in self.config.minute_sources if s != "daily_proxy"],
            )
            self.last_minute_source = source
            return df
        except Exception as exc:  # noqa: BLE001
            logger.warning("Minute multi-source fallback failed: %s", exc)

        if "daily_proxy" in self.config.minute_sources or self.config.fallback_daily_proxy:
            daily = self.fetch_daily_bars()
            df = expand_daily_to_minute_proxy(daily, self.config.symbol)
            self.last_minute_source = "daily_proxy"
            logger.info("Using daily-expanded minute proxy (%d bars)", len(df))
            return df

        raise RuntimeError("Failed to fetch minute data from all sources")

    def fetch_demo_dataset(self, n_days: int = 60) -> dict[str, pd.DataFrame]:
        """离线 demo 数据（网络不可用时使用）。"""
        logger.info("Generating demo dataset for %s (%d trading days)", self.config.symbol, n_days)
        minute = generate_demo_minute_bars(
            symbol=self.config.symbol,
            start_date=self.config.start_date,
            n_days=n_days,
        )
        fund_flow = generate_demo_fund_flow(minute)
        dates = minute["date"].drop_duplicates().sort_values()
        north = generate_demo_northbound(dates)
        margin = generate_demo_margin(dates)
        daily = (
            minute.groupby("date")
            .agg(
                open=("open", "first"),
                close=("close", "last"),
                high=("high", "max"),
                low=("low", "min"),
                volume=("volume", "sum"),
                amount=("amount", "sum"),
            )
            .reset_index()
        )
        daily["symbol"] = self.config.symbol
        return {
            "minute": minute,
            "daily": daily,
            "fund_flow": fund_flow,
            "northbound": north,
            "margin": margin,
        }
