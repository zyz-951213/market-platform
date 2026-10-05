from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pandas as pd

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|]')


def normalize_hierarchy_path(rel_path: str) -> str:
    """去掉路径中连续重复层级，如 白酒/白酒/贵州茅台 → 白酒/贵州茅台。"""
    parts = [p for p in str(rel_path).split("/") if p]
    if not parts:
        return ""
    out = [parts[0]]
    for part in parts[1:]:
        if part != out[-1]:
            out.append(part)
    return "/".join(out)


def canonical_output_rel_path(sector_l1: str, name: str, symbol: str) -> str:
    folder = sanitize_folder_name(name, fallback=str(symbol).zfill(6))
    l1 = sanitize_folder_name(sector_l1, fallback="未分类")
    return f"{l1}/{folder}"


def sanitize_folder_name(name: str, *, fallback: str = "") -> str:
    text = str(name or "").strip()
    text = _INVALID_CHARS.sub("", text)
    text = re.sub(r"\s+", "", text)
    if not text:
        text = fallback
    return text[:80] if text else fallback


CLASSIFICATION_STANDARD = {
    "name": "中信证券行业分类",
    "name_en": "CITIC Industry Classification",
    "levels": [
        {"level": 1, "field": "sector_l1", "standard": "中信一级行业", "folder": True},
        {"level": 2, "field": "sector_l2", "standard": "中信二级行业", "folder": True},
        {"level": 3, "field": "sector_l3", "standard": "中信三级行业", "folder": True},
        {"level": 4, "field": "name", "standard": "上市公司简称", "folder": True},
    ],
    "path_pattern": "{sector_l1}/{sector_l2}/{sector_l3}/{name}/",
    "description": "采用中信证券行业分类（一级/二级/三级），目录为四级路径。",
}


def lookup_symbol_hierarchy(data_root: Path, symbol: str) -> dict[str, str]:
    """从 sector_hierarchy.csv 查询多级行业与名称。"""
    from market_platform.agents.采集.行业.行业层级 import load_hierarchy

    sym = str(symbol).zfill(6)
    data_root = Path(data_root)
    hier = load_hierarchy(data_root)
    if hier.empty:
        return {"symbol": sym, "name": "", "sector_l1": "未分类", "sector_l2": "未分类", "sector": "未分类"}

    rows = hier[hier["symbol"].astype(str).str.zfill(6) == sym]
    if rows.empty:
        from market_platform.agents.采集.行业.行业映射 import lookup_sector

        sector = lookup_sector(data_root, sym)
        l1 = sector or "未分类"
        return {"symbol": sym, "name": "", "sector_l1": l1, "sector_l2": l1, "sector": l1}

    row = rows.iloc[0]
    l1 = str(row.get("sector_l1", "") or "未分类").strip() or "未分类"
    l2 = str(row.get("sector_l2", "") or l1).strip() or l1
    l3 = str(row.get("sector_l3", "") or l2).strip() or l2
    name = str(row.get("name", "") or "").strip()
    return {"symbol": sym, "name": name, "sector_l1": l1, "sector_l2": l2, "sector_l3": l3, "sector": l1}


def symbol_hierarchy_dir(
    root: Path,
    symbol: str,
    *,
    sector_l1: str = "",
    sector_l2: str = "",
    sector_l3: str = "",
    name: str = "",
    group_by_sector: bool = True,
    group_by_l2: bool = True,
    group_by_l3: bool = True,
    name_as_folder: bool = True,
) -> Path:
    """构造目录：{中信一级}/{二级}/{三级}/{公司名}/。"""
    sym = str(symbol).zfill(6)
    root = Path(root)
    folder_name = sanitize_folder_name(name, fallback=sym) if name_as_folder else sym

    if not group_by_sector:
        return root / folder_name

    l1 = sanitize_folder_name((sector_l1 or "").strip() or "未分类", fallback="未分类")
    l2_raw = (sector_l2 or "").strip() or l1
    l2 = sanitize_folder_name(l2_raw, fallback=l1)
    if not group_by_l2:
        return root / l1 / folder_name
    l3_raw = (sector_l3 or "").strip()
    if group_by_l3 and l3_raw:
        l3 = sanitize_folder_name(l3_raw, fallback=l2)
        if l3 and l3 != l2:
            return root / l1 / l2 / l3 / folder_name
    return root / l1 / l2 / folder_name


def hierarchy_dir_for_symbol(
    root: Path,
    symbol: str,
    *,
    data_root: Path | None = None,
    sector_l1: str = "",
    sector_l2: str = "",
    sector_l3: str = "",
    name: str = "",
    group_by_sector: bool = True,
    group_by_l2: bool = True,
    group_by_l3: bool = True,
    name_as_folder: bool = True,
) -> Path:
    """优先使用显式参数，否则从 hierarchy 索引补全。"""
    sym = str(symbol).zfill(6)
    meta = lookup_symbol_hierarchy(data_root or root, sym) if data_root or root else {}
    l1 = (sector_l1 or meta.get("sector_l1") or meta.get("sector") or "未分类").strip() or "未分类"
    l2 = (sector_l2 or meta.get("sector_l2") or l1).strip() or l1
    l3 = (sector_l3 or meta.get("sector_l3") or "").strip()
    nm = (name or meta.get("name") or "").strip()
    return symbol_hierarchy_dir(
        root,
        sym,
        sector_l1=l1,
        sector_l2=l2,
        sector_l3=l3,
        name=nm,
        group_by_sector=group_by_sector,
        group_by_l2=group_by_l2,
        group_by_l3=group_by_l3,
        name_as_folder=name_as_folder,
    )


def find_dir_by_symbol(
    root: Path,
    symbol: str,
    *,
    validator: Callable[[Path], bool] | None = None,
) -> Path | None:
    """按 symbol.txt 深度扫描目录（兼容任意层级）。"""
    root = Path(root)
    if not root.is_dir():
        return None
    sym = str(symbol).zfill(6)

    for sym_file in root.rglob("symbol.txt"):
        try:
            if sym_file.read_text(encoding="utf-8").strip().zfill(6) != sym:
                continue
        except OSError:
            continue
        candidate = sym_file.parent
        if validator is None or validator(candidate):
            return candidate

    flat = root / sym
    if flat.is_dir() and (validator is None or validator(flat)):
        return flat
    return None


def registry_record(
    symbol: str,
    *,
    data_root: Path,
    rel_path: str,
    sector_l1: str = "",
    sector_l2: str = "",
    sector_l3: str = "",
    name: str = "",
    market: str = "",
    status: str = "ok",
) -> dict[str, str]:
    meta = lookup_symbol_hierarchy(data_root, symbol)
    l1 = sector_l1 or meta["sector_l1"]
    l2 = sector_l2 or meta["sector_l2"]
    l3 = sector_l3 or meta.get("sector_l3", l2)
    nm = name or meta["name"]
    return {
        "symbol": str(symbol).zfill(6),
        "name": nm,
        "sector": l1,
        "sector_l1": l1,
        "sector_l2": l2,
        "sector_l3": l3,
        "path": rel_path,
        "market": market,
        "status": status,
    }


def _blank_mask(series: pd.Series) -> pd.Series:
    return series.isna() | series.astype(str).str.strip().eq("")


def _weak_mask(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    return series.isna() | text.eq("") | text.eq("未分类")


def _map_citic_l1(name: str) -> str:
    from market_platform.agents.采集.行业.中信行业 import SHENWAN_TO_CITIC_L1

    text = str(name or "").strip()
    if not text or text == "未分类":
        return "未分类"
    return SHENWAN_TO_CITIC_L1.get(text, text)


def normalize_sector_hierarchy_df(df: pd.DataFrame) -> pd.DataFrame:
    """补齐一/二/三级行业：空字符串回退到 sector，并映射为中信一级。"""
    if df.empty:
        return df
    out = df.copy()
    out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    if "sector" not in out.columns:
        out["sector"] = ""
    for col in ("sector_l1", "sector_l2", "sector_l3", "name"):
        if col not in out.columns:
            out[col] = ""

    blank_l1 = _blank_mask(out["sector_l1"])
    if blank_l1.any():
        mapped = out.loc[blank_l1, "sector"].map(_map_citic_l1)
        out.loc[blank_l1, "sector_l1"] = mapped

    blank_l2 = _weak_mask(out["sector_l2"])
    if blank_l2.any():
        out.loc[blank_l2, "sector_l2"] = out.loc[blank_l2, "sector_l1"]

    blank_l3 = _weak_mask(out["sector_l3"])
    if blank_l3.any():
        out.loc[blank_l3, "sector_l3"] = out.loc[blank_l3, "sector_l2"]

    out["sector_l1"] = out["sector_l1"].astype(str).str.strip().replace("", "未分类")
    out["sector_l2"] = out["sector_l2"].astype(str).str.strip().replace("", "未分类")
    out["sector_l3"] = out["sector_l3"].astype(str).str.strip().replace("", "未分类")
    blank_sector = _blank_mask(out["sector"])
    if blank_sector.any():
        out.loc[blank_sector, "sector"] = out.loc[blank_sector, "sector_l1"]
    return out


def ensure_registry_columns(df: pd.DataFrame) -> pd.DataFrame:
    return normalize_sector_hierarchy_df(df)
