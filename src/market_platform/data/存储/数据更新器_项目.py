"""项目 schema 维护入口：包装兄弟项目的自动更新。

行为分析 ← market-agents
指数预测 ← market-predictor
隐因子   ← factor-latent（本地路径引导，editable 可能指向失效目录）
微盘/特处：研究档案，保持不可调度。
"""
from __future__ import annotations

from market_platform.core.配置 import 数据库DSN

import importlib.util
import sys
import types
from pathlib import Path

from market_platform.core.路径 import 仓库根

from .数据更新器 import refresh_watermark

import os

# 兄弟项目根(隐因子等)可用环境变量覆盖;默认取当前仓库的父目录
_PROJECTS = Path(os.environ.get("MARKET_PROJECTS_ROOT") or Path(仓库根()).parent)
_FACTOR_ROOT = _PROJECTS / "factor-latent"
_FACTOR_BACKEND = _FACTOR_ROOT / "后端"


def _refresh(schema: str, table: str, date_col: str, note: str = "") -> None:
    import psycopg

    dsn = 数据库DSN()
    with psycopg.connect(dsn, autocommit=True) as conn:
        try:
            refresh_watermark(
                conn,
                source=schema,
                dataset=table,
                schema=schema,
                table=table,
                date_col=date_col,
                note=note,
            )
        except Exception:
            pass


def _ensure_factor_latent() -> None:
    """将 my_projects/factor-latent/后端 挂为 factor_latent 包（兼容坏掉的 editable）。"""
    if "factor_latent" in sys.modules and getattr(
        sys.modules["factor_latent"], "__path__", None
    ):
        path = list(sys.modules["factor_latent"].__path__)
        if path and Path(path[0]).exists():
            return
    if not _FACTOR_BACKEND.is_dir():
        raise RuntimeError(f"找不到 factor-latent 后端: {_FACTOR_BACKEND}")
    pkg = types.ModuleType("factor_latent")
    pkg.__file__ = str(_FACTOR_BACKEND / "__init__.py")
    pkg.__path__ = [str(_FACTOR_BACKEND)]
    sys.modules["factor_latent"] = pkg
    init = _FACTOR_BACKEND / "__init__.py"
    if init.exists():
        spec = importlib.util.spec_from_file_location("factor_latent", init)
        if spec and spec.loader:
            spec.loader.exec_module(pkg)


def update_behavior_daily(updater) -> str:
    """行为分析.日行情：调用 market-agents 自动更新（限量）。"""
    from market_platform.agents.settings import AppConfig
    from market_platform.agents.核心.任务.运行器 import run_auto_update
    from market_platform.agents.核心.路径.项目 import profile_path

    cfg_path = profile_path("自动更新.yaml")
    config = AppConfig.from_yaml(cfg_path)
    result = run_auto_update(
        config,
        fetch_limit=40,
        analyze_limit=20,
        refresh_universe_list=False,
        refresh_market_data=True,
        build_features=True,
        optimize=False,
    )
    note = (
        f"agents fetch {result.fetch_ok}/{result.fetch_attempted} "
        f"analyze {result.analyze_ok}/{result.analyze_attempted} "
        f"{result.duration_seconds:.0f}s"
    )
    if result.status != "ok" and (result.fetch_error or result.analyze_error):
        # 部分成功仍刷新水位
        _refresh("行为分析", "日行情", "交易日期", note)
        _refresh("行为分析", "资金流向", "交易日期", note)
        _refresh("行为分析", "参与者活跃度", "交易日期", note)
        _refresh("行为分析", "分钟日特征", "交易日期", note)
        raise RuntimeError(
            f"行为分析更新部分失败: {note} status={result.status} msg={result.message}"
        )
    _refresh("行为分析", "日行情", "交易日期", note)
    _refresh("行为分析", "资金流向", "交易日期", note)
    _refresh("行为分析", "参与者活跃度", "交易日期", note)
    _refresh("行为分析", "分钟日特征", "交易日期", note)
    return f"行为分析: 新增/刷新完成（{note}）"


def update_predictor_series(updater) -> str:
    """指数预测.时序数据：调用 market-predictor 日更（跳过模拟盘）。"""
    from market_platform.predictor.分析.自动更新 import run_daily_update

    report = run_daily_update(force=True, skip_paper=True)
    status = report.get("status")
    note = f"predictor status={status} as_of={report.get('data_as_of')}"
    _refresh("指数预测", "时序数据", "交易日期", note)
    _refresh("指数预测", "指数特征", "交易日期", note)
    if status not in ("ok", "skipped"):
        raise RuntimeError(f"指数预测更新失败: {report.get('error') or note}")
    return f"指数预测: 新增/刷新完成（{note}）"


def update_latent_bars(updater) -> str:
    """隐因子.行情补丁：同步公共骨架并可选补缺口。"""
    _ensure_factor_latent()
    from factor_latent.脚本.增量补行情 import main

    # 默认同步公共骨架；限制 akshare 补洞数量，避免作业过久
    from market_platform.core.配置 import 日行情档案分界日

    # 分界日来自 config/数据库.yaml(评审 D7:此前硬编码)
    main(["--since", 日行情档案分界日(), "--fill-gaps", "--limit", "60", "--sleep", "0.2"])
    note = "factor-latent 增量补行情"
    _refresh("隐因子", "行情补丁", "交易日期", note)
    _refresh("隐因子", "因子取值", "交易日期", note)
    return f"隐因子: 新增/刷新完成（{note}）"
