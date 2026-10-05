"""
结构化 JSON 日志 — 预测/回测关键节点事件与特征快照
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from market_platform.predictor.配置 import get_config
from market_platform.predictor.核心.路径.项目 import logs_dir

_EVENTS_DIR = logs_dir() / "events"
_structlog_logger = None


def _events_dir() -> Path:
    _EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    return _EVENTS_DIR


def _get_structlog():
    global _structlog_logger
    if _structlog_logger is not None:
        return _structlog_logger
    cfg = get_config().get("logging", {})
    if not cfg.get("structured", True):
        return None
    try:
        import structlog

        structlog.configure(
            processors=[
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.JSONRenderer(ensure_ascii=False),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(20),
        )
        _structlog_logger = structlog.get_logger("market_platform.predictor")
        return _structlog_logger
    except ImportError:
        return None


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "item"):
        try:
            return obj.item()
        except (TypeError, ValueError):
            pass
    return str(obj)


def emit_event(
    event: str,
    *,
    level: str = "info",
    payload: Optional[Dict[str, Any]] = None,
    feature_snapshot: Optional[Dict[str, Any]] = None,
) -> None:
    """写入 JSON 行事件日志（structlog 或 fallback 文件）"""
    cfg = get_config().get("logging", {})
    if not cfg.get("structured", True):
        return

    record: Dict[str, Any] = {
        "event": event,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if payload:
        record.update(payload)
    if feature_snapshot and cfg.get("feature_snapshot", True):
        record["feature_snapshot"] = feature_snapshot

    slog = _get_structlog()
    if slog is not None:
        getattr(slog, level, slog.info)(**record)
    else:
        line = json.dumps(record, ensure_ascii=False, default=_json_default)
        day = datetime.now().strftime("%Y-%m-%d")
        path = _events_dir() / f"events_{day}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    if cfg.get("mirror_to_loguru", True):
        msg = f"[{event}] {json.dumps(payload or {}, ensure_ascii=False, default=_json_default)[:500]}"
        getattr(logger, level, logger.info)(msg)


def build_feature_snapshot(
    row,
    *,
    prefix: Optional[str] = None,
    max_items: int = 24,
) -> Dict[str, Any]:
    """从单行数据提取数值特征快照（用于复盘）"""
    if row is None:
        return {}
    if hasattr(row, "to_dict"):
        data = row.to_dict()
    elif isinstance(row, dict):
        data = row
    else:
        return {}

    skip = {"date", "open", "high", "low", "close", "volume", "macro_news_summary"}
    out: Dict[str, Any] = {}
    for key, val in data.items():
        if key in skip or str(key).startswith("_"):
            continue
        if prefix and not str(key).startswith(prefix):
            continue
        if isinstance(val, (int, float)) and val == val:
            out[str(key)] = round(float(val), 6)
        if len(out) >= max_items:
            break
    if "date" in data:
        out["date"] = str(data["date"])
    return out
