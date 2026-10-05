"""股票名称补全：本地多源合并 + 可选远端回填。"""

from __future__ import annotations

import logging
import re
import urllib.request
from typing import Any, Iterable

import pandas as pd
from market_platform.agents.存储 import data_access as store
from market_platform.agents.采集.股票池 import infer_market

logger = logging.getLogger(__name__)


def fetch_symbol_name_tencent(symbol: str) -> str:
    sym = str(symbol).zfill(6)
    mkt = infer_market(sym)
    url = f"https://qt.gtimg.cn/q={mkt}{sym}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            text = resp.read().decode("gbk", errors="ignore")
        m = re.search(r'="(\d+)~([^~]+)~', text)
        if m:
            return str(m.group(2)).strip()
    except Exception:
        logger.debug("Tencent name fetch failed for %s", sym, exc_info=True)
    return ""


def _put(names: dict[str, str], sym: Any, name: Any) -> None:
    code = str(sym or "").zfill(6)
    label = str(name or "").strip()
    if code and label and code not in names:
        names[code] = label


def load_local_name_map() -> dict[str, str]:
    """从 registry / summary / universe 合并本地已有名称。"""
    names: dict[str, str] = {}

    try:
        reg = store.load_symbols_registry()
        if not reg.empty and "symbol" in reg.columns and "name" in reg.columns:
            for row in reg.itertuples(index=False):
                _put(names, row.symbol, getattr(row, "name", ""))
    except Exception:
        logger.debug("load registry names failed", exc_info=True)

    try:
        summary = store.load_symbol_summary()
        if not summary.empty and "symbol" in summary.columns and "name" in summary.columns:
            for row in summary.itertuples(index=False):
                _put(names, row.symbol, getattr(row, "name", ""))
    except Exception:
        logger.debug("load summary names failed", exc_info=True)

    try:
        from market_platform.agents.分析.批处理.增量 import load_universe_meta
        from market_platform.agents.核心.路径.项目 import data_dir

        meta = load_universe_meta(data_dir())
        if not meta.empty and "symbol" in meta.columns and "name" in meta.columns:
            for row in meta.itertuples(index=False):
                _put(names, row.symbol, getattr(row, "name", ""))
    except Exception:
        logger.debug("load universe meta names failed", exc_info=True)

    return names


def load_akshare_name_map() -> dict[str, str]:
    """一次拉取全 A 股代码-名称表。"""
    names: dict[str, str] = {}
    try:
        import akshare as ak

        raw = ak.stock_info_a_code_name()
        df = raw.rename(columns={"code": "symbol", "name": "name"}).copy()
        for row in df.itertuples(index=False):
            _put(names, getattr(row, "symbol", ""), getattr(row, "name", ""))
    except Exception:
        logger.warning("akshare stock_info_a_code_name failed", exc_info=True)
    return names


def persist_names(name_map: dict[str, str], *, symbols: Iterable[str] | None = None) -> int:
    """把名称写回 symbols / symbol_summary（空名不覆盖由 DB COALESCE 保证）。"""
    codes = {str(s).zfill(6) for s in (symbols if symbols is not None else name_map.keys())}
    rows = []
    for sym in sorted(codes):
        label = str(name_map.get(sym) or "").strip()
        if not label:
            continue
        rows.append({"symbol": sym, "name": label, "market": infer_market(sym)})
    if not rows:
        return 0
    n = store.upsert_symbols_registry(rows)
    # 同步更新 summary 里的空名
    try:
        summary = store.load_symbol_summary()
        if not summary.empty:
            patch = []
            for row in summary.itertuples(index=False):
                sym = str(row.symbol).zfill(6)
                label = str(name_map.get(sym) or "").strip()
                if not label:
                    continue
                cur = str(getattr(row, "name", "") or "").strip()
                if cur:
                    continue
                patch.append(
                    {
                        "symbol": sym,
                        "name": label,
                        "sector": getattr(row, "sector", ""),
                        "latest_state": getattr(row, "latest_state", ""),
                        "suggested_position": getattr(row, "suggested_position", None),
                        "strategy_return": getattr(row, "strategy_return", None),
                        "benchmark_return": getattr(row, "benchmark_return", None),
                        "excess_return": getattr(row, "excess_return", None),
                        "strategy_sharpe": getattr(row, "strategy_sharpe", None),
                        "anomalies": getattr(row, "anomalies", None),
                        "status": getattr(row, "status", "ok"),
                        "output_path": getattr(row, "output_path", "") or getattr(row, "path", ""),
                    }
                )
            if patch:
                store.save_symbol_summary_rows(patch)
    except Exception:
        logger.debug("sync summary names failed", exc_info=True)
    return n


def build_symbol_name_map(
    symbols: Iterable[str],
    *,
    extra: dict[str, str] | None = None,
    fetch_missing: bool = True,
    persist: bool = True,
) -> dict[str, str]:
    """合并本地/额外来源；缺失时可选腾讯补全并落库。"""
    names = load_local_name_map()
    if extra:
        for sym, label in extra.items():
            _put(names, sym, label)

    need = {str(s).zfill(6) for s in symbols if str(s).zfill(6) not in names}
    fetched: dict[str, str] = {}
    if fetch_missing and need:
        for sym in sorted(need):
            label = fetch_symbol_name_tencent(sym)
            if label:
                names[sym] = label
                fetched[sym] = label
        if persist and fetched:
            try:
                persist_names(fetched)
            except Exception:
                logger.debug("persist fetched names failed", exc_info=True)
    return names


def fill_dataframe_names(
    df: pd.DataFrame,
    *,
    fetch_missing: bool = False,
    persist: bool = True,
) -> pd.DataFrame:
    """就地补全 DataFrame 的 name 列。"""
    if df is None or df.empty or "symbol" not in df.columns:
        return df
    out = df.copy()
    if "name" not in out.columns:
        out["name"] = ""
    out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    blank = out["name"].fillna("").astype(str).str.strip().eq("")
    if not blank.any():
        return out

    need = out.loc[blank, "symbol"].tolist()
    name_map = build_symbol_name_map(need, fetch_missing=fetch_missing, persist=persist)
    out.loc[blank, "name"] = out.loc[blank, "symbol"].map(lambda s: name_map.get(s, ""))
    return out


def backfill_blank_names(*, use_akshare: bool = True, use_tencent: bool = True, limit: int | None = None) -> dict[str, int]:
    """一次性回填 symbols / symbol_summary 空名。"""
    summary = store.load_symbol_summary()
    reg = store.load_symbols_registry()
    blank_syms: set[str] = set()
    for df in (summary, reg):
        if df is None or df.empty or "symbol" not in df.columns:
            continue
        name_col = df["name"] if "name" in df.columns else pd.Series("", index=df.index)
        mask = name_col.fillna("").astype(str).str.strip().eq("")
        blank_syms.update(df.loc[mask, "symbol"].astype(str).str.zfill(6).tolist())

    blank_before = len(blank_syms)
    if limit is not None:
        blank_syms = set(sorted(blank_syms)[: max(0, int(limit))])

    names: dict[str, str] = {}
    akshare_ok = 0
    if use_akshare and blank_syms:
        ak_map = load_akshare_name_map()
        for sym in list(blank_syms):
            if ak_map.get(sym):
                names[sym] = ak_map[sym]
                blank_syms.discard(sym)
                akshare_ok += 1

    tencent_ok = 0
    if use_tencent and blank_syms:
        for sym in sorted(blank_syms):
            label = fetch_symbol_name_tencent(sym)
            if label:
                names[sym] = label
                blank_syms.discard(sym)
                tencent_ok += 1

    persisted = persist_names(names) if names else 0
    return {
        "blank_before": blank_before,
        "filled": akshare_ok + tencent_ok,
        "akshare": akshare_ok,
        "tencent": tencent_ok,
        "persisted": persisted,
        "still_blank": len(blank_syms),
    }
