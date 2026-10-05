from __future__ import annotations

from pathlib import Path

import pandas as pd
from market_platform.agents.settings import AppConfig
from market_platform.agents.核心.路径.层级 import (
    find_dir_by_symbol,
    hierarchy_dir_for_symbol,
    lookup_symbol_hierarchy,
    sanitize_folder_name,
)
from market_platform.agents.核心.路径.项目 import data_dir

_OUTPUT_INDEX_NAME = "stocks_index.csv"
_EQ_REL = Path("backtest") / "equity_curve.parquet"
_RESERVED = {"portfolios"}


def symbol_output_dir(
    root: Path,
    symbol: str,
    *,
    sector: str = "",
    sector_l2: str = "",
    sector_l3: str = "",
    name: str = "",
    group_by_sector: bool = False,
    group_by_l2: bool = False,
    group_by_l3: bool = False,
    name_as_folder: bool = False,
    data_root: Path | None = None,
) -> Path:
    sym = str(symbol).zfill(6)
    root = Path(root)
    if not group_by_sector:
        folder = sanitize_folder_name(name, fallback=sym) if name_as_folder else sym
        return root / folder

    return hierarchy_dir_for_symbol(
        root,
        sym,
        data_root=data_root or data_dir(),
        sector_l1=sector,
        sector_l2=sector_l2,
        sector_l3=sector_l3,
        name=name,
        group_by_sector=True,
        group_by_l2=group_by_l2,
        group_by_l3=group_by_l3,
        name_as_folder=name_as_folder,
    )


def config_symbol_output_dir(config: AppConfig) -> Path:
    return symbol_output_dir(
        config.output.root,
        config.data.symbol,
        sector=config.data.sector,
        name=config.data.name,
        group_by_sector=config.output.group_by_sector,
        group_by_l2=config.output.group_by_l2,
        name_as_folder=config.output.name_as_folder,
        data_root=config.data.root,
    )


_REGISTRY_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}


def _read_registry_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    mtime = path.stat().st_mtime
    key = str(path.resolve())
    cached = _REGISTRY_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    df = pd.read_csv(path, dtype={"symbol": str})
    if not df.empty and "symbol" in df.columns:
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    _REGISTRY_CACHE[key] = (mtime, df)
    return df


def load_output_registry(output_root: Path) -> pd.DataFrame:
    path = Path(output_root) / _OUTPUT_INDEX_NAME
    if path.exists():
        return _read_registry_csv(path)

    frames: list[pd.DataFrame] = []
    for name in ("batch_summary.csv", "universe_batch_summary.csv", "daily_batch_summary.csv"):
        summary = output_root / name
        if summary.exists():
            frames.append(_read_registry_csv(summary))
    if not frames:
        return pd.DataFrame(columns=["symbol", "name", "sector", "path", "status"])
    merged = pd.concat(frames, ignore_index=True)
    if "sector" in merged.columns:
        merged["_has_sector"] = merged["sector"].fillna("").astype(str).str.strip().ne("")
        merged = merged.sort_values(["symbol", "_has_sector"], ascending=[True, False])
        merged = merged.drop_duplicates(subset=["symbol"], keep="first").drop(columns=["_has_sector"])
    else:
        merged = merged.drop_duplicates(subset=["symbol"], keep="first")
    return merged.reset_index(drop=True)


def save_output_registry(df: pd.DataFrame, output_root: Path) -> Path:
    from market_platform.agents.核心.路径.层级 import ensure_registry_columns

    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / _OUTPUT_INDEX_NAME
    out = ensure_registry_columns(df.copy())
    out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    cols = [c for c in ("sector", "sector_l1", "sector_l2", "sector_l3", "symbol", "name", "path", "status") if c in out.columns]
    sort_cols = [c for c in ("sector_l1", "sector_l2", "sector_l3", "name", "symbol") if c in out.columns]
    out = out[cols].drop_duplicates(subset=["symbol"], keep="last").sort_values(sort_cols)
    out.to_csv(path, index=False)
    return path


def load_batch_registry(output_root: Path) -> pd.DataFrame:
    return load_output_registry(output_root)


def _has_equity_curve(path: Path) -> bool:
    return (path / _EQ_REL).exists()


def _symbol_from_dir(path: Path) -> str:
    sym_file = path / "symbol.txt"
    if sym_file.exists():
        return sym_file.read_text(encoding="utf-8").strip().zfill(6)
    if path.name.isdigit() and len(path.name) == 6:
        return path.name.zfill(6)
    return ""


def find_analysis_dir(output_root: Path, symbol: str) -> Path | None:
    """查找已分析标的目录（支持行业/公司名或旧版代码目录）。"""
    output_root = Path(output_root)
    if not output_root.exists():
        return None
    sym = str(symbol).zfill(6)

    registry = load_output_registry(output_root)
    if not registry.empty and "path" in registry.columns:
        rows = registry[registry["symbol"] == sym]
        if not rows.empty:
            rel = str(rows.iloc[0].get("path", "")).strip()
            if rel:
                candidate = output_root / rel
                if _has_equity_curve(candidate):
                    return candidate

    found = find_dir_by_symbol(output_root, sym, validator=_has_equity_curve)
    if found is not None:
        return found

    for child in sorted(output_root.iterdir()):
        if not child.is_dir() or child.name in _RESERVED or child.name.isdigit():
            continue
        if child.name.endswith(".csv") or child.name.endswith(".parquet"):
            continue
        direct = child / sym
        if _has_equity_curve(direct):
            return direct
        for sub in child.iterdir():
            if sub.is_dir() and _has_equity_curve(sub) and _symbol_from_dir(sub) == sym:
                return sub
            if sub.is_dir():
                for leaf in sub.iterdir():
                    if leaf.is_dir() and _has_equity_curve(leaf) and _symbol_from_dir(leaf) == sym:
                        return leaf

    flat = output_root / sym
    if _has_equity_curve(flat):
        return flat
    return None


def resolve_symbol_dir(output_root: Path, symbol: str) -> Path | None:
    sym = str(symbol).zfill(6)
    found = find_analysis_dir(output_root, sym)
    if found is not None:
        return found

    registry = load_output_registry(output_root)
    if not registry.empty:
        rows = registry[registry["symbol"] == sym]
        if not rows.empty:
            if "path" in rows.columns:
                rel = str(rows.iloc[0].get("path", "")).strip()
                if rel:
                    candidate = output_root / rel
                    if candidate.is_dir():
                        return candidate
            sector = str(rows.iloc[0].get("sector", "")).strip()
            if sector:
                candidate = output_root / sector / sym
                if candidate.exists():
                    return candidate

    flat = output_root / sym
    if flat.exists():
        return flat

    for child in sorted(output_root.iterdir()):
        if child.is_dir() and not child.name.isdigit() and child.name not in _RESERVED:
            candidate = child / sym
            if candidate.exists():
                return candidate
    return None


def lookup_symbol_meta(output_root: Path, symbol: str) -> dict[str, str]:
    sym = str(symbol).zfill(6)
    registry = load_output_registry(output_root)
    if registry.empty:
        return {}
    rows = registry[registry["symbol"] == sym]
    if rows.empty:
        return {}
    row = rows.iloc[0]
    return {
        "symbol": sym,
        "sector": str(row.get("sector", "")).strip(),
        "name": str(row.get("name", "")).strip(),
        "path": str(row.get("path", "")).strip(),
    }


def resolve_symbol_output_dir(
    output_root: Path,
    symbol: str,
    *,
    data_root: Path | None = None,
    sector: str = "",
    sector_l2: str = "",
    name: str = "",
    group_by_sector: bool = True,
    group_by_l2: bool = True,
    name_as_folder: bool = True,
    create: bool = False,
) -> Path:
    """优先复用 stocks_index / 已有分析目录，避免重复建文件夹。"""
    from market_platform.agents.核心.工具.读写 import ensure_dir

    output_root = Path(output_root)
    sym = str(symbol).zfill(6)
    dr = data_root or data_dir()

    meta = lookup_symbol_meta(output_root, sym)
    rel = meta.get("path", "")
    if rel:
        candidate = output_root / rel
        if candidate.is_dir() and _has_equity_curve(candidate):
            return ensure_dir(candidate) if create else candidate

    existing = find_analysis_dir(output_root, sym)
    if existing is not None:
        return ensure_dir(existing) if create else existing

    if rel:
        candidate = output_root / rel
        if candidate.is_dir():
            return ensure_dir(candidate) if create else candidate

    hmeta = lookup_symbol_hierarchy(dr, sym)
    path = symbol_output_dir(
        output_root,
        sym,
        sector=sector or hmeta.get("sector_l1", "") or meta.get("sector", ""),
        sector_l2=hmeta.get("sector_l2", ""),
        sector_l3=hmeta.get("sector_l3", ""),
        name=name or hmeta.get("name", "") or meta.get("name", ""),
        group_by_sector=group_by_sector,
        group_by_l2=group_by_l2,
        group_by_l3=False,
        name_as_folder=name_as_folder,
        data_root=dr,
    )
    return ensure_dir(path) if create else path


def list_symbol_entries(output_root: Path) -> list[dict[str, str]]:
    registry = load_output_registry(output_root)
    if not registry.empty:
        entries: list[dict[str, str]] = []
        output_root = Path(output_root)
        for _, row in registry.iterrows():
            sym = str(row["symbol"]).zfill(6)
            sector = str(row.get("sector", "")).strip()
            name = str(row.get("name", "")).strip()
            rel = str(row.get("path", "")).strip()
            sym_path: Path | None = None
            if rel:
                candidate = output_root / rel
                if _has_equity_curve(candidate):
                    sym_path = candidate
            if sym_path is None:
                sym_path = find_analysis_dir(output_root, sym)
            if sym_path is not None:
                entries.append(
                    {
                        "symbol": sym,
                        "sector": sector,
                        "name": name,
                        "path": str(sym_path),
                    }
                )
        return entries

    entries = []
    for child in sorted(output_root.iterdir()):
        if not child.is_dir() or child.name in _RESERVED:
            continue
        if child.name.isdigit():
            entries.append({"symbol": child.name, "sector": "", "name": "", "path": str(child)})
        elif not child.name.endswith(".csv"):
            for sym_dir in sorted(child.iterdir()):
                if sym_dir.is_dir():
                    sym = _symbol_from_dir(sym_dir)
                    entries.append(
                        {
                            "symbol": sym,
                            "sector": child.name,
                            "name": sym_dir.name,
                            "path": str(sym_dir),
                        }
                    )
    return entries
