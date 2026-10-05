"""模拟实盘 API 服务"""

from __future__ import annotations

from typing import Any, Dict, Optional

from market_platform.predictor.分析.模拟盘.引擎 import PaperTradingEngine
from market_platform.predictor.配置 import get_config


def _resolve_provider(provider: str | None = None) -> str:
    if provider:
        return provider
    return get_config().get("model", {}).get("llm", {}).get("active_api") or "deepseek"


def get_paper_trading_status(provider: str | None = None) -> Dict[str, Any]:
    prov = _resolve_provider(provider)
    engine = PaperTradingEngine(provider=prov)
    state = engine.load_state()
    cfg = get_config().get("paper_trading", {})
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "llm_provider": prov,
        "config": {
            "initial_capital": engine.initial_capital,
            "auto_run": bool(cfg.get("auto_run", True)),
            "schedule_time": cfg.get("schedule_time", "16:30"),
            "refresh_data": bool(cfg.get("refresh_data", True)),
        },
        "status": engine.build_status(state),
        "performance": engine.build_performance(state),
        "cash_detail": engine.build_cash_detail(state),
        "charts": engine.build_charts(state),
    }


def get_paper_trading_history(
    limit: Optional[int] = 100,
    provider: str | None = None,
) -> Dict[str, Any]:
    prov = _resolve_provider(provider)
    engine = PaperTradingEngine(provider=prov)
    state = engine.load_state()
    history = state.get("history") or []
    if limit and limit > 0:
        history = history[-limit:]
    return {
        "llm_provider": prov,
        "total": len(state.get("history") or []),
        "records": list(reversed(history)),
    }


def run_paper_trading_cycle(
    *,
    refresh_data: bool | None = None,
    force: bool = False,
    provider: str | None = None,
) -> Dict[str, Any]:
    cfg = get_config().get("paper_trading", {})
    if not cfg.get("enabled", True):
        return {"status": "disabled", "message": "模拟实盘未启用"}
    prov = _resolve_provider(provider)
    engine = PaperTradingEngine(provider=prov)
    result = engine.run_cycle(refresh_data=refresh_data, force=force)
    if isinstance(result, dict):
        result["llm_provider"] = prov
    return result


def reset_paper_trading(provider: str | None = None) -> Dict[str, Any]:
    prov = _resolve_provider(provider)
    engine = PaperTradingEngine(provider=prov)
    state = engine.reset_state()
    return {
        "status": "ok",
        "llm_provider": prov,
        "status_snapshot": engine.build_status(state),
    }


def set_paper_auto_trade(enabled: bool, provider: str | None = None) -> Dict[str, Any]:
    prov = _resolve_provider(provider)
    engine = PaperTradingEngine(provider=prov)
    return {
        "status": "ok",
        "llm_provider": prov,
        "status_snapshot": engine.set_auto_trade(enabled),
    }
