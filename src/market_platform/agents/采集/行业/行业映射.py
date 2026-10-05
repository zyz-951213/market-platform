from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_SECTOR_MAP_NAME = "sector_map.csv"
_EASTMONEY_LIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_PAGE_SIZE = 100

# 证监会行业代码前缀 -> 申万一级（近似）
_CSRC_PREFIX_SECTOR: dict[str, str] = {
    "A01": "农林牧渔",
    "A02": "农林牧渔",
    "A03": "农林牧渔",
    "A04": "农林牧渔",
    "A05": "农林牧渔",
    "B06": "煤炭",
    "B07": "石油石化",
    "B08": "钢铁",
    "B09": "有色金属",
    "B10": "有色金属",
    "B11": "采掘",
    "B12": "采掘",
    "C13": "食品饮料",
    "C14": "食品饮料",
    "C15": "食品饮料",
    "C16": "食品饮料",
    "C17": "纺织服饰",
    "C18": "纺织服饰",
    "C19": "纺织服饰",
    "C20": "轻工制造",
    "C21": "轻工制造",
    "C22": "轻工制造",
    "C23": "传媒",
    "C24": "轻工制造",
    "C25": "石油石化",
    "C26": "基础化工",
    "C27": "医药生物",
    "C28": "基础化工",
    "C29": "基础化工",
    "C30": "建筑材料",
    "C31": "钢铁",
    "C32": "有色金属",
    "C33": "机械设备",
    "C34": "机械设备",
    "C35": "机械设备",
    "C36": "汽车",
    "C37": "国防军工",
    "C38": "电力设备",
    "C39": "电子",
    "C40": "机械设备",
    "C41": "公用事业",
    "C42": "环保",
    "C43": "环保",
    "D44": "公用事业",
    "D45": "公用事业",
    "D46": "公用事业",
    "E47": "建筑装饰",
    "E48": "建筑装饰",
    "E49": "建筑装饰",
    "E50": "建筑装饰",
    "F51": "商贸零售",
    "F52": "商贸零售",
    "G53": "交通运输",
    "G54": "交通运输",
    "G55": "交通运输",
    "G56": "交通运输",
    "G57": "交通运输",
    "G58": "交通运输",
    "G59": "交通运输",
    "H61": "社会服务",
    "H62": "社会服务",
    "I63": "通信",
    "I64": "传媒",
    "I65": "计算机",
    "J66": "银行",
    "J67": "非银金融",
    "J68": "非银金融",
    "J69": "非银金融",
    "K70": "房地产",
    "L71": "社会服务",
    "L72": "社会服务",
    "M73": "社会服务",
    "M74": "社会服务",
    "M75": "社会服务",
    "N76": "环保",
    "N77": "环保",
    "N78": "环保",
    "O79": "社会服务",
    "O80": "社会服务",
    "O81": "社会服务",
    "P82": "社会服务",
    "Q83": "社会服务",
    "Q84": "医药生物",
    "R85": "传媒",
    "R86": "社会服务",
    "R87": "传媒",
    "R88": "传媒",
    "S90": "综合",
}


def map_csrc_industry(csrc_industry: str) -> str:
    """将证监会行业分类映射为申万一级行业名。"""
    text = str(csrc_industry or "").strip()
    if not text:
        return "未分类"
    prefix = text[:3]
    if prefix in _CSRC_PREFIX_SECTOR:
        return _CSRC_PREFIX_SECTOR[prefix]
    letter = text[0] if text else ""
    fallback = {
        "A": "农林牧渔",
        "B": "采掘",
        "C": "机械设备",
        "D": "公用事业",
        "E": "建筑装饰",
        "F": "商贸零售",
        "G": "交通运输",
        "H": "社会服务",
        "I": "计算机",
        "J": "非银金融",
        "K": "房地产",
        "L": "社会服务",
        "M": "社会服务",
        "N": "环保",
        "O": "社会服务",
        "P": "社会服务",
        "Q": "医药生物",
        "R": "传媒",
        "S": "综合",
    }
    return fallback.get(letter, "未分类")


def _is_uncategorized(sector: str) -> bool:
    return not str(sector or "").strip() or str(sector).strip() == "未分类"


def merge_sector_maps(*frames: pd.DataFrame) -> pd.DataFrame:
    """合并多份行业映射，优先保留非「未分类」记录。"""
    merged: dict[str, dict] = {}
    for frame in frames:
        if frame is None or frame.empty:
            continue
        for _, row in frame.iterrows():
            sym = str(row["symbol"]).zfill(6)
            sector = str(row.get("sector", "")).strip()
            name = str(row.get("name", "")).strip()
            prev = merged.get(sym)
            if prev is None:
                merged[sym] = {"symbol": sym, "name": name, "sector": sector or "未分类"}
                continue
            if _is_uncategorized(prev["sector"]) and not _is_uncategorized(sector):
                prev["sector"] = sector
            if name and (not prev["name"] or prev["name"] == sym):
                prev["name"] = name
    if not merged:
        return pd.DataFrame(columns=["symbol", "name", "sector"])
    out = pd.DataFrame(merged.values())
    out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    return out.drop_duplicates("symbol").sort_values("symbol").reset_index(drop=True)


def _fetch_eastmoney_sector_page(page: int, *, max_retries: int = 5) -> tuple[list[dict], int]:
    params = urllib.parse.urlencode(
        {
            "pn": page,
            "pz": _PAGE_SIZE,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f12,f14,f100",
        }
    )
    url = f"{_EASTMONEY_LIST_URL}?{params}"
    last_exc: Exception | None = None

    for attempt in range(max_retries):
        try:
            proc = subprocess.run(
                ["curl", "-sS", "--max-time", "30", "-H", "User-Agent: Mozilla/5.0", url],
                capture_output=True,
                text=True,
                check=True,
            )
            payload = json.loads(proc.stdout)
            data = payload.get("data") or {}
            total = int(data.get("total", 0))
            items = data.get("diff") or []
            rows = []
            for item in items:
                sym = str(item.get("f12", "")).zfill(6)
                name = str(item.get("f14", "")).strip()
                sector = str(item.get("f100", "")).strip()
                if sym.isdigit() and len(sym) == 6 and sector:
                    rows.append({"symbol": sym, "name": name, "sector": sector})
            return rows, total
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    payload = json.loads(resp.read().decode())
                data = payload.get("data") or {}
                total = int(data.get("total", 0))
                items = data.get("diff") or []
                rows = []
                for item in items:
                    sym = str(item.get("f12", "")).zfill(6)
                    name = str(item.get("f14", "")).strip()
                    sector = str(item.get("f100", "")).strip()
                    if sym.isdigit() and len(sym) == 6 and sector:
                        rows.append({"symbol": sym, "name": name, "sector": sector})
                return rows, total
            except Exception as exc2:  # noqa: BLE001
                last_exc = exc2
            time.sleep(1.5 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def fetch_sector_map(*, sleep_seconds: float = 0.35) -> pd.DataFrame:
    """从东方财富批量拉取 A 股行业分类（同花顺行业名）。"""
    all_rows: list[dict] = []
    total = None
    page = 1
    while True:
        rows, total_count = _fetch_eastmoney_sector_page(page)
        if total is None:
            total = total_count
        if not rows:
            break
        all_rows.extend(rows)
        logger.info("Sector map page %d: +%d (total %d/%d)", page, len(rows), len(all_rows), total or 0)
        if len(all_rows) >= (total or 0):
            break
        page += 1
        time.sleep(sleep_seconds)

    df = pd.DataFrame(all_rows)
    if df.empty:
        return df
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    df = df.drop_duplicates(subset=["symbol"], keep="first")
    return df.sort_values("symbol").reset_index(drop=True)


def fetch_sector_map_baostock() -> pd.DataFrame:
    """从 baostock 拉取证监会行业并映射为申万一级。"""
    import baostock as bs

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"baostock login failed: {login.error_msg}")

    rows: list[dict] = []
    try:
        rs = bs.query_stock_industry()
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            if len(row) < 4:
                continue
            code, name, csrc = row[1], row[2], row[3]
            sym = str(code).split(".")[-1].zfill(6)
            if not sym.isdigit() or len(sym) != 6:
                continue
            rows.append(
                {
                    "symbol": sym,
                    "name": str(name).strip(),
                    "sector": map_csrc_industry(csrc),
                }
            )
    finally:
        bs.logout()

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    return df.drop_duplicates("symbol").sort_values("symbol").reset_index(drop=True)


def _seed_from_outputs(df: pd.DataFrame, output_root: Path) -> pd.DataFrame:
    """合并行业验证 batch 中已有的 sector 标注。"""
    for name in ("stocks_index.csv", "batch_summary.csv"):
        path = output_root / name
        if not path.exists():
            continue
        extra = pd.read_csv(path, dtype={"symbol": str})
        if "sector" not in extra.columns:
            continue
        extra["symbol"] = extra["symbol"].astype(str).str.zfill(6)
        extra = extra[extra["sector"].astype(str).str.strip() != ""]
        extra = extra[~extra["sector"].astype(str).str.strip().eq("未分类")]
        if extra.empty:
            continue
        cols = [c for c in ("symbol", "name", "sector") if c in extra.columns]
        extra = extra[cols].drop_duplicates("symbol")
        df = merge_sector_maps(df, extra)
    return df


def save_sector_map(df: pd.DataFrame, data_root: Path) -> Path:
    data_root = Path(data_root)
    data_root.mkdir(parents=True, exist_ok=True)
    path = data_root / _SECTOR_MAP_NAME
    out = df.copy()
    out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    out = out.drop_duplicates(subset=["symbol"], keep="first").sort_values("symbol")
    out.to_csv(path, index=False)
    logger.info("Sector map saved -> %s (%d symbols)", path, len(out))
    return path


def load_sector_map(data_root: Path) -> pd.DataFrame:
    path = Path(data_root) / _SECTOR_MAP_NAME
    if not path.exists():
        return pd.DataFrame(columns=["symbol", "name", "sector"])
    df = pd.read_csv(path, dtype={"symbol": str})
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    return df


def build_sector_map(
    data_root: Path,
    output_root: Path | None = None,
    *,
    prefer_baostock: bool = False,
) -> pd.DataFrame:
    """构建完整行业映射：东财优先，失败则回退 baostock。"""
    existing = load_sector_map(data_root)
    frames: list[pd.DataFrame] = []

    if prefer_baostock:
        logger.info("Fetching sector map from baostock...")
        frames.append(fetch_sector_map_baostock())
    else:
        try:
            logger.info("Fetching sector map from eastmoney...")
            em = fetch_sector_map()
            if not em.empty:
                frames.append(em)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Eastmoney sector fetch failed: %s", exc)

    if not frames or (len(frames) == 1 and frames[0].empty):
        logger.info("Falling back to baostock sector map...")
        frames = [fetch_sector_map_baostock()]

    if output_root is not None:
        frames.append(_seed_from_outputs(pd.DataFrame(), Path(output_root)))

    df = merge_sector_maps(existing, *frames)
    return df


def prepare_sector_map(
    data_root: Path,
    output_root: Path | None = None,
    *,
    refresh: bool = False,
    prefer_baostock: bool = False,
) -> pd.DataFrame:
    data_root = Path(data_root)
    path = data_root / _SECTOR_MAP_NAME
    if path.exists() and not refresh:
        return load_sector_map(data_root)

    df = build_sector_map(data_root, output_root, prefer_baostock=prefer_baostock)
    save_sector_map(df, data_root)
    return load_sector_map(data_root)


def lookup_sector(data_root: Path, symbol: str, *, name: str = "") -> str:
    sym = str(symbol).zfill(6)
    df = load_sector_map(data_root)
    if df.empty:
        return "未分类"
    hit = df[df["symbol"] == sym]
    if hit.empty:
        return "未分类"
    sector = str(hit.iloc[0].get("sector", "")).strip()
    return sector or "未分类"


def enrich_universe_with_sector(data_root: Path) -> pd.DataFrame:
    """将 sector 合并进 universe.csv（不重复行）。"""
    from market_platform.agents.采集.股票池 import load_universe_csv, save_universe

    universe_path = Path(data_root) / "universe.csv"
    if not universe_path.exists():
        return pd.DataFrame()
    universe = load_universe_csv(universe_path)
    sectors = load_sector_map(data_root)
    if sectors.empty:
        return universe

    merged = universe.merge(sectors[["symbol", "sector"]], on="symbol", how="left")
    merged["sector"] = merged["sector"].fillna("未分类")
    save_universe(merged, universe_path)
    return merged
