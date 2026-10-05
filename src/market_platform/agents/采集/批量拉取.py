from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from market_platform.agents.settings import AppConfig
from market_platform.agents.分析.特征.分钟 import compute_minute_features, summarize_minute_to_daily
from market_platform.agents.存储 import data_access as store
from market_platform.agents.采集.拉取器 import MarketDataFetcher
from market_platform.agents.采集.数据源 import expand_daily_to_minute_proxy
from market_platform.agents.采集.管道 import DataPipeline
from market_platform.agents.采集.股票池 import (
    infer_market,
    load_a_share_universe,
    load_universe_csv,
    save_universe,
)

logger = logging.getLogger(__name__)


@dataclass
class FetchRow:
    symbol: str
    market: str
    name: str
    status: str
    daily_rows: int = 0
    fund_flow_rows: int = 0
    minute_source: str = ""
    daily_source: str = ""
    fund_flow_source: str = ""
    error: str = ""


def _config_for_symbol(base: AppConfig, symbol: str, market: str) -> AppConfig:
    return replace(
        base,
        data=replace(
            base.data,
            symbol=symbol,
            market=market,
            start_date=base.data.resolved_start_date(),
        ),
    )


def _symbol_complete(symbol_dir: Path, *, require_features: bool, symbol: str = "") -> bool:
    sym = str(symbol or getattr(symbol_dir, "name", "") or "").zfill(6)
    return store.symbol_data_ready(sym, require_features=require_features)


def _merge_by_date(
    old: pd.DataFrame,
    new: pd.DataFrame,
    *,
    date_col: str = "date",
) -> pd.DataFrame:
    if old is None or old.empty:
        out = new.copy() if new is not None else pd.DataFrame()
    elif new is None or new.empty:
        out = old.copy()
    else:
        out = pd.concat([old, new], ignore_index=True)
    if out.empty or date_col not in out.columns:
        return out
    out = out.copy()
    out[date_col] = pd.to_datetime(out[date_col])
    out = out.drop_duplicates(subset=[date_col], keep="last").sort_values(date_col)
    out[date_col] = out[date_col].dt.date
    return out


def _latest_bar_date(daily: pd.DataFrame) -> date | None:
    if daily.empty or "date" not in daily.columns:
        return None
    ts = pd.to_datetime(daily["date"]).max()
    return None if pd.isna(ts) else ts.date()


def _incremental_start_date(daily: pd.DataFrame, *, expected: date | None = None) -> str | None:
    last = _latest_bar_date(daily)
    if last is None:
        return None
    target = expected or date.today()
    if last >= target:
        return None
    return (last + timedelta(days=1)).strftime("%Y%m%d")


def _rebuild_lite_features(cfg: AppConfig, daily: pd.DataFrame, fund_flow: pd.DataFrame) -> None:
    """只持久化 minute_daily；feature_matrix / minute_bars 不再落库。"""
    del fund_flow  # 资金流已单独存表；矩阵在分析阶段现算
    sym = str(cfg.data.symbol).zfill(6)
    minute = expand_daily_to_minute_proxy(daily, cfg.data.symbol)
    minute_feat = compute_minute_features(minute, cfg.features.minute)
    minute_daily = summarize_minute_to_daily(minute_feat)
    store.save_series(sym, store.SERIES_MINUTE_DAILY, minute_daily)


def fetch_market_level_data(config: AppConfig, *, force_refresh: bool = False) -> None:
    """北向/两融等市场级数据，全市场共用一份。"""
    fetcher = MarketDataFetcher(config.data)

    north = store.load_market_series_df("northbound")
    if force_refresh or north.empty:
        north = fetcher.fetch_northbound_flow()
        store.save_market_series("northbound", north)
        logger.info("Saved market northbound")

    margin = store.load_market_series_df("margin")
    if force_refresh or margin.empty:
        margin = fetcher.fetch_margin_summary()
        store.save_market_series("margin", margin)
        logger.info("Saved market margin")


def refresh_symbol_lite(
    config: AppConfig,
    symbol: str,
    market: str,
    *,
    build_features: bool = True,
    skip_news: bool = True,
    expected_date: date | None = None,
) -> FetchRow:
    """增量刷新：仅拉取缺失交易日并追加到 PostgreSQL。"""
    cfg = _config_for_symbol(config, symbol, market)
    name = ""
    uni_path = cfg.data.root / "universe.csv"
    if uni_path.exists():
        uni = load_universe_csv(uni_path)
        hit = uni[uni["symbol"] == str(symbol).zfill(6)]
        if not hit.empty:
            name = str(hit.iloc[0].get("name", ""))

    sym = str(symbol).zfill(6)
    existing_daily = store.load_daily_bars(sym)
    if existing_daily.empty:
        return fetch_symbol_lite(
            config,
            symbol,
            market,
            build_features=build_features,
            skip_news=skip_news,
        )

    inc_start = _incremental_start_date(existing_daily, expected=expected_date)
    if inc_start is None:
        return FetchRow(
            symbol=symbol,
            market=market,
            name=name,
            status="skipped",
            daily_rows=len(existing_daily),
        )

    inc_cfg = replace(cfg, data=replace(cfg.data, start_date=inc_start))
    fetcher = MarketDataFetcher(inc_cfg.data, sleep_seconds=cfg.data.fetch_sleep_seconds)

    try:
        from market_platform.agents.核心.任务.陈旧检测 import sync_suspension_after_fetch

        new_daily = fetcher.fetch_daily_bars()
        if new_daily.empty:
            latest = store.latest_bar_date(sym)
            if expected_date is not None:
                sync_suspension_after_fetch(
                    sym, expected_date=expected_date, latest=latest, name=name
                )
            return FetchRow(
                symbol=symbol,
                market=market,
                name=name,
                status="skipped",
                daily_rows=len(existing_daily),
            )
        daily = _merge_by_date(existing_daily, new_daily)
        store.save_daily_bars(sym, daily, replace=True)

        existing_fund = store.load_fund_flow(sym)
        minute = expand_daily_to_minute_proxy(daily, symbol)
        fund_flow = existing_fund
        try:
            new_fund = fetcher.fetch_individual_fund_flow(daily=daily, minute=minute)
            if not new_fund.empty:
                fund_flow = _merge_by_date(existing_fund, new_fund)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Incremental fund flow failed %s: %s", symbol, exc)
        store.save_fund_flow(sym, fund_flow)

        if build_features:
            _rebuild_lite_features(cfg, daily, fund_flow)

        if not skip_news:
            try:
                DataPipeline(cfg).run_fetch_news(demo=False)
            except Exception as exc:  # noqa: BLE001
                logger.debug("News skipped %s: %s", symbol, exc)

        latest = store.latest_bar_date(sym)
        if expected_date is not None:
            sync_suspension_after_fetch(
                sym, expected_date=expected_date, latest=latest, name=name
            )

        return FetchRow(
            symbol=symbol,
            market=market,
            name=name,
            status="ok",
            daily_rows=len(daily),
            fund_flow_rows=len(fund_flow),
            minute_source="daily_proxy",
            daily_source=fetcher.last_daily_source or "incremental",
            fund_flow_source=fetcher.last_fund_flow_source or "",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Incremental refresh failed for %s", symbol)
        return FetchRow(
            symbol=symbol,
            market=market,
            name=name,
            status="error",
            error=str(exc),
        )


def fetch_symbol_lite(
    config: AppConfig,
    symbol: str,
    market: str,
    *,
    build_features: bool = True,
    skip_news: bool = True,
) -> FetchRow:
    """轻量拉取：日线 + 资金流 + 日线代理分钟线（不拉真实分钟）。"""
    cfg = _config_for_symbol(config, symbol, market)
    name = ""
    uni_path = cfg.data.root / "universe.csv"
    if uni_path.exists():
        uni = load_universe_csv(uni_path)
        hit = uni[uni["symbol"] == str(symbol).zfill(6)]
        if not hit.empty:
            name = str(hit.iloc[0].get("name", ""))
    sym = str(symbol).zfill(6)
    fetcher = MarketDataFetcher(cfg.data, sleep_seconds=cfg.data.fetch_sleep_seconds)

    try:
        daily = fetcher.fetch_daily_bars()
        store.save_daily_bars(sym, daily, replace=True)

        minute = expand_daily_to_minute_proxy(daily, symbol)

        fund_flow = pd.DataFrame()
        try:
            fund_flow = fetcher.fetch_individual_fund_flow(daily=daily, minute=minute)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fund flow failed %s: %s", symbol, exc)
        store.save_fund_flow(sym, fund_flow)

        if build_features:
            _rebuild_lite_features(cfg, daily, fund_flow)

        if not skip_news:
            try:
                DataPipeline(cfg).run_fetch_news(demo=False)
            except Exception as exc:  # noqa: BLE001
                logger.debug("News skipped %s: %s", symbol, exc)

        return FetchRow(
            symbol=symbol,
            market=market,
            name=name,
            status="ok",
            daily_rows=len(daily),
            fund_flow_rows=len(fund_flow),
            minute_source=fetcher.last_minute_source or "daily_proxy",
            daily_source=fetcher.last_daily_source or "",
            fund_flow_source=fetcher.last_fund_flow_source or "",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Lite fetch failed for %s", symbol)
        return FetchRow(
            symbol=symbol,
            market=market,
            name=name,
            status="error",
            error=str(exc),
        )


def run_universe_fetch(
    base_config: AppConfig,
    universe: pd.DataFrame,
    *,
    build_features: bool = True,
    skip_existing: bool = True,
    skip_news: bool = True,
    limit: int | None = None,
    offset: int = 0,
) -> pd.DataFrame:
    """批量拉取全市场数据。"""
    fetch_market_level_data(base_config)

    rows = universe.iloc[offset:]
    if limit is not None:
        rows = rows.head(limit)

    results: list[FetchRow] = []
    total = len(rows)
    for i, row in enumerate(rows.itertuples(index=False), start=1):
        symbol = str(row.symbol).zfill(6)
        market = str(getattr(row, "market", infer_market(symbol)))
        name = str(getattr(row, "name", ""))

        if skip_existing and _symbol_complete(Path("."), require_features=build_features, symbol=symbol):
            results.append(
                FetchRow(symbol=symbol, market=market, name=name, status="skipped")
            )
            if i % 100 == 0:
                logger.info("Progress %d/%d (skipped %s)", i, total, symbol)
            continue

        logger.info("[%d/%d] Fetching %s.%s %s", i, total, symbol, market, name)
        result = fetch_symbol_lite(
            base_config,
            symbol,
            market,
            build_features=build_features,
            skip_news=skip_news,
        )
        result.name = name
        results.append(result)

        if i % 50 == 0:
            _flush_progress(base_config.data.root, results)

    df = pd.DataFrame([r.__dict__ for r in results])
    _save_progress(base_config.data.root, df)
    return df


def _flush_progress(data_root: Path, results: list[FetchRow]) -> None:
    _save_progress(data_root, pd.DataFrame([r.__dict__ for r in results]))


def _save_progress(data_root: Path, df: pd.DataFrame) -> None:
    store.save_app_state("fetch_progress", df.to_dict(orient="records"))
    ok = (df["status"] == "ok").sum() if "status" in df.columns else 0
    err = (df["status"] == "error").sum() if "status" in df.columns else 0
    skip = (df["status"] == "skipped").sum() if "status" in df.columns else 0
    logger.info("Fetch progress: ok=%d skipped=%d error=%d", ok, skip, err)


def prepare_universe(
    data_root: Path,
    *,
    refresh_list: bool = False,
    exclude_st: bool = True,
    exclude_delisted: bool = True,
    boards: tuple[str, ...] = ("sh", "sz"),
) -> pd.DataFrame:
    list_path = data_root / "universe.csv"
    if list_path.exists() and not refresh_list:
        return load_universe_csv(list_path)

    df = load_a_share_universe(
        exclude_st=exclude_st,
        exclude_delisted=exclude_delisted,
        boards=boards,
    )
    save_universe(df, list_path)
    return df
