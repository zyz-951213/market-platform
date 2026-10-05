from __future__ import annotations

from pathlib import Path

import pandas as pd
from market_platform.agents.核心.路径.层级 import (
    find_dir_by_symbol,
    hierarchy_dir_for_symbol,
)

_RESERVED = {"_market", "portfolios"}


def symbol_data_dir(
    root: Path,
    symbol: str,
    *,
    sector: str = "",
    sector_l2: str = "",
    name: str = "",
    group_by_sector: bool = True,
    group_by_l2: bool = True,
    name_as_folder: bool = True,
    data_root: Path | None = None,
) -> Path:
    sym = str(symbol).zfill(6)
    root = Path(root)
    if not group_by_sector and not name_as_folder:
        return root / sym

    return hierarchy_dir_for_symbol(
        root,
        sym,
        data_root=data_root or root,
        sector_l1=sector,
        sector_l2=sector_l2,
        name=name,
        group_by_sector=group_by_sector,
        group_by_l2=group_by_l2,
        name_as_folder=name_as_folder,
    )


def load_data_registry(data_root: Path) -> pd.DataFrame:
    from market_platform.agents.存储 import data_access as store

    return store.load_symbols_registry()


def _data_dir_valid(path: Path) -> bool:
    return path.is_dir() and (
        (path / "daily_bars.parquet").exists()
        or (path / "minute_bars.parquet").exists()
        or (path / "symbol.txt").exists()
    )


def resolve_data_dir(data_root: Path, symbol: str) -> Path | None:
    """按 symbol 解析数据目录（支持行业/公司名或旧版代码目录）。"""
    data_root = Path(data_root)
    sym = str(symbol).zfill(6)

    registry = load_data_registry(data_root)
    if not registry.empty:
        rows = registry[registry["symbol"] == sym]
        if not rows.empty:
            rel = str(rows.iloc[0].get("path", "")).strip()
            if rel:
                candidate = data_root / rel
                if candidate.is_dir():
                    return candidate

    flat = data_root / sym
    if flat.is_dir():
        return flat

    found = find_dir_by_symbol(data_root, sym, validator=_data_dir_valid)
    if found is not None:
        return found

    for child in sorted(data_root.iterdir()):
        if not child.is_dir() or child.name in _RESERVED or child.name.isdigit():
            continue
        if child.name.endswith(".csv") or child.name.endswith(".parquet"):
            continue
        direct = child / sym
        if direct.is_dir():
            return direct
        for sub in child.iterdir():
            if not sub.is_dir():
                continue
            if (sub / "daily_bars.parquet").exists():
                meta = sub / "symbol.txt"
                if meta.exists() and meta.read_text(encoding="utf-8").strip().zfill(6) == sym:
                    return sub
            for leaf in sub.iterdir():
                if leaf.is_dir() and (leaf / "daily_bars.parquet").exists():
                    meta = leaf / "symbol.txt"
                    if meta.exists() and meta.read_text(encoding="utf-8").strip().zfill(6) == sym:
                        return leaf
    return None


def data_dir_for_config(
    data_root: Path,
    symbol: str,
    *,
    sector: str = "",
    name: str = "",
    group_by_sector: bool = True,
    group_by_l2: bool = True,
    name_as_folder: bool = True,
    create: bool = False,
) -> Path:
    from market_platform.agents.核心.工具.读写 import ensure_dir

    existing = resolve_data_dir(data_root, symbol)
    if existing is not None:
        return ensure_dir(existing) if create else existing
    path = symbol_data_dir(
        data_root,
        symbol,
        sector=sector,
        name=name,
        group_by_sector=group_by_sector,
        group_by_l2=group_by_l2,
        name_as_folder=name_as_folder,
        data_root=data_root,
    )
    return ensure_dir(path) if create else path
