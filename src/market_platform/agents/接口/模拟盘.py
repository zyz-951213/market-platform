"""模拟盘 API。"""

from __future__ import annotations

from datetime import date
from typing import Any

from market_platform.agents.settings import AppConfig
from market_platform.agents.核心.路径.项目 import profile_path


def get_paper_status(config: AppConfig | None = None) -> dict[str, Any]:
    from market_platform.agents.分析.模拟盘.引擎 import (
        build_paper_digest,
        enrich_paper_positions,
        enrich_paper_trades,
        load_paper_state,
    )

    config = config or AppConfig.from_yaml(profile_path("默认.yaml"))
    state = load_paper_state(config)
    enrich_paper_positions(state, config)
    pending = state.get("pending") or {}
    title, summary = build_paper_digest(
        state,
        picks=pending.get("holdings"),
        signal_date=None,
    )
    return {
        "config": {
            "enabled": config.paper_trading.enabled,
            "top_n": config.paper_trading.top_n,
            "notify_after_cycle": config.paper_trading.notify_after_cycle,
        },
        "state": state,
        "recent_trades": enrich_paper_trades(state),
        "summary": summary,
        "title": title,
    }


def get_tomorrow_picks(config: AppConfig | None = None) -> dict[str, Any]:
    from market_platform.agents.分析.模拟盘.引擎 import enrich_paper_positions, load_paper_state, select_top_picks

    config = config or AppConfig.from_yaml(profile_path("默认.yaml"))
    state = load_paper_state(config)
    enrich_paper_positions(state, config)
    pending = state.get("pending") or {}
    picks = pending.get("holdings") or select_top_picks(config)
    return {
        "signal_date": state.get("signal_date") or pending.get("signal_date"),
        "execute_date": pending.get("execute_date"),
        "executed": bool(pending.get("executed")),
        "picks": picks,
        "execution_note": "T 日收盘综合评分 → T+1 全换仓买入",
    }


def get_paper_nav_compare(config: AppConfig | None = None) -> dict[str, Any]:
    from market_platform.agents.分析.模拟盘.引擎 import load_paper_state
    from market_platform.agents.采集.指数 import build_paper_nav_compare

    config = config or AppConfig.from_yaml(profile_path("默认.yaml"))
    state = load_paper_state(config)
    return build_paper_nav_compare(state)


def run_paper_cycle(
    *,
    dry_run: bool = False,
    notify: bool | None = None,
    trade_date: date | None = None,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    from market_platform.agents.分析.模拟盘.引擎 import (
        build_paper_digest,
        enrich_paper_positions,
        enrich_paper_trades,
        run_paper_trading_cycle,
    )

    config = config or AppConfig.from_yaml(profile_path("默认.yaml"))
    result = run_paper_trading_cycle(config, trade_date=trade_date, dry_run=dry_run, notify=notify)
    if result.get("skipped"):
        return result

    state = result.get("state") or {}
    enrich_paper_positions(state, config)
    pending = state.get("pending") or {}
    picks = result.get("tomorrow_picks") or pending.get("holdings") or []
    title, summary = build_paper_digest(
        state,
        picks=picks,
        execution=result.get("execution"),
        signal_date=None,
    )
    result["state"] = state
    result["tomorrow_picks"] = picks
    result["recent_trades"] = enrich_paper_trades(state)
    result["summary"] = summary
    result["title"] = title
    result["config"] = {
        "enabled": config.paper_trading.enabled,
        "top_n": config.paper_trading.top_n,
        "notify_after_cycle": config.paper_trading.notify_after_cycle,
    }
    return result
