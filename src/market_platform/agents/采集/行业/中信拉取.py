from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
from market_platform.agents.采集.股票池 import infer_market
from market_platform.agents.采集.行业.中信行业 import SHENWAN_TO_CITIC_L1

logger = logging.getLogger(__name__)

_EM_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax?code={code}"
_RE_L2 = re.compile(r"Ⅱ$")
_RE_L3 = re.compile(r"Ⅲ$")

# 东财一级 -> 中信2020一级
_EM_L1_TO_CITIC: dict[str, str] = {
    **SHENWAN_TO_CITIC_L1,
    "电力设备": "电力设备及新能源",
    "电力设备及新能源": "电力设备及新能源",
    "餐饮旅游": "消费者服务",
    "消费者服务": "消费者服务",
    "电子元器件": "电子",
    "非银金融": "非银行金融",
    "商贸零售": "商贸零售",
}


def _to_citic_l1(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return "综合"
    return _EM_L1_TO_CITIC.get(text, text)


def _to_citic_l2(name: str) -> str:
    text = _RE_L2.sub("", str(name or "").strip())
    return text or "综合"


def _to_citic_l3(name: str) -> str:
    text = _RE_L3.sub("", str(name or "").strip())
    return text or "综合"


def _fetch_one(symbol: str, market: str = "") -> dict | None:
    sym = str(symbol).zfill(6)
    mkt = (market or infer_market(sym)).lower()
    code = f"SH{sym}" if mkt == "sh" else f"SZ{sym}"
    url = _EM_URL.format(code=code)
    try:
        proc = subprocess.run(
            ["curl", "-sS", "--max-time", "12", "-H", "User-Agent: Mozilla/5.0", url],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(proc.stdout)
    except Exception:  # noqa: BLE001
        return None

    ssbk = payload.get("ssbk") or []
    ranks = {int(x.get("BOARD_RANK", 0)): str(x.get("BOARD_NAME", "")).strip() for x in ssbk}
    l1_raw = ranks.get(1, "")
    l2_raw = ranks.get(2, "")
    l3_raw = ranks.get(3, "")
    if not l1_raw:
        return None

    l1 = _to_citic_l1(l1_raw)
    l2 = _to_citic_l2(l2_raw) if l2_raw else l1
    l3 = _to_citic_l3(l3_raw) if l3_raw else l2
    name = ""
    for x in ssbk:
        if str(x.get("BOARD_RANK")) == "1" and x.get("SECURITY_NAME_ABBR"):
            name = str(x["SECURITY_NAME_ABBR"]).strip()
            break

    return {
        "symbol": sym,
        "name": name,
        "sector_l1": l1,
        "sector_l2": l2,
        "sector_l3": l3,
    }


def fetch_citic_hierarchy(
    symbols: list[str],
    *,
    markets: dict[str, str] | None = None,
    max_workers: int = 24,
    sleep_every: int = 0,
) -> pd.DataFrame:
    """并发拉取东财 F10 中信行业（一/二/三级）。"""
    markets = markets or {}
    rows: list[dict] = []
    todo = [str(s).zfill(6) for s in symbols]
    done = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_fetch_one, sym, markets.get(sym, "")): sym for sym in todo
        }
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                row = fut.result()
                if row:
                    rows.append(row)
            except Exception as exc:  # noqa: BLE001
                logger.debug("fetch %s failed: %s", sym, exc)
            done += 1
            if done % 200 == 0:
                logger.info("CITIC fetch progress %d/%d", done, len(todo))
            if sleep_every and done % sleep_every == 0:
                time.sleep(0.05)

    if not rows:
        return pd.DataFrame(columns=["symbol", "name", "sector_l1", "sector_l2", "sector_l3"])
    df = pd.DataFrame(rows)
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    return df.drop_duplicates("symbol").sort_values(["sector_l1", "sector_l2", "sector_l3", "symbol"])


def build_citic_hierarchy_df(
    data_root: Path,
    *,
    refresh: bool = False,
    max_workers: int = 24,
    symbols: list[str] | None = None,
) -> pd.DataFrame:
    """构建含中信三级的 sector_hierarchy.csv。"""
    data_root = Path(data_root)
    cache = data_root / "sector_hierarchy.csv"
    if cache.exists() and not refresh:
        df = pd.read_csv(cache, dtype={"symbol": str})
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        if not df.empty and "sector_l3" in df.columns:
            l1 = df["sector_l1"].astype(str).str.strip()
            l2 = df["sector_l2"].astype(str).str.strip()
            l3 = df["sector_l3"].astype(str).str.strip()
            if len(df) >= 10 and (l2.ne(l1) | l3.ne(l2)).any():
                return df

    markets: dict[str, str] = {}
    if symbols:
        todo = [str(s).zfill(6) for s in symbols]
    else:
        todo = []
        uni = data_root / "universe.csv"
        if uni.exists():
            u = pd.read_csv(uni, dtype={"symbol": str})
            u["symbol"] = u["symbol"].astype(str).str.zfill(6)
            todo = u["symbol"].tolist()
            if "market" in u.columns:
                markets = dict(zip(u["symbol"], u["market"].astype(str), strict=False))
    if not todo and cache.exists():
        old = pd.read_csv(cache, dtype={"symbol": str})
        todo = old["symbol"].astype(str).str.zfill(6).tolist()

    if not todo:
        return pd.DataFrame(columns=["symbol", "name", "sector_l1", "sector_l2", "sector_l3"])

    logger.info("Fetching CITIC L1/L2/L3 for %d symbols...", len(todo))
    fetched = fetch_citic_hierarchy(todo, markets=markets, max_workers=max_workers)

    # 合并本地名称
    sm = data_root / "sector_map.csv"
    if sm.exists() and not fetched.empty:
        base = pd.read_csv(sm, dtype={"symbol": str})
        base["symbol"] = base["symbol"].astype(str).str.zfill(6)
        fetched = fetched.merge(base[["symbol", "name"]].rename(columns={"name": "name_map"}), on="symbol", how="left")
        empty = fetched["name"].fillna("").astype(str).str.strip().eq("")
        fetched.loc[empty, "name"] = fetched.loc[empty, "name_map"]
        fetched = fetched.drop(columns=["name_map"], errors="ignore")

    fetched["name"] = fetched["name"].fillna("").astype(str)
    for col in ("sector_l1", "sector_l2", "sector_l3"):
        fetched[col] = fetched[col].fillna("综合").replace("", "综合")
    fetched["sector_l2"] = fetched["sector_l2"].fillna(fetched["sector_l1"])
    fetched["sector_l3"] = fetched["sector_l3"].fillna(fetched["sector_l2"])

    if fetched.empty:
        if cache.exists():
            old = pd.read_csv(cache, dtype={"symbol": str})
            old["symbol"] = old["symbol"].astype(str).str.zfill(6)
            if not old.empty:
                logger.warning("CITIC fetch returned empty; keeping existing cache (%d rows)", len(old))
                return old
        return fetched

    data_root.mkdir(parents=True, exist_ok=True)
    fetched.to_csv(cache, index=False)
    logger.info(
        "CITIC hierarchy saved: %d symbols, L1=%d L2=%d L3=%d",
        len(fetched),
        fetched["sector_l1"].nunique(),
        fetched["sector_l2"].nunique(),
        fetched["sector_l3"].nunique(),
    )
    return fetched.reset_index(drop=True)
