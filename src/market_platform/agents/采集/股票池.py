from __future__ import annotations

import logging
import re

import pandas as pd

logger = logging.getLogger(__name__)

_ST_NAME_PATTERN = re.compile(r"ST|退|\*")


def infer_market(symbol: str) -> str:
    code = str(symbol).zfill(6)
    if code.startswith(("6", "5")):
        return "sh"
    if code.startswith(("8", "4", "92")):
        return "bj"
    return "sz"


def load_a_share_universe(
    *,
    exclude_st: bool = True,
    exclude_delisted: bool = True,
    boards: tuple[str, ...] = ("sh", "sz"),
) -> pd.DataFrame:
    """加载 A 股代码列表（akshare）。"""
    import akshare as ak

    raw = ak.stock_info_a_code_name()
    df = raw.rename(columns={"code": "symbol", "name": "name"}).copy()
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    df["market"] = df["symbol"].map(infer_market)

    if boards:
        df = df[df["market"].isin(boards)]

    if exclude_st:
        df = df[~df["name"].astype(str).str.contains(_ST_NAME_PATTERN, regex=True, na=False)]

    if exclude_delisted:
        df = df[~df["name"].astype(str).str.contains("退", na=False)]

    return df.sort_values("symbol").reset_index(drop=True)


def save_universe(df: pd.DataFrame, path) -> None:
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    logger.info("Universe saved -> %s (%d symbols)", path, len(df))


def load_universe_csv(path) -> pd.DataFrame:
    from pathlib import Path

    path = Path(path)
    df = pd.read_csv(path, dtype={"symbol": str})
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    if "market" not in df.columns:
        df["market"] = df["symbol"].map(infer_market)
    return df.sort_values("symbol").reset_index(drop=True)
