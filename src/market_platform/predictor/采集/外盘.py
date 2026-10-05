"""
外部数据源 — 外盘指数、宏观新闻等（供 LLM 综合研判 Prompt 使用）
"""

from __future__ import annotations

from market_platform.predictor.核心.路径.项目 import data_dir

import json
import time
from datetime import timedelta
from pathlib import Path
from typing import List, Optional, Set

import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.配置 import get_config
from market_platform.predictor.存储.数据访问 import load_fetch_meta, load_series_df, save_fetch_meta, save_series_df

RAW_DIR = data_dir() / "raw"


def get_external_config() -> dict:
    return get_config().get("features", {}).get("external", {})


def is_external_enabled() -> bool:
    return bool(get_external_config().get("enabled", False))


def _cache_path(name: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    return RAW_DIR / name


def _load_cache(name: str) -> Optional[pd.DataFrame]:
    return load_series_df(name)


def _save_cache(df: pd.DataFrame, name: str):
    save_series_df(name, df)
    logger.info(f"💾 外部数据已缓存: {_cache_path(name)} ({len(df)} 条)")


def fetch_us_index_sina() -> pd.DataFrame:
    """新浪美股指数（标普500代理）"""
    import akshare as ak

    df = ak.index_us_stock_sina()
    df = df.rename(columns={"date": "date"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["spx_close"] = pd.to_numeric(df["close"], errors="coerce")
    df["spx_ret_1d"] = df["spx_close"].pct_change()
    return df[["date", "spx_close", "spx_ret_1d"]]


def fetch_hk_index_sina(symbol: str = "HSI") -> pd.DataFrame:
    """新浪港股指数（恒生指数）"""
    import akshare as ak

    df = ak.stock_hk_index_daily_sina(symbol=symbol)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["hsi_close"] = pd.to_numeric(df["close"], errors="coerce")
    df["hsi_ret_1d"] = df["hsi_close"].pct_change()
    return df[["date", "hsi_close", "hsi_ret_1d"]]


def _fetch_yfinance_overseas(symbols: dict, start: str, end: str) -> Optional[pd.DataFrame]:
    """yfinance 备用（可选）"""
    try:
        import yfinance as yf
    except ImportError:
        return None

    frames = []
    for col_prefix, ticker in symbols.items():
        try:
            raw = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
            if raw is None or raw.empty:
                continue
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            part = raw.reset_index()
            part.columns = [str(c).lower() for c in part.columns]
            date_col = "date" if "date" in part.columns else part.columns[0]
            part = part.rename(columns={date_col: "date"})
            part["date"] = pd.to_datetime(part["date"])
            part[f"{col_prefix}_close"] = pd.to_numeric(part["close"], errors="coerce")
            part[f"{col_prefix}_ret_1d"] = part[f"{col_prefix}_close"].pct_change()
            frames.append(part[["date", f"{col_prefix}_close", f"{col_prefix}_ret_1d"]])
        except Exception as e:
            logger.warning(f"⚠️ yfinance {ticker} 失败: {e}")
    if not frames:
        return None
    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on="date", how="outer")
    return out.sort_values("date").reset_index(drop=True)


def fetch_overseas_indices(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    cfg = get_external_config().get("overseas", {})
    cache_name = cfg.get("cache_file", "external_overseas.parquet")
    cached = _load_cache(cache_name)
    if cached is not None and not cached.empty:
        cmax = cached["date"].max()
        if cmax >= end - timedelta(days=5):
            return cached

    us = None
    hk = None
    try:
        us = fetch_us_index_sina()
        logger.info(f"📡 外盘(美指): {len(us)} 条")
    except Exception as e:
        logger.warning(f"⚠️ 新浪美指获取失败: {e}")

    try:
        hk = fetch_hk_index_sina(cfg.get("hk_symbol", "HSI"))
        logger.info(f"📡 外盘(恒指): {len(hk)} 条")
    except Exception as e:
        logger.warning(f"⚠️ 新浪恒指获取失败: {e}")

    if us is None and hk is None:
        yf_df = _fetch_yfinance_overseas(
            {"spx": "^GSPC", "ndx": "^IXIC", "hsi": "^HSI"},
            start.strftime("%Y-%m-%d"),
            (end + timedelta(days=1)).strftime("%Y-%m-%d"),
        )
        if yf_df is not None:
            _save_cache(yf_df, cache_name)
            return yf_df
        return pd.DataFrame(columns=["date"])

    out = us if us is not None else hk
    if us is not None and hk is not None:
        out = us.merge(hk, on="date", how="outer")
    out = out.sort_values("date").reset_index(drop=True)
    _save_cache(out, cache_name)
    return out


def fetch_economic_news_for_date(date_str: str) -> pd.DataFrame:
    """百度宏观财经日历（单日）"""
    import akshare as ak

    df = ak.news_economic_baidu(date=date_str)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["date"] = pd.to_datetime(date_str, format="%Y%m%d")
    return df


def _macro_news_meta_key(cache_name: str) -> str:
    return Path(cache_name).stem


def _load_macro_news_meta(cache_name: str) -> Set[pd.Timestamp]:
    data = load_fetch_meta(_macro_news_meta_key(cache_name))
    if not data:
        return set()
    try:
        return {
            pd.Timestamp(d).normalize()
            for d in data.get("fetched_dates", [])
        }
    except (TypeError, ValueError):
        return set()


def _save_macro_news_meta(cache_name: str, fetched_dates: Set[pd.Timestamp]):
    key = _macro_news_meta_key(cache_name)
    dates = sorted({pd.Timestamp(d).normalize() for d in fetched_dates})
    save_fetch_meta(
        key,
        {"fetched_dates": [d.strftime("%Y-%m-%d") for d in dates]},
    )


def _business_days(start: pd.Timestamp, end: pd.Timestamp) -> List[pd.Timestamp]:
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    if start > end:
        return []
    return [pd.Timestamp(d).normalize() for d in pd.bdate_range(start, end)]


def fetch_macro_news_history(
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """按行情区间增量拉取宏观新闻（对齐回测历史）"""
    cfg = get_external_config().get("macro_news", {})
    cache_name = cfg.get("cache_file", "external_macro_news.parquet")
    cached = _load_cache(cache_name)
    if cached is None:
        cached = pd.DataFrame()

    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    history_start = cfg.get("history_start")
    if history_start:
        start = max(start, pd.Timestamp(history_start).normalize())
    delay = float(cfg.get("request_delay_sec", 0.35))
    retries = int(cfg.get("max_retries", 3))
    retry_sleep = float(cfg.get("retry_sleep_sec", 2.0))
    save_every = int(cfg.get("save_every_days", 40))

    fetched_dates = _load_macro_news_meta(cache_name)
    if not cached.empty:
        fetched_dates.update(pd.to_datetime(cached["date"]).dt.normalize())

    all_days = _business_days(start, end)
    missing = [d for d in all_days if d not in fetched_dates]
    if not missing:
        logger.info(f"📰 宏观新闻缓存已覆盖 {start.date()} → {end.date()}（{len(all_days)} 个工作日）")
        return cached[(cached["date"] >= start) & (cached["date"] <= end)] if not cached.empty else cached

    logger.info(
        f"📰 开始补齐宏观新闻: 待拉取 {len(missing)}/{len(all_days)} 个工作日 "
        f"（{start.date()} → {end.date()}）"
    )

    rows: list[dict] = []
    existing_events: set[tuple] = set()
    if not cached.empty:
        for _, r in cached.iterrows():
            existing_events.add(
                (pd.Timestamp(r["date"]).normalize(), str(r.get("event", "")))
            )

    new_rows: list[dict] = []
    for i, cur in enumerate(missing, start=1):
        ds = cur.strftime("%Y%m%d")
        day_rows = 0
        for attempt in range(retries):
            try:
                df = fetch_economic_news_for_date(ds)
                for _, r in df.iterrows():
                    key = (cur, str(r.get("事件", "")))
                    if key in existing_events:
                        continue
                    existing_events.add(key)
                    item = {
                        "date": cur,
                        "event": str(r.get("事件", "")),
                        "region": str(r.get("地区", "")),
                        "importance": str(r.get("重要性", "")),
                        "actual": str(r.get("公布", "")),
                        "forecast": str(r.get("预期", "")),
                    }
                    rows.append(item)
                    new_rows.append(item)
                    day_rows += 1
                break
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(retry_sleep * (attempt + 1))
                else:
                    logger.debug(f"宏观新闻拉取失败 {ds}: {e}")

        fetched_dates.add(cur)
        if i % 20 == 0 or i == len(missing):
            logger.info(f"📰 新闻进度 {i}/{len(missing)}，本日 {day_rows} 条")
        if i % save_every == 0 and new_rows:
            partial = pd.DataFrame(new_rows)
            cached = pd.concat([cached, partial], ignore_index=True) if not cached.empty else partial
            cached = cached.drop_duplicates(subset=["date", "event"]).sort_values("date")
            _save_cache(cached, cache_name)
            _save_macro_news_meta(cache_name, fetched_dates)
            new_rows = []
        time.sleep(delay)

    if new_rows:
        partial = pd.DataFrame(new_rows)
        cached = pd.concat([cached, partial], ignore_index=True) if not cached.empty else partial

    if rows or missing:
        if not cached.empty:
            cached = cached.drop_duplicates(subset=["date", "event"]).sort_values("date")
            _save_cache(cached, cache_name)
        _save_macro_news_meta(cache_name, fetched_dates)
        logger.info(
            f"📰 宏观新闻补齐完成: 缓存 {len(cached)} 条，"
            f"覆盖 {len(fetched_dates)} 个工作日"
        )

    if cached.empty:
        return cached
    return cached[(cached["date"] >= start) & (cached["date"] <= end)].reset_index(drop=True)


def fetch_macro_news_recent(
    lookback_days: int = 30,
    end: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """拉取宏观新闻并缓存（默认对齐行情末日）"""
    cfg = get_external_config().get("macro_news", {})
    if cfg.get("history", False):
        end = pd.Timestamp(end or pd.Timestamp.now()).normalize()
        start = end - timedelta(days=lookback_days)
        return fetch_macro_news_history(start, end)

    cache_name = cfg.get("cache_file", "external_macro_news.parquet")
    cached = _load_cache(cache_name)

    end = pd.Timestamp(end or pd.Timestamp.now()).normalize()
    start = end - timedelta(days=lookback_days)

    if cached is not None and not cached.empty:
        recent = cached[(cached["date"] >= start) & (cached["date"] <= end)]
        fetched = _load_macro_news_meta(cache_name)
        needed = _business_days(start, end)
        if len(recent) >= lookback_days * 0.4 or len(fetched) >= len(needed) * 0.9:
            return cached

    return fetch_macro_news_history(start, end)


def _aggregate_news_to_daily(df: pd.DataFrame, news: pd.DataFrame, max_items: int = 3) -> pd.DataFrame:
    if news is None or news.empty:
        df["macro_news_summary"] = None
        df["macro_news_count"] = 0
        return df

    news = news.copy()
    news["date"] = pd.to_datetime(news["date"]).dt.normalize()
    summaries = {}
    counts = {}
    for day, grp in news.groupby("date"):
        items = []
        for _, r in grp.head(max_items).iterrows():
            imp = r.get("importance", "")
            tag = "[重要]" if str(imp) in {"高", "3", "★★★"} else ""
            items.append(f"{tag}{r.get('region','')}{r.get('event','')}".strip())
        summaries[day] = "；".join(items) if items else None
        counts[day] = len(grp)

    out = df.copy()
    out["_day"] = pd.to_datetime(out["date"]).dt.normalize()
    out["macro_news_summary"] = out["_day"].map(summaries)
    out["macro_news_count"] = out["_day"].map(counts).fillna(0).astype(int)
    out = out.drop(columns=["_day"])
    ffill_limit = int(get_external_config().get("macro_news", {}).get("ffill_limit", 5))
    # 仅向前填充短缺口（周末），避免把最新新闻铺满全历史
    out["macro_news_summary"] = out["macro_news_summary"].ffill(limit=ffill_limit)
    out["macro_news_count"] = out["macro_news_count"].ffill(limit=ffill_limit).fillna(0).astype(int)
    return out


def merge_external_data(df: pd.DataFrame) -> pd.DataFrame:
    """合并外盘与宏观新闻到日频行情表"""
    if not is_external_enabled():
        return df

    cfg = get_external_config()
    sources = cfg.get("sources", {})
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    start, end = out["date"].min(), out["date"].max()

    merge_cols = []

    if sources.get("overseas", True):
        try:
            overseas = fetch_overseas_indices(start, end)
            if not overseas.empty:
                cols = [c for c in overseas.columns if c != "date"]
                out = out.merge(overseas, on="date", how="left")
                merge_cols.extend(cols)
                for c in cols:
                    if c.endswith("_ret_1d"):
                        out[c] = out[c].ffill()
                    elif c.endswith("_close"):
                        out[c] = out[c].ffill()
                logger.info(
                    f"🔗 外盘指数: {out[cols[0]].notna().sum()}/{len(out)} 日有数据"
                )
        except Exception as e:
            logger.warning(f"⚠️ 外盘数据合并失败: {e}")

    if sources.get("macro_news", True):
        try:
            news_cfg = cfg.get("macro_news", {})
            max_items = int(news_cfg.get("max_headlines_per_day", 3))
            if news_cfg.get("history", False):
                news = fetch_macro_news_history(start, end)
            else:
                lookback = int(news_cfg.get("lookback_days", 30))
                news = fetch_macro_news_recent(lookback_days=lookback, end=end)
            out = _aggregate_news_to_daily(out, news, max_items=max_items)
            merge_cols.extend(["macro_news_summary", "macro_news_count"])
            logger.info(
                f"🔗 宏观新闻: {out['macro_news_summary'].notna().sum()}/{len(out)} 日有摘要"
            )
        except Exception as e:
            logger.warning(f"⚠️ 宏观新闻合并失败: {e}")

    return out


def load_macro_news_for_date(date: pd.Timestamp) -> List[dict]:
    """读取指定交易日全部宏观新闻（供信息窗口完整展示）"""
    cfg = get_external_config().get("macro_news", {})
    cache_name = cfg.get("cache_file", "external_macro_news.parquet")
    cached = _load_cache(cache_name)
    if cached is None or cached.empty:
        return []

    day = pd.Timestamp(date).normalize()
    sub = cached[pd.to_datetime(cached["date"]).dt.normalize() == day].copy()
    if sub.empty:
        return []

    items: List[dict] = []
    for _, r in sub.iterrows():
        actual = r.get("actual")
        forecast = r.get("forecast")
        items.append({
            "event": str(r.get("event", "")).strip(),
            "region": str(r.get("region", "")).strip(),
            "importance": str(r.get("importance", "")).strip(),
            "actual": "" if pd.isna(actual) else str(actual).strip(),
            "forecast": "" if pd.isna(forecast) else str(forecast).strip(),
        })
    return items


def weekly_news_summary(df: pd.DataFrame, week_end: pd.Timestamp, max_items: int = 5) -> Optional[str]:
    """聚合单周新闻摘要（周频 Prompt 用）；粗去重，细清洗在日信号复合中完成。"""
    if "macro_news_summary" not in df.columns:
        return None
    week_end = pd.Timestamp(week_end)
    week_start = week_end - timedelta(days=6)
    mask = (pd.to_datetime(df["date"]) >= week_start) & (pd.to_datetime(df["date"]) <= week_end)
    chunk = df.loc[mask, "macro_news_summary"].dropna()
    if chunk.empty:
        return None
    items = []
    for text in chunk.unique():
        for part in str(text).split("；"):
            part = part.strip()
            if part and part not in items:
                items.append(part)
    # 多取一些留给下游清洗过滤
    return "；".join(items[: max(max_items * 2, 12)]) if items else None
