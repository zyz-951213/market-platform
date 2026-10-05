"""常用 A 股指数日线（腾讯接口）。"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

PAPER_BENCHMARKS: list[dict[str, str]] = [
    {"id": "sse", "name": "上证指数", "code": "sh000001"},
    {"id": "szse", "name": "深证成指", "code": "sz399001"},
    {"id": "chinext", "name": "创业板指", "code": "sz399006"},
    {"id": "hs300", "name": "沪深300", "code": "sh000300"},
    {"id": "sz50", "name": "上证50", "code": "sh000016"},
    {"id": "csi500", "name": "中证500", "code": "sh000905"},
    {"id": "csi1000", "name": "中证1000", "code": "sh000852"},
    {"id": "csi2000", "name": "中证2000", "code": "sh932000"},
    {"id": "kc50", "name": "科创50", "code": "sh000688"},
]


def fetch_index_closes_tencent(code: str, start: str, end: str) -> dict[str, float]:
    """拉取指数日收盘价，日期格式 YYYY-MM-DD。"""
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={code},day,{start},{end},640,qfq"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    block = data.get("data") or {}
    key = next(iter(block), None)
    if not key:
        return {}
    days = (block.get(key) or {}).get("day") or []
    out: dict[str, float] = {}
    for row in days:
        if len(row) < 3:
            continue
        out[str(row[0])] = float(row[2])
    return out


def _pct_series(values: list[float | None]) -> list[float | None]:
    base = next((v for v in values if v is not None and v > 0), None)
    if base is None:
        return [None] * len(values)
    return [((v / base - 1.0) * 100.0) if v is not None else None for v in values]


def _align_closes(dates: list[str], close_map: dict[str, float]) -> list[float | None]:
    out: list[float | None] = []
    last: float | None = None
    for d in dates:
        if d in close_map:
            last = close_map[d]
        out.append(last)
    return out


def build_paper_nav_compare(state: dict[str, Any]) -> dict[str, Any]:
    """将模拟盘净值与常用指数对齐，输出累计收益率（%）序列。"""
    curve = state.get("nav_curve") or []
    if not curve:
        return {"dates": [], "series": [], "returns": {}}

    dates = [str(c["date"]) for c in curve]
    start, end = dates[0], dates[-1]
    navs = [float(c["nav"]) for c in curve]
    paper_pct = _pct_series(navs)
    paper_ret = paper_pct[-1] if paper_pct else None

    series: list[dict[str, Any]] = [
        {"id": "paper", "name": "模拟盘", "values": paper_pct, "return": paper_ret},
    ]
    returns: dict[str, float | None] = {"paper": paper_ret}

    for bm in PAPER_BENCHMARKS:
        try:
            closes = fetch_index_closes_tencent(bm["code"], start, end)
        except Exception as exc:
            logger.warning("Index fetch failed %s: %s", bm["code"], exc)
            continue
        if not closes:
            logger.debug("Index empty %s (%s)", bm["name"], bm["code"])
            continue
        aligned = _align_closes(dates, closes)
        # 种子日可能落在假日：允许首段为 None，由 _pct_series 用首个有效点归零
        if all(v is None for v in aligned):
            continue
        pct = _pct_series(aligned)
        if all(v is None for v in pct):
            continue
        ret = pct[-1]
        series.append({"id": bm["id"], "name": bm["name"], "values": pct, "return": ret})
        returns[bm["id"]] = ret

    return {
        "dates": dates,
        "series": series,
        "returns": returns,
        "start_date": start,
        "end_date": end,
    }
