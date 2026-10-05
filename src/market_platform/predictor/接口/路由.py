"""FastAPI 路由（只读 API；写入走 CLI / systemd）"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from market_platform.predictor.接口.回测存储 import (
    compare_backtest_runs,
    list_backtest_channel_summaries,
    list_backtest_runs,
)
from market_platform.predictor.接口.市场分析 import (
    build_prompt_preview,
    get_backtest_charts,
    get_llm_channel_catalog,
    get_overview,
    get_price_series,
)
from market_platform.predictor.接口.准确率分区 import (
    compute_accuracy_partitions,
    detect_current_regime,
    compute_regime_weights,
)
from market_platform.predictor.接口.归因 import compute_attribution
from market_platform.predictor.接口.模拟盘 import (
    get_paper_trading_history,
    get_paper_trading_status,
)
from market_platform.predictor.接口.自动更新 import fetch_auto_update_status

router = APIRouter(prefix="/api")


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/overview")
def overview():
    try:
        return get_overview()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/price-series")
def price_series(
    limit: Optional[int] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
):
    return get_price_series(limit=limit, start=start, end=end)


@router.get("/backtest-charts")
def backtest_charts(provider: Optional[str] = None):
    try:
        return get_backtest_charts(provider=provider)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/backtest-runs")
def backtest_runs(limit: int = 20):
    return {"runs": list_backtest_runs(limit=limit)}


@router.get("/backtest-channel-summaries")
def backtest_channel_summaries():
    """各渠道最近回测指标汇总（对齐对比用，不含曲线）"""
    return list_backtest_channel_summaries()


@router.get("/accuracy-partitions")
def accuracy_partitions(force: bool = False):
    """各模型按市场状态切片的准确率热力与优势区间。"""
    try:
        data = compute_accuracy_partitions(force=force)
        data["current_regime"] = detect_current_regime()
        data["routing"] = compute_regime_weights()
        return data
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/backtest-compare")
def backtest_compare(a: str, b: str):
    result = compare_backtest_runs(a, b)
    if not result.get("available"):
        raise HTTPException(status_code=404, detail="回测记录不存在")
    return result


@router.get("/prompt-preview")
def prompt_preview(date: Optional[str] = None):
    try:
        return build_prompt_preview(date=date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/llm-providers")
def llm_providers():
    """列出可用 LLM 渠道（供前端切换展示）"""
    return get_llm_channel_catalog()


@router.get("/attribution")
def attribution(provider: Optional[str] = None):
    """策略归因分析（可按 LLM 渠道切换已入库回测）"""
    try:
        return compute_attribution(provider=provider)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/paper-trading/status")
def paper_trading_status(provider: Optional[str] = None):
    return get_paper_trading_status(provider=provider)


@router.get("/paper-trading/history")
def paper_trading_history(limit: int = 100, provider: Optional[str] = None):
    return get_paper_trading_history(limit=limit, provider=provider)


@router.get("/auto-update/status")
def auto_update_status():
    return fetch_auto_update_status()
