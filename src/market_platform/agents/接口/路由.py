from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from market_platform.agents.接口 import holdings, portfolio, web_data
from market_platform.agents.接口 import 模拟盘 as paper
from market_platform.agents.核心.任务.调度器 import get_auto_update_scheduler
from market_platform.agents.核心.路径.项目 import data_dir, output_dir
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api")

_DEFAULT_OUTPUT = output_dir()
_DEFAULT_DATA = data_dir()


@router.get("/health")
def health():
    return web_data.get_health()


@router.get("/overview")
def overview(
    output_root: Optional[str] = Query(default=None),
    data_root: Optional[str] = Query(default=None),
):
    return web_data.get_overview(
        Path(output_root) if output_root else None,
        Path(data_root) if data_root else None,
    )


@router.get("/sector-tree")
def sector_tree(
    output_root: Optional[str] = Query(default=None),
    data_root: Optional[str] = Query(default=None),
):
    return web_data.get_sector_tree(
        Path(output_root) if output_root else None,
        Path(data_root) if data_root else None,
    )


@router.get("/classification")
def classification(data_root: Optional[str] = Query(default=None)):
    return web_data.get_classification(Path(data_root) if data_root else None)


@router.get("/symbols")
def symbols(
    output_root: Optional[str] = Query(default=None),
    data_root: Optional[str] = Query(default=None),
    q: str = Query(default=""),
    sector: str = Query(default=""),
    sector_l1: str = Query(default=""),
    sector_l2: str = Query(default=""),
    sector_l3: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    all_symbols: bool = Query(default=False),
    include_data_only: bool = Query(default=False),
):
    return {
        "symbols": web_data.list_symbols(
            Path(output_root) if output_root else None,
            Path(data_root) if data_root else None,
            include_data_only=include_data_only,
            q=q,
            sector=sector,
            sector_l1=sector_l1,
            sector_l2=sector_l2,
            sector_l3=sector_l3,
            limit=limit,
            all_symbols=all_symbols,
        )
    }


@router.get("/daily-signals")
def daily_signals(
    output_root: Optional[str] = Query(default=None),
    data_root: Optional[str] = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
    sector_l1: str = Query(default=""),
    sector_l2: str = Query(default=""),
    sector_l3: str = Query(default=""),
    q: str = Query(default=""),
    include_hold: bool = Query(default=False),
    portfolio_id: str = Query(default=""),
):
    return web_data.get_daily_signals(
        Path(output_root) if output_root else None,
        Path(data_root) if data_root else None,
        limit=limit,
        sector_l1=sector_l1,
        sector_l2=sector_l2,
        sector_l3=sector_l3,
        q=q,
        include_hold=include_hold,
        portfolio_id=portfolio_id,
    )


@router.get("/batch-summary")
def batch_summary(
    output_root: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    sector: str = Query(default=""),
    sector_l1: str = Query(default=""),
    sector_l2: str = Query(default=""),
    sector_l3: str = Query(default=""),
    q: str = Query(default=""),
    sort_by: str = Query(default="symbol"),
    sort_order: Literal["asc", "desc"] = Query(default="asc"),
    all_rows: bool = Query(default=False),
    slim: bool = Query(default=True),
):
    return web_data.get_batch_summary(
        Path(output_root) if output_root else None,
        limit=limit,
        offset=offset,
        sector=sector,
        sector_l1=sector_l1,
        sector_l2=sector_l2,
        sector_l3=sector_l3,
        q=q,
        sort_by=sort_by,
        sort_order=sort_order,
        all_rows=all_rows,
        slim=slim,
    )


@router.get("/industry-summary")
def industry_summary(
    output_root: Optional[str] = Query(default=None),
    year: Optional[str] = Query(default=None, description="日历年，如 2025；all=全样本；缺省=最近有数据年份"),
):
    try:
        return web_data.get_industry_summary(
            Path(output_root) if output_root else None,
            year=year,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/symbol/{symbol}")
def symbol_summary(symbol: str, output_root: Optional[str] = Query(default=None)):
    try:
        return web_data.get_symbol_summary(symbol, Path(output_root) if output_root else None)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/symbol/{symbol}/equity")
def symbol_equity(symbol: str, output_root: Optional[str] = Query(default=None)):
    try:
        return web_data.get_equity_chart(symbol, Path(output_root) if output_root else None)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/symbol/{symbol}/states")
def symbol_states(symbol: str, output_root: Optional[str] = Query(default=None)):
    try:
        return web_data.get_states_chart(symbol, Path(output_root) if output_root else None)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/symbol/{symbol}/agents")
def symbol_agents(symbol: str, output_root: Optional[str] = Query(default=None)):
    try:
        return web_data.get_agents_chart(symbol, Path(output_root) if output_root else None)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/symbol/{symbol}/state-probs")
def symbol_state_probs(symbol: str, output_root: Optional[str] = Query(default=None)):
    try:
        return web_data.get_state_probs(symbol, Path(output_root) if output_root else None)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/symbol/{symbol}/agent-labels")
def symbol_agent_labels(symbol: str, output_root: Optional[str] = Query(default=None)):
    try:
        return web_data.get_agent_labels(symbol, Path(output_root) if output_root else None)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class PortfolioBuildRequest(BaseModel):
    sectors: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    picks_per_sector: int = Field(default=2, ge=1, le=20)
    weight_mode: Literal["equal", "sharpe_weighted", "risk_parity", "heat_weighted"] = "equal"
    sort_by: Literal["score", "excess_return", "strategy_sharpe", "heat"] = "score"
    name: str = ""


@router.get("/update/status")
def update_status_route(
    output_root: Optional[str] = Query(default=None),
    data_root: Optional[str] = Query(default=None),
    rescan: bool = Query(default=False),
):
    return web_data.get_update_status(
        Path(output_root) if output_root else None,
        Path(data_root) if data_root else None,
        rescan=rescan,
    )


@router.post("/update/run")
def update_run_route():
    started = get_auto_update_scheduler().run_once_async()
    if not started:
        return {"started": False, "message": "更新任务正在进行中"}
    return {"started": True, "message": "已触发后台更新"}


@router.get("/kline")
def kline_batch(
    symbols: str = Query(..., description="逗号分隔股票代码"),
    limit: int = Query(default=90, ge=0, le=8000, description="K线条数；0=上市至今全历史"),
    data_root: Optional[str] = Query(default=None),
):
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        raise HTTPException(status_code=400, detail="请提供股票代码")
    if len(sym_list) > 60:
        raise HTTPException(status_code=400, detail="单次最多查询 60 只股票")
    return web_data.get_kline_batch(
        sym_list,
        limit=limit,
        data_root=Path(data_root) if data_root else None,
    )


@router.get("/sectors")
def sectors(
    output_root: Optional[str] = Query(default=None),
    sector_l1: str = Query(default=""),
    sector_l2: str = Query(default=""),
):
    return {
        "sectors": portfolio.list_sectors(
            Path(output_root) if output_root else None,
            sector_l1=sector_l1,
            sector_l2=sector_l2,
        )
    }


@router.get("/recommend")
def recommend(
    sectors: str = Query(default=""),
    symbols: str = Query(default=""),
    picks_per_sector: int = Query(default=2, ge=1, le=20),
    sort_by: Literal["score", "excess_return", "strategy_sharpe", "heat"] = Query(default="score"),
    output_root: Optional[str] = Query(default=None),
):
    sector_list = [s.strip() for s in sectors.split(",") if s.strip()]
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    root = Path(output_root) if output_root else None
    if sym_list:
        return {
            "recommendations": portfolio.recommend_stocks(
                [],
                picks_per_sector=picks_per_sector,
                sort_by=sort_by,
                symbols=sym_list,
                output_root=root,
            )
        }
    if not sector_list:
        raise HTTPException(status_code=400, detail="请至少选择一个行业或股票")
    return {
        "recommendations": portfolio.recommend_stocks(
            sector_list,
            picks_per_sector=picks_per_sector,
            sort_by=sort_by,
            output_root=root,
        )
    }


@router.post("/portfolio/build")
def portfolio_build(body: PortfolioBuildRequest, output_root: Optional[str] = Query(default=None)):
    try:
        result = portfolio.build_portfolio(
            sectors=body.sectors or None,
            symbols=body.symbols or None,
            picks_per_sector=body.picks_per_sector,
            weight_mode=body.weight_mode,
            sort_by=body.sort_by,
            name=body.name,
            output_root=Path(output_root) if output_root else None,
        )
        daily = portfolio.analyze_portfolio_daily(result.holdings, Path(output_root) if output_root else None)
        payload = result.to_dict()
        payload["daily_analysis"] = daily
        return payload
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/portfolio/list")
def portfolio_list(output_root: Optional[str] = Query(default=None)):
    return {"portfolios": portfolio.list_portfolios(Path(output_root) if output_root else None)}


@router.get("/portfolio/{portfolio_id}")
def portfolio_detail(portfolio_id: str, output_root: Optional[str] = Query(default=None)):
    try:
        return portfolio.load_portfolio(portfolio_id, Path(output_root) if output_root else None)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/portfolio/{portfolio_id}/equity")
def portfolio_equity(portfolio_id: str, output_root: Optional[str] = Query(default=None)):
    try:
        return portfolio.get_portfolio_equity_chart(portfolio_id, Path(output_root) if output_root else None)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/portfolio/{portfolio_id}/daily-analysis")
def portfolio_daily_analysis(portfolio_id: str, output_root: Optional[str] = Query(default=None)):
    try:
        meta = portfolio.load_portfolio(portfolio_id, Path(output_root) if output_root else None)
        return portfolio.analyze_portfolio_daily(meta.get("holdings", []), Path(output_root) if output_root else None)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class HoldingPosition(BaseModel):
    symbol: str
    name: str = ""
    weight: float = Field(ge=0, le=1)
    quantity: float | None = None
    cost: float | None = None


class HoldingsSaveRequest(BaseModel):
    target_source: Literal["signals", "portfolio"] = "signals"
    target_portfolio_id: str = ""
    cash_ratio: float = Field(default=0.0, ge=0, le=1)
    note: str = ""
    positions: list[HoldingPosition] = Field(default_factory=list)


@router.get("/holdings")
def get_holdings():
    doc = holdings.load_holdings_doc()
    report = holdings.analyze_rebalance(doc)
    return {"holdings": doc, "rebalance": report}


@router.put("/holdings")
def put_holdings(body: HoldingsSaveRequest):
    doc = holdings.save_holdings_doc(body.model_dump())
    report = holdings.analyze_rebalance(doc)
    return {"holdings": doc, "rebalance": report}


@router.get("/holdings/rebalance")
def get_holdings_rebalance(output_root: Optional[str] = Query(default=None)):
    return holdings.analyze_rebalance(output_root=Path(output_root) if output_root else None)


@router.post("/holdings/notify")
def post_holdings_notify(dry_run: bool = Query(default=False)):
    try:
        return holdings.send_rebalance_notification(dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/paper")
def get_paper_status():
    return paper.get_paper_status()


@router.get("/paper/tomorrow-picks")
def get_paper_tomorrow_picks():
    return paper.get_tomorrow_picks()


@router.get("/paper/nav-compare")
def get_paper_nav_compare():
    return paper.get_paper_nav_compare()


@router.post("/paper/run")
def post_paper_run(
    dry_run: bool = Query(default=False),
    notify: bool | None = Query(default=None),
):
    try:
        return paper.run_paper_cycle(dry_run=dry_run, notify=notify)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
