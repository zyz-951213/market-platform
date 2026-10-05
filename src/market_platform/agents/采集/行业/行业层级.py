from __future__ import annotations

import logging
import threading
from pathlib import Path

import pandas as pd
from market_platform.agents.采集.行业.中信拉取 import build_citic_hierarchy_df

logger = logging.getLogger(__name__)

_hierarchy_lock = threading.Lock()
_hierarchy_fetching = False


def hierarchy_is_usable(df: pd.DataFrame) -> bool:
    """判断行业层级是否含有效的二/三级区分。"""
    if df.empty or len(df) < 10:
        return False
    for col in ("sector_l1", "sector_l2", "sector_l3"):
        if col not in df.columns:
            return False
    l1 = df["sector_l1"].astype(str).str.strip()
    l2 = df["sector_l2"].astype(str).str.strip()
    l3 = df["sector_l3"].astype(str).str.strip()
    valid = l1.ne("") & l1.ne("未分类") & l1.ne("综合")
    if valid.sum() < 10:
        return False
    # 至少部分标的具有不同于一级的二/三级
    diverse = (l2.ne(l1) | l3.ne(l2)).any()
    return bool(diverse)


def sync_hierarchy_registry(df: pd.DataFrame) -> int:
    """将层级写入 PostgreSQL symbols 表。"""
    from market_platform.agents.存储 import data_access as store

    if df.empty or not store.pg_enabled():
        return 0
    rows = []
    for _, row in df.iterrows():
        sym = str(row["symbol"]).zfill(6)
        rows.append(
            {
                "symbol": sym,
                "name": str(row.get("name", "") or ""),
                "sector": str(row.get("sector_l1", "") or row.get("sector", "") or ""),
                "sector_l1": str(row.get("sector_l1", "") or ""),
                "sector_l2": str(row.get("sector_l2", "") or ""),
                "sector_l3": str(row.get("sector_l3", "") or ""),
            }
        )
    store.upsert_symbols_registry(rows)
    return len(rows)


def ensure_sector_hierarchy(
    data_root: Path,
    symbols: list[str],
    *,
    refresh: bool = False,
    max_workers: int = 32,
    background: bool = True,
) -> pd.DataFrame:
    """确保 sector_hierarchy.csv 含中信二/三级；必要时从东财 F10 拉取。"""
    global _hierarchy_fetching
    data_root = Path(data_root)
    data_root.mkdir(parents=True, exist_ok=True)

    cache = data_root / "sector_hierarchy.csv"
    if cache.exists() and not refresh:
        cached = pd.read_csv(cache, dtype={"symbol": str})
        cached["symbol"] = cached["symbol"].astype(str).str.zfill(6)
        if hierarchy_is_usable(cached):
            return cached

    todo = sorted({str(s).zfill(6) for s in symbols if str(s).strip()})
    if not todo:
        return pd.DataFrame(columns=["symbol", "name", "sector_l1", "sector_l2", "sector_l3"])

    def _run_fetch() -> pd.DataFrame:
        global _hierarchy_fetching
        uni = data_root / "universe.csv"
        if not uni.exists():
            pd.DataFrame({"symbol": todo}).to_csv(uni, index=False)
        logger.info("Fetching CITIC hierarchy for %d symbols...", len(todo))
        fetched = build_citic_hierarchy_df(
            data_root,
            refresh=True,
            max_workers=max_workers,
            symbols=todo,
        )
        if hierarchy_is_usable(fetched):
            sync_hierarchy_registry(fetched)
        return fetched

    if background:
        with _hierarchy_lock:
            if not _hierarchy_fetching:
                _hierarchy_fetching = True

                def _bg() -> None:
                    global _hierarchy_fetching
                    try:
                        _run_fetch()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Background hierarchy fetch failed: %s", exc)
                    finally:
                        _hierarchy_fetching = False

                threading.Thread(target=_bg, daemon=True, name="citic-hierarchy").start()
        if cache.exists():
            cached = pd.read_csv(cache, dtype={"symbol": str})
            cached["symbol"] = cached["symbol"].astype(str).str.zfill(6)
            if not cached.empty:
                return cached
        return pd.DataFrame(columns=["symbol", "name", "sector_l1", "sector_l2", "sector_l3"])

    return _run_fetch()


def load_hierarchy(data_root: Path) -> pd.DataFrame:
    from market_platform.agents.存储 import data_access as store
    from market_platform.agents.核心.路径.层级 import normalize_sector_hierarchy_df

    df = store.load_hierarchy_df()
    if not df.empty:
        return normalize_sector_hierarchy_df(df)

    data_root = Path(data_root)
    return normalize_sector_hierarchy_df(build_citic_hierarchy_df(data_root, refresh=False))


def load_effective_hierarchy(data_root: Path) -> pd.DataFrame:
    """优先 sector_hierarchy.csv，再回退 DB 注册表。"""
    from market_platform.agents.存储 import data_access as store
    from market_platform.agents.核心.路径.层级 import normalize_sector_hierarchy_df

    data_root = Path(data_root)
    file_hier = build_citic_hierarchy_df(data_root, refresh=False)
    if hierarchy_is_usable(file_hier):
        return normalize_sector_hierarchy_df(file_hier)

    hier = store.load_hierarchy_df()
    if hier.empty:
        return normalize_sector_hierarchy_df(file_hier) if not file_hier.empty else pd.DataFrame()

    hier = normalize_sector_hierarchy_df(hier)
    if file_hier.empty:
        return hier

    file_hier = normalize_sector_hierarchy_df(file_hier)
    merged = hier.merge(
        file_hier[["symbol", "sector_l1", "sector_l2", "sector_l3", "name"]],
        on="symbol",
        how="left",
        suffixes=("", "_file"),
    )

    def _meaningful(series: pd.Series) -> pd.Series:
        text = series.astype(str).str.strip()
        return text.ne("") & text.ne("未分类") & text.ne("综合")

    for col in ("sector_l1", "sector_l2", "sector_l3"):
        file_col = f"{col}_file"
        if file_col not in merged.columns:
            continue
        mask = _meaningful(merged[file_col])
        if mask.any():
            merged.loc[mask, col] = merged.loc[mask, file_col]
    if "name_file" in merged.columns:
        blank_name = merged["name"].astype(str).str.strip().eq("")
        has_name = merged["name_file"].astype(str).str.strip().ne("")
        fill = blank_name & has_name
        if fill.any():
            merged.loc[fill, "name"] = merged.loc[fill, "name_file"]
    drop_cols = [c for c in merged.columns if c.endswith("_file")]
    return normalize_sector_hierarchy_df(merged.drop(columns=drop_cols, errors="ignore"))


def build_hierarchy_df(data_root: Path, *, refresh: bool = False) -> pd.DataFrame:
    return build_citic_hierarchy_df(data_root, refresh=refresh)


def build_sector_tree(hierarchy: pd.DataFrame) -> list[dict]:
    """构建 L1 → L2 → L3 → 个股 四级树。"""
    if hierarchy.empty:
        return []

    tree: list[dict] = []
    for l1, l1_df in hierarchy.groupby("sector_l1", sort=True):
        l1_node = {"name": str(l1), "level": 1, "count": int(len(l1_df)), "children": []}
        for l2, l2_df in l1_df.groupby("sector_l2", sort=True):
            l2_node = {"name": str(l2), "level": 2, "count": int(len(l2_df)), "children": []}
            for l3, l3_df in l2_df.groupby("sector_l3", sort=True):
                symbols = []
                for _, row in l3_df.sort_values(["name", "symbol"]).iterrows():
                    symbols.append(
                        {
                            "symbol": str(row["symbol"]).zfill(6),
                            "name": str(row.get("name", "")),
                            "level": 4,
                        }
                    )
                l2_node["children"].append(
                    {
                        "name": str(l3),
                        "level": 3,
                        "count": len(symbols),
                        "children": symbols,
                    }
                )
            l1_node["children"].append(l2_node)
        tree.append(l1_node)
    return tree
