from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

_SHANGHAI = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger(__name__)

# 评审 D6:日历不可用/陈旧时必须可被外部看见,而不是静默按"周一~五"运行
_日历回退原因: str | None = None
_日历覆盖到: date | None = None


@lru_cache(maxsize=1)
def _load_a_share_trading_days() -> frozenset[date]:
    """从「公共.日历文件」加载 A 股开市日；失败则返回空集（走周末回退）。

    **静默退化修正(评审 D6)**:此前加载失败只打一句英文 warning,调度会悄悄按
    "周一~五" 判交易日(法定节假日会误判)。现在改为 error 级 + 记录回退原因,
    并在日历**未覆盖到近期**时同样告警(此前日历停在几个月前无人发现)。
    """
    global _日历回退原因, _日历覆盖到
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
            _日历回退原因 = "公共.日历文件 无 A 股开市记录"
            logger.error("A 股日历为空,交易日判断退化为『周一~五』(节假日会误判): %s", _日历回退原因)
            return frozenset()
        col = df.columns[0]
        days = {
            (v.date() if hasattr(v, "date") else datetime.strptime(str(v)[:10], "%Y-%m-%d").date())
            for v in df[col].tolist()
            if v is not None
        }
        _日历回退原因 = None
        _日历覆盖到 = max(days) if days else None
        if _日历覆盖到 is not None and (date.today() - _日历覆盖到).days > 30:
            logger.error(
                "A 股日历仅覆盖到 %s(距今 %d 天),近期交易日判断可能不准;请更新 公共.日历文件",
                _日历覆盖到, (date.today() - _日历覆盖到).days,
            )
        return frozenset(days)
    except Exception as exc:
        _日历回退原因 = f"{type(exc).__name__}: {exc}"
        logger.error("加载 A 股日历失败,交易日判断退化为『周一~五』: %s", exc)
        return frozenset()


def 日历状态() -> dict:
    """给自检/健康检查用:日历是否可用、覆盖到哪天、回退原因。"""
    days = _load_a_share_trading_days()
    return {
        "可用": bool(days),
        "交易日数": len(days),
        "覆盖到": _日历覆盖到,
        "回退原因": _日历回退原因,
        "回退方式": "无" if days else "周一~五(不含法定节假日)",
    }


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
