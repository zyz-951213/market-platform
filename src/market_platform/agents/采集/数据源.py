from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Callable

import akshare as ak
import pandas as pd
from market_platform.agents.settings import DataConfig

logger = logging.getLogger(__name__)

FetchFn = Callable[[], pd.DataFrame]


def _to_bs_symbol(symbol: str, market: str) -> str:
    code = symbol.replace(".SH", "").replace(".SZ", "").replace("sh", "").replace("sz", "")
    mkt = "sh" if market.lower() == "sh" else "sz"
    return f"{mkt}.{code}"


def _to_em_symbol(symbol: str, market: str) -> str:
    code = symbol.replace(".SH", "").replace(".SZ", "").replace("sh", "").replace("sz", "")
    prefix = "sh" if market.lower() == "sh" else "sz"
    return f"{prefix}{code}"


def _to_sina_symbol(symbol: str, market: str) -> str:
    code = symbol.replace(".SH", "").replace(".SZ", "").replace("sh", "").replace("sz", "")
    prefix = "sh" if market.lower() == "sh" else "sz"
    return f"{prefix}{code}"


def _normalize_fund_flow(df: pd.DataFrame, symbol: str, start_date: str) -> pd.DataFrame:
    df = df.rename(
        columns={
            "日期": "date",
            "收盘价": "close",
            "涨跌幅": "pct_change",
            "主力净流入-净额": "main_net_inflow",
            "主力净流入-净占比": "main_net_ratio",
            "超大单净流入-净额": "super_large_net_inflow",
            "超大单净流入-净占比": "super_large_net_ratio",
            "大单净流入-净额": "large_net_inflow",
            "大单净流入-净占比": "large_net_ratio",
            "中单净流入-净额": "medium_net_inflow",
            "中单净流入-净占比": "medium_net_ratio",
            "小单净流入-净额": "small_net_inflow",
            "小单净流入-净占比": "small_net_ratio",
        }
    )
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["symbol"] = symbol
    start = pd.to_datetime(start_date).date()
    return df[df["date"] >= start].sort_values("date").reset_index(drop=True)


def fetch_fund_flow_eastmoney_direct(config: DataConfig) -> pd.DataFrame:
    """直连东方财富资金流 API（优先 curl_cffi，绕过 requests 连接问题）。"""
    market_map = {"sh": 1, "sz": 0, "bj": 0}
    market = config.market.lower()
    symbol = config.symbol.replace(".SH", "").replace(".SZ", "")
    secid = f"{market_map.get(market, 0)}.{symbol}"
    params = {
        "lmt": "0",
        "klt": "101",
        "secid": secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "_": str(int(time.time() * 1000)),
    }
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    payload: dict | None = None
    try:
        from curl_cffi import requests as cffi_requests

        resp = cffi_requests.get(url, params=params, impersonate="chrome", timeout=20)
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fund flow curl_cffi failed, try urllib: %s", exc)
        query = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        req = urllib.request.Request(
            f"{url}?{query}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

    klines = (payload.get("data") or {}).get("klines") or [] if payload else []
    if not klines:
        return pd.DataFrame()
    temp_df = pd.DataFrame([item.split(",") for item in klines])
    temp_df.columns = [
        "日期",
        "主力净流入-净额",
        "小单净流入-净额",
        "中单净流入-净额",
        "大单净流入-净额",
        "超大单净流入-净额",
        "主力净流入-净占比",
        "小单净流入-净占比",
        "中单净流入-净占比",
        "大单净流入-净占比",
        "超大单净流入-净占比",
        "收盘价",
        "涨跌幅",
        "-",
        "-",
    ]
    temp_df = temp_df[
        [
            "日期",
            "收盘价",
            "涨跌幅",
            "主力净流入-净额",
            "主力净流入-净占比",
            "超大单净流入-净额",
            "超大单净流入-净占比",
            "大单净流入-净额",
            "大单净流入-净占比",
            "中单净流入-净额",
            "中单净流入-净占比",
            "小单净流入-净额",
            "小单净流入-净占比",
        ]
    ]
    for col in temp_df.columns:
        if col != "日期":
            temp_df[col] = pd.to_numeric(temp_df[col], errors="coerce")
    return _normalize_fund_flow(temp_df, config.symbol, config.start_date)


def fetch_fund_flow_akshare(config: DataConfig) -> pd.DataFrame:
    market = "sh" if config.market.lower() == "sh" else "sz"
    symbol = config.symbol.replace(".SH", "").replace(".SZ", "")
    df = ak.stock_individual_fund_flow(stock=symbol, market=market)
    if df is None or df.empty:
        return pd.DataFrame()
    return _normalize_fund_flow(df, config.symbol, config.start_date)


def _normalize_fund_flow_sina(raw: pd.DataFrame, symbol: str, start_date: str) -> pd.DataFrame:
    """新浪个股资金流：r0_net=主力净流入，历史可回溯至约 2010 年。"""
    df = raw.copy()
    df["date"] = pd.to_datetime(df["opendate"], errors="coerce").dt.date
    df["close"] = pd.to_numeric(df["trade"], errors="coerce")
    df["pct_change"] = pd.to_numeric(df["changeratio"], errors="coerce") * 100
    df["main_net_inflow"] = pd.to_numeric(df["r0_net"], errors="coerce")
    df["main_net_ratio"] = pd.to_numeric(df["r0_ratio"], errors="coerce") * 100
    df["symbol"] = symbol
    start = pd.to_datetime(start_date).date()
    keep = ["date", "close", "pct_change", "main_net_inflow", "main_net_ratio", "symbol"]
    return df[keep][df["date"] >= start].sort_values("date").reset_index(drop=True)


def fetch_fund_flow_sina(config: DataConfig, page_size: int = 5000) -> pd.DataFrame:
    """新浪历史个股资金流（免费，约 2010 年起，单股 ~4000 交易日）。"""
    sina_code = _to_sina_symbol(config.symbol, config.market)
    url = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "MoneyFlow.ssl_qsfx_zjlrqs"
    )
    params = {
        "page": "1",
        "num": str(page_size),
        "sort": "opendate",
        "asc": "0",
        "daima": sina_code,
    }
    payload: list[dict] | None = None
    try:
        from curl_cffi import requests as cffi_requests

        resp = cffi_requests.get(url, params=params, impersonate="chrome", timeout=30)
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fund flow sina curl_cffi failed, try urllib: %s", exc)
        query = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        req = urllib.request.Request(
            f"{url}?{query}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))

    if not payload:
        return pd.DataFrame()
    return _normalize_fund_flow_sina(pd.DataFrame(payload), config.symbol, config.start_date)


def _merge_fund_flow_by_priority(frames: list[tuple[str, pd.DataFrame]]) -> tuple[pd.DataFrame, str]:
    """按 sources 顺序合并：先出现的源在重叠日期上优先。"""
    merged = pd.DataFrame()
    used: list[str] = []
    for source, df in frames:
        if df is None or df.empty:
            continue
        used.append(source)
        if merged.empty:
            merged = df.copy()
            continue
        existing = set(merged["date"])
        extra = df[~df["date"].isin(existing)]
        if not extra.empty:
            merged = pd.concat([merged, extra], ignore_index=True)
    if merged.empty:
        return merged, ""
    merged = merged.sort_values("date").reset_index(drop=True)
    return merged, "+".join(used)


def build_fund_flow_volume_proxy(
    daily_df: pd.DataFrame | None,
    minute_df: pd.DataFrame | None,
    symbol: str,
    start_date: str,
) -> pd.DataFrame:
    """由量价数据估算日频主力净流入（真实 API 不可用时的兜底）。

    优先用分钟线 volume_imbalance_proxy（涨跌方向 × 成交量）× VWAP；
    无分钟线时退化为 sign(日涨跌) × 成交额。
    """
    start = pd.to_datetime(start_date).date()
    rows: list[dict] = []

    if minute_df is not None and not minute_df.empty:
        from market_platform.agents.分析.特征.分钟 import compute_minute_features

        mf = compute_minute_features(minute_df.copy())
        mf["date"] = pd.to_datetime(mf["date"]).dt.date
        g = mf.groupby("date", sort=True)
        agg = g.agg(
            open=("open", "first"),
            close=("close", "last"),
            amount=("amount", "sum"),
            volume=("volume", "sum"),
            imb=("volume_imbalance_proxy", "sum"),
        ).reset_index()
        for _, row in agg.iterrows():
            if row["date"] < start:
                continue
            amount = float(row["amount"] or 0)
            volume = float(row["volume"] or 0)
            vwap = amount / volume if volume > 0 else float(row["close"] or 0)
            main_inflow = float(row["imb"] or 0) * vwap
            open_px = float(row["open"] or row["close"] or 0)
            close_px = float(row["close"] or 0)
            pct = (close_px / open_px - 1) * 100 if open_px > 0 else 0.0
            ratio = (main_inflow / amount * 100) if amount > 0 else 0.0
            rows.append(
                {
                    "date": row["date"],
                    "close": close_px,
                    "pct_change": pct,
                    "main_net_inflow": main_inflow,
                    "large_net_inflow": main_inflow * 0.5,
                    "small_net_inflow": -main_inflow * 0.3,
                    "main_net_ratio": ratio,
                    "small_net_ratio": -ratio * 0.4,
                    "symbol": symbol,
                }
            )
    elif daily_df is not None and not daily_df.empty:
        daily = daily_df.copy()
        daily["date"] = pd.to_datetime(daily["date"]).dt.date
        daily = daily.sort_values("date")
        if "amount" not in daily.columns:
            daily["amount"] = daily["volume"] * daily["close"]
        daily["return"] = daily["close"].pct_change()
        for _, row in daily.iterrows():
            if row["date"] < start:
                continue
            amount = float(row.get("amount") or 0)
            ret = float(row.get("return") or 0)
            sign = 1.0 if ret > 0 else (-1.0 if ret < 0 else 0.0)
            main_inflow = sign * amount
            ratio = sign * 100.0 if amount > 0 else 0.0
            rows.append(
                {
                    "date": row["date"],
                    "close": float(row["close"]),
                    "pct_change": ret * 100,
                    "main_net_inflow": main_inflow,
                    "large_net_inflow": main_inflow * 0.5,
                    "small_net_inflow": -main_inflow * 0.3,
                    "main_net_ratio": ratio,
                    "small_net_ratio": -ratio * 0.4,
                    "symbol": symbol,
                }
            )

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def _append_volume_proxy_gaps(
    collected: list[tuple[str, pd.DataFrame]],
    proxy: pd.DataFrame,
) -> list[tuple[str, pd.DataFrame]]:
    """仅将 proxy 中未被已有源覆盖的日期追加到 collected。"""
    if proxy.empty:
        return collected
    covered: set = set()
    for _, df in collected:
        covered.update(df["date"])
    gap = proxy[~proxy["date"].isin(covered)]
    if gap.empty:
        return collected
    return [*collected, ("volume_proxy", gap)]


def fetch_fund_flow_with_fallback(
    config: DataConfig,
    sources: list[str] | None = None,
    daily_df: pd.DataFrame | None = None,
    minute_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, str]:
    chain = sources or config.fund_flow_sources or [
        "eastmoney_direct",
        "sina",
        "eastmoney",
        "volume_proxy",
    ]
    providers: dict[str, FetchFn] = {
        "eastmoney_direct": lambda: fetch_fund_flow_eastmoney_direct(config),
        "eastmoney": lambda: fetch_fund_flow_akshare(config),
        "sina": lambda: fetch_fund_flow_sina(config),
    }
    errors: list[str] = []
    collected: list[tuple[str, pd.DataFrame]] = []
    use_volume_proxy = "volume_proxy" in chain
    api_chain = [name for name in chain if name != "volume_proxy"]

    for name in api_chain:
        fn = providers.get(name)
        if fn is None:
            continue
        try:
            df = fn()
            if df is not None and not df.empty:
                collected.append((name, df))
                logger.info("Fund flow loaded via %s (%d rows)", name, len(df))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            logger.warning("Fund flow source %s failed: %s", name, exc)

    proxy = pd.DataFrame()
    if use_volume_proxy and (daily_df is not None or minute_df is not None):
        proxy = build_fund_flow_volume_proxy(daily_df, minute_df, config.symbol, config.start_date)
        if not proxy.empty:
            logger.info("Fund flow volume proxy built (%d rows)", len(proxy))

    if not collected and proxy.empty:
        raise RuntimeError("All fund flow sources failed: " + "; ".join(errors))

    if not collected and not proxy.empty:
        return proxy, "volume_proxy"

    if proxy.empty:
        if len(collected) == 1:
            return collected[0][1], collected[0][0]
        merged, label = _merge_fund_flow_by_priority(collected)
        logger.info("Fund flow merged from %s (%d rows)", label, len(merged))
        return merged, label

    collected = _append_volume_proxy_gaps(collected, proxy)
    merged, label = _merge_fund_flow_by_priority(collected)
    logger.info("Fund flow merged from %s (%d rows)", label, len(merged))
    return merged, label


def fetch_minute_baostock(config: DataConfig, period: str = "15") -> pd.DataFrame:
    import baostock as bs

    bs_code = _to_bs_symbol(config.symbol, config.market)
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")
    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,time,open,high,low,close,volume,amount",
            start_date=pd.to_datetime(config.start_date).strftime("%Y-%m-%d"),
            end_date=pd.to_datetime(config.resolved_end_date()).strftime("%Y-%m-%d"),
            frequency=period,
            adjustflag="2",
        )
        rows: list[list[str]] = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
    finally:
        bs.logout()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["date", "time", "open", "high", "low", "close", "volume", "amount"])
    df["datetime"] = pd.to_datetime(df["time"].str.slice(0, 14), format="%Y%m%d%H%M%S", errors="coerce")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for col in ("open", "high", "low", "close", "volume", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["vwap"] = df["close"]
    df["symbol"] = config.symbol
    keep = ["datetime", "date", "open", "close", "high", "low", "volume", "amount", "vwap", "symbol"]
    return df[keep].dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)


def _filter_by_start(df: pd.DataFrame, start_date: str, date_col: str = "date") -> pd.DataFrame:
    if df.empty or date_col not in df.columns:
        return df
    start = pd.to_datetime(start_date).date()
    out = df[df[date_col] >= start].copy()
    return out.sort_values(date_col).reset_index(drop=True)


def _normalize_daily(df: pd.DataFrame, symbol: str, source: str) -> pd.DataFrame:
    if source == "eastmoney":
        df = df.rename(
            columns={
                "日期": "date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
                "振幅": "amplitude",
                "涨跌幅": "pct_change",
                "涨跌额": "change",
                "换手率": "turnover",
            }
        )
    elif source == "tencent":
        if "volume" not in df.columns:
            df["volume"] = df["amount"]
        if "amount" not in df.columns or df["amount"].isna().all():
            df["amount"] = df["volume"] * df["close"]
    elif source == "sina":
        pass  # already english columns

    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["symbol"] = symbol
    keep = [c for c in ("date", "open", "close", "high", "low", "volume", "amount", "symbol") if c in df.columns]
    return df[keep].sort_values("date").reset_index(drop=True)


def fetch_daily_eastmoney(config: DataConfig) -> pd.DataFrame:
    symbol = config.symbol.replace(".SH", "").replace(".SZ", "")
    df = ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=config.start_date,
        end_date=config.resolved_end_date(),
        adjust="qfq",
    )
    return _normalize_daily(df, config.symbol, "eastmoney")


def fetch_daily_tencent(config: DataConfig) -> pd.DataFrame:
    symbol = _to_em_symbol(config.symbol, config.market)
    df = ak.stock_zh_a_hist_tx(
        symbol=symbol,
        start_date=config.start_date,
        end_date=config.resolved_end_date(),
        adjust="qfq",
    )
    return _normalize_daily(df, config.symbol, "tencent")


def fetch_daily_sina(config: DataConfig) -> pd.DataFrame:
    symbol = _to_em_symbol(config.symbol, config.market)
    df = ak.stock_zh_a_daily(
        symbol=symbol,
        start_date=config.start_date,
        end_date=config.resolved_end_date(),
        adjust="qfq",
    )
    return _normalize_daily(df, config.symbol, "sina")


def _daily_ohlc_quality(df: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """清洗非正 OHLC / 跳价，返回 (clean_df, 综合质量分 0~1)。

    质量分综合考虑保留比例与清洗后残留极端涨跌；残留 |涨跌|>50% 直接判低质。
    """
    from market_platform.agents.存储.行情清洗 import daily_extreme_stats, sanitize_daily_ohlcv

    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.copy(), 0.0
    clean = sanitize_daily_ohlcv(df)
    if clean.empty:
        return clean, 0.0
    keep_ratio = float(len(clean)) / float(len(df)) if len(df) else 0.0
    stats = daily_extreme_stats(clean)
    # 清洗后仍有 >50% 跳价 → 不可用
    if int(stats["n50"]) > 0:
        return clean, min(keep_ratio, 0.5) * 0.5
    # 残留 >30% 跳价按比例扣分
    n30 = int(stats["n30"])
    n = max(int(stats["n"]), 1)
    penalty = min(0.4, 0.05 * n30 + 0.5 * (n30 / n))
    score = max(0.0, keep_ratio - penalty)
    return clean, score


def fetch_daily_with_fallback(
    config: DataConfig,
    sources: list[str] | None = None,
    *,
    min_valid_ratio: float = 0.85,
) -> tuple[pd.DataFrame, str]:
    """按优先级尝试多数据源拉取日线。返回 (df, source_name)。

    部分源前复权会出现负价或暴涨暴跌毛刺；清洗后质量分过低则换下一源。
    """
    chain = sources or config.daily_sources or ["eastmoney", "tencent", "sina"]
    providers: dict[str, FetchFn] = {
        "eastmoney": lambda: fetch_daily_eastmoney(config),
        "tencent": lambda: fetch_daily_tencent(config),
        "sina": lambda: fetch_daily_sina(config),
    }
    errors: list[str] = []
    best: tuple[pd.DataFrame, str, float] | None = None
    for name in chain:
        fn = providers.get(name)
        if fn is None:
            continue
        try:
            df = fn()
            if df is None or df.empty:
                errors.append(f"{name}: empty")
                logger.warning("Daily source %s returned empty", name)
                continue
            clean, score = _daily_ohlc_quality(df)
            if clean.empty:
                errors.append(f"{name}: no valid OHLC")
                logger.warning("Daily source %s has no valid OHLC after sanitize", name)
                continue
            if best is None or score > best[2] or (
                abs(score - best[2]) < 1e-9 and len(clean) > len(best[0])
            ):
                best = (clean, name, score)
            if score >= min_valid_ratio:
                logger.info(
                    "Daily bars loaded via %s (%d rows, quality=%.1f%%)",
                    name,
                    len(clean),
                    score * 100,
                )
                return clean, name
            errors.append(f"{name}: low quality {score:.1%} ({len(clean)}/{len(df)})")
            logger.warning(
                "Daily source %s low OHLC quality (%.1f%%), try next",
                name,
                score * 100,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            logger.warning("Daily source %s failed: %s", name, exc)
    if best is not None:
        clean, name, score = best
        logger.warning(
            "Using best available daily source %s (%d rows, quality=%.1f%%) after quality fallback",
            name,
            len(clean),
            score * 100,
        )
        return clean, name
    raise RuntimeError("All daily sources failed: " + "; ".join(errors))


def _normalize_minute(df: pd.DataFrame, symbol: str, source: str) -> pd.DataFrame:
    if source == "eastmoney":
        df = df.rename(
            columns={
                "时间": "datetime",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
                "均价": "vwap",
            }
        )
        df["datetime"] = pd.to_datetime(df["datetime"])
    elif source == "sina":
        df = df.rename(columns={"day": "datetime"})
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ("open", "high", "low", "close", "volume", "amount"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "vwap" not in df.columns:
            df["vwap"] = df["close"]
    elif source == "baostock":
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ("open", "high", "low", "close", "volume", "amount", "vwap"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

    df["date"] = df["datetime"].dt.date
    df["symbol"] = symbol
    keep = [
        c
        for c in ("datetime", "date", "open", "close", "high", "low", "volume", "amount", "vwap", "symbol")
        if c in df.columns
    ]
    return df[keep].sort_values("datetime").reset_index(drop=True)


def fetch_minute_eastmoney(config: DataConfig, period: str = "1") -> pd.DataFrame:
    symbol = _to_em_symbol(config.symbol, config.market)
    df = ak.stock_zh_a_hist_min_em(
        symbol=symbol,
        start_date=config.start_date,
        end_date=config.resolved_end_date(),
        period=period,
        adjust="",
    )
    return _normalize_minute(df, config.symbol, "eastmoney")


def fetch_minute_sina(config: DataConfig, period: str = "15") -> pd.DataFrame:
    symbol = _to_em_symbol(config.symbol, config.market)
    df = ak.stock_zh_a_minute(symbol=symbol, period=period, adjust="qfq")
    if df is None or df.empty:
        return pd.DataFrame()
    out = _normalize_minute(df, config.symbol, "sina")
    return _filter_by_start(out, config.start_date, date_col="date")


def fetch_minute_with_fallback(
    config: DataConfig,
    sources: list[str] | None = None,
    em_periods: tuple[str, ...] = ("1", "5", "15", "30"),
    sina_periods: tuple[str, ...] = ("15", "5", "30", "60"),
    bs_periods: tuple[str, ...] = ("15", "5", "30", "60"),
) -> tuple[pd.DataFrame, str]:
    """按优先级尝试多数据源拉取分钟线。返回 (df, source_label)。"""
    chain = sources or config.minute_sources or ["baostock", "eastmoney", "sina"]
    errors: list[str] = []

    if "baostock" in chain:
        for period in bs_periods:
            try:
                df = fetch_minute_baostock(config, period=period)
                if not df.empty:
                    label = f"baostock_{period}m"
                    logger.info("Minute bars loaded via %s (%d rows)", label, len(df))
                    return df, label
            except Exception as exc:  # noqa: BLE001
                errors.append(f"baostock_{period}m: {exc}")
                logger.warning("Minute baostock period=%s failed: %s", period, exc)

    if "eastmoney" in chain:
        for period in em_periods:
            try:
                df = fetch_minute_eastmoney(config, period=period)
                if not df.empty:
                    label = f"eastmoney_{period}m"
                    logger.info("Minute bars loaded via %s (%d rows)", label, len(df))
                    return df, label
            except Exception as exc:  # noqa: BLE001
                errors.append(f"eastmoney_{period}m: {exc}")
                logger.warning("Minute eastmoney period=%s failed: %s", period, exc)

    if "sina" in chain:
        for period in sina_periods:
            try:
                df = fetch_minute_sina(config, period=period)
                if not df.empty:
                    label = f"sina_{period}m"
                    logger.info("Minute bars loaded via %s (%d rows)", label, len(df))
                    return df, label
            except Exception as exc:  # noqa: BLE001
                errors.append(f"sina_{period}m: {exc}")
                logger.warning("Minute sina period=%s failed: %s", period, exc)

    raise RuntimeError("All minute sources failed: " + "; ".join(errors))


def expand_daily_to_minute_proxy(daily_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """分钟线不可用时的降级方案：由日线展开为 4 根代理分钟 K 线。"""
    rows: list[dict] = []
    slots = [(9, 35), (10, 30), (14, 0), (14, 55)]
    for _, d in daily_df.iterrows():
        day = pd.Timestamp(d["date"])
        for hour, minute in slots:
            ts = day.replace(hour=hour, minute=minute)
            rows.append(
                {
                    "datetime": ts,
                    "date": day.date(),
                    "open": float(d["open"]),
                    "close": float(d["close"]),
                    "high": float(d["high"]),
                    "low": float(d["low"]),
                    "volume": float(d["volume"]) / len(slots),
                    "amount": float(d.get("amount", d["volume"] * d["close"])) / len(slots),
                    "vwap": float(d["close"]),
                    "symbol": symbol,
                }
            )
    return pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)
