"""
市场信号因子 — 行业轮动、ETF 份额、期权 PCR、股指期货贴水
"""

from __future__ import annotations

from market_platform.predictor.核心.路径.项目 import data_dir

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

# 申万一级行业代码（2021 版，31 个）
SW_LEVEL1_CODES = [
    "801010", "801020", "801030", "801040", "801050", "801080",
    "801110", "801120", "801130", "801140", "801150", "801160",
    "801170", "801180", "801200", "801210", "801230", "801710",
    "801720", "801730", "801740", "801750", "801760", "801770",
    "801780", "801790", "801880", "801890", "801950", "801960",
    "801970",
]


def get_market_signals_config() -> dict:
    return get_config().get("features", {}).get("market_signals", {})


def is_market_signals_enabled() -> bool:
    return bool(get_market_signals_config().get("enabled", False))


def _cache_path(name: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    return RAW_DIR / name


def _load_cache(name: str) -> Optional[pd.DataFrame]:
    return load_series_df(name)


def _save_cache(df: pd.DataFrame, name: str):
    save_series_df(name, df)
    logger.info(f"💾 市场信号已缓存: {_cache_path(name)} ({len(df)} 条)")


def _meta_key(cache_name: str) -> str:
    return Path(cache_name).stem


def _load_fetched_dates(cache_name: str) -> Set[pd.Timestamp]:
    data = load_fetch_meta(_meta_key(cache_name))
    if not data:
        return set()
    try:
        return {pd.Timestamp(d).normalize() for d in data.get("fetched_dates", [])}
    except (TypeError, ValueError):
        return set()


def _save_fetched_dates(cache_name: str, dates: Set[pd.Timestamp]):
    normalized = sorted({pd.Timestamp(d).normalize() for d in dates})
    save_fetch_meta(
        _meta_key(cache_name),
        {"fetched_dates": [d.strftime("%Y-%m-%d") for d in normalized]},
    )


def _business_days(start: pd.Timestamp, end: pd.Timestamp) -> List[pd.Timestamp]:
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    if start > end:
        return []
    return [pd.Timestamp(d).normalize() for d in pd.bdate_range(start, end)]


def _fetch_sw_industry_close(code: str) -> pd.DataFrame:
    import akshare as ak

    raw = ak.index_hist_sw(symbol=code)
    raw = raw.rename(columns={"日期": "date", "收盘": "close"})
    raw["date"] = pd.to_datetime(raw["date"])
    raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
    raw["industry_code"] = code
    return raw[["date", "industry_code", "close"]].dropna(subset=["close"])


def fetch_industry_panel(
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """拉取申万一级行业收盘价面板并缓存"""
    cfg = get_market_signals_config().get("industry_rotation", {})
    cache_name = cfg.get("cache_file", "market_industry_panel.parquet")
    codes = cfg.get("sw_codes", SW_LEVEL1_CODES)
    delay = float(cfg.get("request_delay_sec", 0.2))

    cached = _load_cache(cache_name)
    if cached is not None and not cached.empty:
        cmax = cached["date"].max()
        if cmax >= end - timedelta(days=7):
            mask = (cached["date"] >= start) & (cached["date"] <= end)
            return cached.loc[mask].reset_index(drop=True)

    frames: List[pd.DataFrame] = []
    if cached is not None and not cached.empty:
        frames.append(cached)

    logger.info(f"📡 拉取申万行业指数: {len(codes)} 个")
    for i, code in enumerate(codes, start=1):
        try:
            part = _fetch_sw_industry_close(code)
            frames.append(part)
            if i % 8 == 0:
                logger.info(f"  行业进度 {i}/{len(codes)}")
        except Exception as e:
            logger.debug(f"行业 {code} 拉取失败: {e}")
        time.sleep(delay)

    if not frames:
        return pd.DataFrame(columns=["date", "industry_code", "close"])

    panel = pd.concat(frames, ignore_index=True)
    panel = panel.drop_duplicates(subset=["date", "industry_code"]).sort_values(["date", "industry_code"])
    _save_cache(panel, cache_name)
    mask = (panel["date"] >= start) & (panel["date"] <= end)
    return panel.loc[mask].reset_index(drop=True)


def compute_industry_rotation_features(panel: pd.DataFrame) -> pd.DataFrame:
    """由行业面板计算轮动因子"""
    cfg = get_market_signals_config().get("industry_rotation", {})
    window = int(cfg.get("window", 20))

    if panel is None or panel.empty:
        return pd.DataFrame(columns=["date", "ind_return_dispersion_20d", "ind_top_bottom_spread_20d"])

    wide = panel.pivot_table(index="date", columns="industry_code", values="close", aggfunc="last")
    wide = wide.sort_index().ffill()
    rets = wide.pct_change()

    cs_std = rets.std(axis=1)
    dispersion = cs_std.rolling(window, min_periods=max(5, window // 4)).mean()

    cum_ret = (1 + rets).rolling(window, min_periods=max(5, window // 4)).apply(np.prod, raw=True) - 1

    def _top_bottom_spread(row: pd.Series) -> float:
        valid = row.dropna()
        if len(valid) < 6:
            return np.nan
        ranked = valid.sort_values()
        bottom = ranked.iloc[:3].mean()
        top = ranked.iloc[-3:].mean()
        return float(top - bottom)

    spread = cum_ret.apply(_top_bottom_spread, axis=1)

    out = pd.DataFrame({
        "date": dispersion.index,
        "ind_return_dispersion_20d": dispersion.values,
        "ind_top_bottom_spread_20d": spread.values,
    })
    return out.reset_index(drop=True)


def fetch_etf_share_series(
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """沪深300 ETF 份额变化（新浪 postVol 代理）"""
    cfg = get_market_signals_config().get("etf_flow", {})
    cache_name = cfg.get("cache_file", "market_etf_510300.parquet")
    symbol = cfg.get("symbol", "sh510300")

    cached = _load_cache(cache_name)
    try:
        import akshare as ak

        raw = ak.fund_etf_hist_sina(symbol=symbol)
        raw["date"] = pd.to_datetime(raw["date"])
        raw["etf_shares"] = pd.to_numeric(raw.get("postVol", raw.get("volume")), errors="coerce")
        fresh = raw[["date", "etf_shares"]].dropna().sort_values("date")
        if cached is not None and not cached.empty:
            merged = pd.concat([cached, fresh], ignore_index=True)
        else:
            merged = fresh
        merged = merged.drop_duplicates(subset=["date"]).sort_values("date")
        _save_cache(merged, cache_name)
        cached = merged
    except Exception as e:
        logger.warning(f"⚠️ ETF 份额拉取失败: {e}")
        if cached is None:
            return pd.DataFrame(columns=["date", "etf_share_change_5d", "etf_share_change_20d"])

    if cached is None or cached.empty:
        return pd.DataFrame(columns=["date", "etf_share_change_5d", "etf_share_change_20d"])

    out = cached.copy()
    out["etf_share_change_1d"] = out["etf_shares"].pct_change()
    out["etf_share_change_5d"] = out["etf_shares"].pct_change(5)
    out["etf_share_change_20d"] = out["etf_shares"].pct_change(20)
    mask = (out["date"] >= start) & (out["date"] <= end)
    return out.loc[mask, ["date", "etf_share_change_1d", "etf_share_change_5d", "etf_share_change_20d"]].reset_index(drop=True)


def fetch_option_pcr_history(
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """沪深300ETF 期权 PCR（按交易日增量拉取 SSE 统计）"""
    cfg = get_market_signals_config().get("option_pcr", {})
    cache_name = cfg.get("cache_file", "market_hs300_pcr.parquet")
    underlying = cfg.get("underlying_code", "510300")
    delay = float(cfg.get("request_delay_sec", 0.35))
    retries = int(cfg.get("max_retries", 2))
    history_start = cfg.get("history_start")
    if history_start:
        start = max(start, pd.Timestamp(history_start).normalize())

    cached = _load_cache(cache_name)
    if cached is None:
        cached = pd.DataFrame(columns=["date", "hs300_pcr_volume", "hs300_pcr_oi"])

    fetched = _load_fetched_dates(cache_name)
    if not cached.empty:
        fetched.update(pd.to_datetime(cached["date"]).dt.normalize())

    missing = [d for d in _business_days(start, end) if d not in fetched]
    if not missing:
        mask = (cached["date"] >= start) & (cached["date"] <= end)
        return cached.loc[mask].reset_index(drop=True) if not cached.empty else cached

    logger.info(f"📡 补齐期权 PCR: {len(missing)} 个交易日")
    import akshare as ak

    new_rows: list[dict] = []
    for i, day in enumerate(missing, start=1):
        ds = day.strftime("%Y%m%d")
        for attempt in range(retries):
            try:
                stats = ak.option_daily_stats_sse(date=ds)
                row = stats[stats["合约标的代码"] == underlying]
                if row.empty:
                    item = {"date": day, "hs300_pcr_volume": np.nan, "hs300_pcr_oi": np.nan}
                else:
                    r = row.iloc[0]
                    put_vol = float(r.get("认沽成交量", 0) or 0)
                    call_vol = float(r.get("认购成交量", 0) or 0)
                    put_oi = float(r.get("未平仓认沽合约数", 0) or 0)
                    call_oi = float(r.get("未平仓认购合约数", 0) or 0)
                    item = {
                        "date": day,
                        "hs300_pcr_volume": put_vol / (call_vol + 1e-10),
                        "hs300_pcr_oi": put_oi / (call_oi + 1e-10),
                    }
                new_rows.append(item)
                fetched.add(day)
                break
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(delay * (attempt + 2))
                else:
                    logger.debug(f"PCR 拉取失败 {ds}: {e}")
        if i % 40 == 0:
            logger.info(f"  PCR 进度 {i}/{len(missing)}")
        time.sleep(delay)

    if new_rows:
        partial = pd.DataFrame(new_rows)
        cached = pd.concat([cached, partial], ignore_index=True) if not cached.empty else partial
        cached = cached.drop_duplicates(subset=["date"]).sort_values("date")
        _save_cache(cached, cache_name)
        _save_fetched_dates(cache_name, fetched)

    if cached.empty:
        return cached
    mask = (cached["date"] >= start) & (cached["date"] <= end)
    return cached.loc[mask].reset_index(drop=True)


def fetch_futures_basis(
    start: pd.Timestamp,
    end: pd.Timestamp,
    index_close: pd.Series,
) -> pd.DataFrame:
    """IF 主力贴水率 = (期货结算价 - 指数收盘) / 指数收盘"""
    cfg = get_market_signals_config().get("futures_basis", {})
    cache_name = cfg.get("cache_file", "market_if_basis.parquet")
    symbol = cfg.get("symbol", "IF0")

    cached = _load_cache(cache_name)
    try:
        import akshare as ak

        raw = ak.futures_zh_daily_sina(symbol=symbol)
        raw["date"] = pd.to_datetime(raw["date"])
        raw["if_settle"] = pd.to_numeric(raw.get("settle", raw["close"]), errors="coerce")
        fresh = raw[["date", "if_settle"]].dropna().sort_values("date")
        if cached is not None and not cached.empty:
            merged = pd.concat([cached[["date", "if_settle"]], fresh], ignore_index=True)
        else:
            merged = fresh
        merged = merged.drop_duplicates(subset=["date"]).sort_values("date")
        _save_cache(merged, cache_name)
        cached = merged
    except Exception as e:
        logger.warning(f"⚠️ 股指期货拉取失败: {e}")
        if cached is None or cached.empty:
            return pd.DataFrame(columns=["date", "if_basis_pct"])

    idx = index_close.copy()
    idx.index = pd.to_datetime(idx.index)
    out = cached.copy()
    out = out.set_index("date")
    out["index_close"] = idx.reindex(out.index)
    out["if_basis_pct"] = (out["if_settle"] - out["index_close"]) / (out["index_close"] + 1e-10)
    out = out.reset_index()[["date", "if_basis_pct", "if_settle"]]
    mask = (out["date"] >= start) & (out["date"] <= end)
    return out.loc[mask].reset_index(drop=True)


def merge_market_signals(df: pd.DataFrame) -> pd.DataFrame:
    """合并行业轮动、情绪类市场信号到行情表"""
    if not is_market_signals_enabled():
        return df

    cfg = get_market_signals_config()
    sources = cfg.get("sources", {})
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    start, end = out["date"].min(), out["date"].max()

    if sources.get("industry_rotation", True):
        try:
            panel = fetch_industry_panel(start, end)
            rot = compute_industry_rotation_features(panel)
            if not rot.empty:
                out = out.merge(rot, on="date", how="left")
                for col in ("ind_return_dispersion_20d", "ind_top_bottom_spread_20d"):
                    if col in out.columns:
                        out[col] = out[col].ffill(limit=5)
                logger.info(
                    f"🔗 行业轮动: dispersion 覆盖 {out['ind_return_dispersion_20d'].notna().sum()}/{len(out)} 日"
                )
        except Exception as e:
            logger.warning(f"⚠️ 行业轮动合并失败: {e}")

    if sources.get("etf_flow", True):
        try:
            etf = fetch_etf_share_series(start, end)
            if not etf.empty:
                out = out.merge(etf, on="date", how="left")
                for col in etf.columns:
                    if col != "date" and col in out.columns:
                        out[col] = out[col].ffill(limit=5)
                logger.info(f"🔗 ETF 份额: {out['etf_share_change_5d'].notna().sum()}/{len(out)} 日有数据")
        except Exception as e:
            logger.warning(f"⚠️ ETF 份额合并失败: {e}")

    if sources.get("option_pcr", True):
        try:
            pcr = fetch_option_pcr_history(start, end)
            if not pcr.empty:
                out = out.merge(pcr, on="date", how="left")
                for col in ("hs300_pcr_volume", "hs300_pcr_oi"):
                    if col in out.columns:
                        out[col] = out[col].ffill(limit=3)
                logger.info(f"🔗 期权 PCR: {out['hs300_pcr_volume'].notna().sum()}/{len(out)} 日有数据")
        except Exception as e:
            logger.warning(f"⚠️ 期权 PCR 合并失败: {e}")

    if sources.get("futures_basis", True):
        try:
            basis = fetch_futures_basis(start, end, out.set_index("date")["close"])
            if not basis.empty:
                out = out.merge(basis[["date", "if_basis_pct"]], on="date", how="left")
                out["if_basis_pct"] = out["if_basis_pct"].ffill(limit=3)
                logger.info(f"🔗 股指期货贴水: {out['if_basis_pct'].notna().sum()}/{len(out)} 日有数据")
        except Exception as e:
            logger.warning(f"⚠️ 期货贴水合并失败: {e}")

    return out
