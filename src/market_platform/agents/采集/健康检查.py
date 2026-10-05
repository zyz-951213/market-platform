from __future__ import annotations

import logging
from dataclasses import dataclass, field

from market_platform.agents.settings import AppConfig
from market_platform.agents.采集.拉取器 import MarketDataFetcher
from market_platform.agents.采集.新闻 import NewsFetcher
from market_platform.agents.采集.数据源 import (
    expand_daily_to_minute_proxy,
    fetch_daily_eastmoney,
    fetch_daily_sina,
    fetch_daily_tencent,
    fetch_fund_flow_eastmoney_direct,
    fetch_minute_baostock,
    fetch_minute_eastmoney,
    fetch_minute_sina,
)

logger = logging.getLogger(__name__)


@dataclass
class FetchCheckResult:
    symbol: str
    daily_ok: bool
    minute_ok: bool
    fund_flow_ok: bool
    news_ok: bool
    minute_rows: int
    daily_rows: int
    minute_period: str
    daily_source: str = ""
    minute_source: str = ""
    fund_flow_source: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return self.daily_ok and self.minute_ok and self.fund_flow_ok


def check_data_sources(config: AppConfig) -> FetchCheckResult:
    """探测各数据源可用性（含多源降级）。"""
    fetcher = MarketDataFetcher(config.data, sleep_seconds=1.0)
    news_fetcher = NewsFetcher(config.data, sleep_seconds=1.0)
    errors: list[str] = []
    daily_ok = minute_ok = fund_flow_ok = news_ok = False
    minute_rows = daily_rows = 0
    minute_period = ""
    daily_source = ""
    fund_flow_source = ""

    for name, fn in (
        ("eastmoney", lambda: fetch_daily_eastmoney(config.data)),
        ("tencent", lambda: fetch_daily_tencent(config.data)),
        ("sina", lambda: fetch_daily_sina(config.data)),
    ):
        try:
            daily = fn()
            daily_rows = len(daily)
            if daily_rows > 0:
                daily_ok = True
                daily_source = name
                break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"daily_{name}: {exc}")

    minute_probes = [
        ("baostock_15", lambda: fetch_minute_baostock(config.data, "15")),
        ("sina_15", lambda: fetch_minute_sina(config.data, "15")),
        ("eastmoney_5", lambda: fetch_minute_eastmoney(config.data, "5")),
    ]
    for label, fn in minute_probes:
        try:
            minute = fn()
            if not minute.empty:
                minute_ok = True
                minute_rows = len(minute)
                minute_source = label
                minute_period = label.split("_")[-1].replace("m", "")
                break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"minute_{label}: {exc}")

    for label, fn in (
        ("eastmoney_direct", lambda: fetch_fund_flow_eastmoney_direct(config.data)),
    ):
        try:
            ff = fn()
            if not ff.empty:
                fund_flow_ok = True
                fund_flow_source = label
                break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"fund_flow_{label}: {exc}")

    if not fund_flow_ok:
        try:
            ff = fetcher.fetch_individual_fund_flow()
            fund_flow_ok = not ff.empty
            if fund_flow_ok:
                fund_flow_source = fetcher.last_fund_flow_source
        except Exception as exc:  # noqa: BLE001
            errors.append(f"fund_flow: {exc}")

    try:
        news = news_fetcher.fetch_stock_news()
        news_ok = not news.empty
    except Exception as exc:  # noqa: BLE001
        errors.append(f"news: {exc}")

    return FetchCheckResult(
        symbol=config.data.symbol,
        daily_ok=daily_ok,
        minute_ok=minute_ok,
        fund_flow_ok=fund_flow_ok,
        news_ok=news_ok,
        minute_rows=minute_rows,
        daily_rows=daily_rows,
        minute_period=minute_period,
        daily_source=daily_source,
        minute_source=minute_source,
        fund_flow_source=fund_flow_source,
        errors=errors,
    )


__all__ = ["FetchCheckResult", "check_data_sources", "expand_daily_to_minute_proxy"]
