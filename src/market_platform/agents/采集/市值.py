from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_EASTMONEY_LIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_PAGE_SIZE = 100
_CACHE_KEY = "circ_mv_map"
_DEFAULT_MAX_AGE_HOURS = 24


def _parse_mv(value: object) -> float | None:
    if value is None or value == "-" or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def _fetch_page(page: int, *, max_retries: int = 4) -> tuple[list[tuple[str, float]], int]:
    """拉取一页：symbol → 流通市值（元）。"""
    params = urllib.parse.urlencode(
        {
            "pn": page,
            "pz": _PAGE_SIZE,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f12",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            # f20 总市值, f21 流通市值
            "fields": "f12,f21",
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
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    payload = json.loads(resp.read().decode())
            except Exception as exc2:  # noqa: BLE001
                last_exc = exc2
                time.sleep(1.2 * (attempt + 1))
                continue

        data = payload.get("data") or {}
        total = int(data.get("total", 0) or 0)
        rows: list[tuple[str, float]] = []
        for item in data.get("diff") or []:
            sym = str(item.get("f12", "")).zfill(6)
            mv = _parse_mv(item.get("f21"))
            if sym.isdigit() and len(sym) == 6 and mv is not None:
                rows.append((sym, mv))
        return rows, total

    raise last_exc  # type: ignore[misc]


def fetch_circ_mv_map(*, sleep_seconds: float = 0.15) -> dict[str, float]:
    """从东方财富拉取 A 股流通市值（元）。"""
    out: dict[str, float] = {}
    total: int | None = None
    page = 1
    while True:
        rows, total_count = _fetch_page(page)
        if total is None:
            total = total_count
        if not rows:
            break
        out.update(rows)
        logger.info("Circ MV page %d: +%d (have %d/%d)", page, len(rows), len(out), total or 0)
        if len(out) >= (total or 0) or page * _PAGE_SIZE >= (total or 0) + _PAGE_SIZE:
            break
        page += 1
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    return out


def _cache_age_hours(payload: dict[str, Any]) -> float | None:
    raw = payload.get("fetched_at")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return max(0.0, (now - ts.astimezone(timezone.utc)).total_seconds() / 3600.0)


def _caps_from_payload(payload: dict[str, Any] | None) -> dict[str, float]:
    if not isinstance(payload, dict):
        return {}
    caps = payload.get("caps") or {}
    if not isinstance(caps, dict):
        return {}
    return {str(k).zfill(6): float(v) for k, v in caps.items() if _parse_mv(v) is not None}


def load_circ_mv_map(
    *,
    max_age_hours: float = _DEFAULT_MAX_AGE_HOURS,
    refresh: bool = False,
    allow_stale: bool = True,
) -> dict[str, float]:
    """读取流通市值映射。

    网页聚合默认 allow_stale=True：有缓存就先用，避免东方财富超时把接口拖到几十秒。
    需要强刷时传 refresh=True。
    """
    from market_platform.agents.存储 import data_access as store

    cached = store.load_app_state(_CACHE_KEY)
    cached_caps = _caps_from_payload(cached if isinstance(cached, dict) else None)
    age = _cache_age_hours(cached) if isinstance(cached, dict) else None
    fresh_enough = cached_caps and (age is None or age <= max_age_hours)

    if cached_caps and not refresh:
        if fresh_enough:
            return cached_caps
        if allow_stale:
            logger.info(
                "Using stale circ MV cache (age=%.1fh > %.1fh); skip blocking refresh",
                age if age is not None else -1.0,
                max_age_hours,
            )
            return cached_caps

    try:
        caps = fetch_circ_mv_map()
    except Exception:
        logger.warning("Fetch circ MV failed; fallback to cache if any", exc_info=True)
        return cached_caps

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "as_of": datetime.now().date().isoformat(),
        "n": len(caps),
        "caps": caps,
    }
    try:
        store.save_app_state(_CACHE_KEY, payload)
    except Exception:
        logger.warning("Persist circ MV cache failed", exc_info=True)
    return caps
