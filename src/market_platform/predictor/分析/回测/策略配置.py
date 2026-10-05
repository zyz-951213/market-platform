"""策略配置解析：全局 strategy + 按渠道 by_provider 覆盖。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

from market_platform.predictor.配置 import get_config


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(base)
    for key, val in (override or {}).items():
        if key == "by_provider":
            continue
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = deepcopy(val)
    return out


def resolve_strategy_for_provider(provider: Optional[str] = None) -> Dict[str, Any]:
    """合并 backtest.strategy 与 by_provider.<provider>。"""
    root = get_config().get("backtest", {}).get("strategy", {}) or {}
    base = {k: deepcopy(v) for k, v in root.items() if k != "by_provider"}
    if not provider:
        return base
    overrides = (root.get("by_provider") or {}).get(provider) or {}
    if not overrides:
        return base
    return _deep_merge(base, overrides)
