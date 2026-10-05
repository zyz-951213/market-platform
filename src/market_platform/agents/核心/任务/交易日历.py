from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

_SHANGHAI = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_a_share_trading_days() -> frozenset[date]:
    """从「公共.日历文件」加载 A 股开市日；失败则返回空集（走周末回退）。"""
    try:
        from market_platform.agents.存储.数据访问 import _store

        store = _store()
        sql = """
            SELECT "日历日期"
            FROM "公共"."日历文件"
            WHERE "市场类型" = 'A股'
              AND ("开市情况" = '1' OR "开市情况" = '开市')
              AND "日历日期" IS NOT NULL
        """
        df = store._read_sql(sql)
        if df is None or df.empty:
            return frozenset()
        col = df.columns[0]
        days = {
            (v.date() if hasattr(v, "date") else datetime.strptime(str(v)[:10], "%Y-%m-%d").date())
            for v in df[col].tolist()
            if v is not None
        }
        return frozenset(days)
    except Exception as exc:
        logger.warning("Load A-share calendar failed, fallback to weekdays: %s", exc)
        return frozenset()


def is_trading_day(d: date) -> bool:
    days = _load_a_share_trading_days()
    if days:
        return d in days
    return d.weekday() < 5


def next_trading_day(d: date) -> date:
    nd = d + timedelta(days=1)
    for _ in range(40):
        if is_trading_day(nd):
            return nd
        nd += timedelta(days=1)
    # 极端回退：只跳周末
    while nd.weekday() >= 5:
        nd += timedelta(days=1)
    return nd


def prev_trading_day(d: date) -> date:
    pd_ = d - timedelta(days=1)
    for _ in range(40):
        if is_trading_day(pd_):
            return pd_
        pd_ -= timedelta(days=1)
    while pd_.weekday() >= 5:
        pd_ -= timedelta(days=1)
    return pd_


def iter_trading_days(start: date, end: date):
    """含首尾的 A 股交易日序列（含法定节假日剔除，若日历可用）。"""
    days = _load_a_share_trading_days()
    if days:
        for d in sorted(x for x in days if start <= x <= end):
            yield d
        return
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def latest_expected_trading_day(*, now: datetime | None = None) -> date:
    """估算当前应已收盘的最新 A 股交易日（18:00 前用上一交易日）。"""
    now = now or datetime.now(_SHANGHAI)
    d = now.date()
    if now.hour < 18:
        d = prev_trading_day(d)
    elif not is_trading_day(d):
        d = prev_trading_day(d)
    return d
