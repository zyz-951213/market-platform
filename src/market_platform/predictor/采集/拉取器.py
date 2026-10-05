"""
数据获取模块 — 从 akshare 拉取日度行情数据
"""

from market_platform.predictor.核心.路径.项目 import data_dir

import pandas as pd
from loguru import logger
from typing import Optional

from market_platform.predictor.配置 import get_config, resolve_end_date
from market_platform.predictor.存储.数据访问 import load_series_df, save_series_df


def fetch_index_daily(
    index_code: str = "000300.SH",
    start_date: str | None = None,
    end_date: str | None = None,
    source: str = "akshare",
    save: bool = True,
) -> pd.DataFrame:
    """
    获取指数日度行情数据

    默认:沪深300,自 2010-01-01 起,到今天为止。
    **修复(评审 C11)**:此前的默认 `end_date="2025-12-31"` 会把数据静默截断到过去,
    直接调用本函数的外部代码拿不到近期行情。现改为 None → 取今天。
    """
    from datetime import date as _date

    start_date = start_date or "2010-01-01"
    end_date = end_date or _date.today().isoformat()
    if source == "akshare":
        return _fetch_from_akshare(index_code, start_date, end_date, save)
    elif source == "tushare":
        return _fetch_from_tushare(index_code, start_date, end_date, save)
    elif source == "yfinance":
        return _fetch_from_yfinance(index_code, start_date, end_date, save)
    else:
        raise ValueError(f"未知数据源: {source}")


def _fetch_from_akshare(
    index_code: str, start_date: str, end_date: str, save: bool
) -> pd.DataFrame:
    """用 akshare 获取"""
    import akshare as ak

    logger.info(f"🚀 从 akshare 获取 {index_code} {start_date} → {end_date}")

    # akshare 的指数日频接口
    df = ak.stock_zh_index_daily(symbol=f"sh{index_code.replace('.SH','').replace('.SZ','')}")

    # 标准化列名
    df.rename(
        columns={
            "date": "date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        },
        inplace=True,
    )
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # 过滤日期范围
    df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]

    if save:
        save_path = data_dir() / "raw" / f"{index_code}.parquet"
        save_series_df(f"{index_code}.parquet", df)
        logger.info(f"💾 已保存到 {save_path}，共 {len(df)} 条记录")

    return df


def _fetch_from_tushare(index_code: str, start_date: str, end_date: str, save: bool):
    """用 tushare 获取（需要 token）"""
    import tushare as ts

    pro = ts.pro_api()
    df = pro.index_daily(
        ts_code=index_code,
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
        fields="trade_date,open,high,low,close,vol,amount",
    )
    df.rename(
        columns={
            "trade_date": "date",
            "vol": "volume",
            "amount": "amount",
        },
        inplace=True,
    )
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    if save:
        save_series_df(f"{index_code}.parquet", df)
    return df


def _fetch_from_yfinance(index_code: str, start_date: str, end_date: str, save: bool):
    """用 yfinance 获取（适合境外指数）"""
    import yfinance as yf

    # 映射到 yfinance 代码
    mapping = {
        "000300.SH": "000300.SS",
        "000001.SH": "000001.SS",
        "399001.SZ": "399001.SZ",
        "^GSPC": "^GSPC",
    }
    ticker = mapping.get(index_code, index_code)
    df = yf.download(ticker, start=start_date, end=end_date, auto_adjust=True)
    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]

    if save:
        save_series_df(f"{index_code}.parquet", df)
    return df


def is_data_stale(df: pd.DataFrame, end_date: str, max_lag_days: int = 0) -> bool:
    """本地行情是否落后于目标截止日，需要重新拉取。

    只要 latest < target 且落后天数 > max_lag_days 即视为过期。
    默认 max_lag_days=0：任一落后交易日都会触发刷新。
    切勿把默认设成 3——周五数据到周一自然日差恰为 3，会漏掉整周增量。
    """
    if df is None or df.empty:
        return True
    latest = pd.Timestamp(df["date"].max()).normalize()
    target = pd.Timestamp(end_date).normalize()
    lag = (target - latest).days
    if lag <= 0:
        return False
    return lag > max_lag_days


def load_or_fetch_index(
    index_code: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    source: str | None = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """加载本地行情；过期则自动从数据源增量刷新"""
    cfg = get_config().get("data", {})
    index_code = index_code or cfg["index_code"]
    start_date = start_date or cfg.get("start_date", "2005-01-01")
    end_date = resolve_end_date(end_date or cfg.get("end_date", "auto"))
    source = source or cfg.get("source", "akshare")
    max_lag = int(cfg.get("max_stale_days", 0))

    local = load_local_data(index_code)
    if force_refresh or is_data_stale(local, end_date, max_lag):
        if local is not None and not local.empty:
            logger.info(
                f"🔄 本地数据截至 {pd.Timestamp(local['date'].max()).date()}，"
                f"目标 {end_date}，重新拉取..."
            )
        return fetch_index_daily(
            index_code=index_code,
            start_date=start_date,
            end_date=end_date,
            source=source,
            save=True,
        )

    out = local.copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out[(out["date"] >= start_date) & (out["date"] <= end_date)]
    logger.info(f"📂 使用本地缓存（截至 {out['date'].max().date()}）")
    return out.sort_values("date").reset_index(drop=True)


def load_local_data(index_code: str = "000300.SH") -> Optional[pd.DataFrame]:
    """从本地加载已存储的数据"""
    df = load_series_df(f"{index_code}.parquet")
    if df is not None:
        logger.info(f"📂 加载本地数据: {index_code}.parquet ({len(df)} 条)")
        return df
    logger.warning(f"⚠️ 本地数据不存在: {index_code}.parquet")
    return None
