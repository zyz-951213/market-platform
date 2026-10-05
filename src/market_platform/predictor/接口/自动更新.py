"""Auto-update API service."""

from __future__ import annotations

from typing import Any, Dict

from market_platform.predictor.分析.自动更新 import get_auto_update_status, run_daily_update


def trigger_auto_update(*, force: bool = False) -> Dict[str, Any]:
    return run_daily_update(force=force)


def fetch_auto_update_status() -> Dict[str, Any]:
    return get_auto_update_status()
