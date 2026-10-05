"""统一门户 —— 一个服务、一个端口同时托管「统一站点 + 三域 API」。

路由布局:
    /                       统一站点壳(web/dist/index.html)
    /assets/*               站点壳静态资源
    /数据底座/              数据底座子应用页面(web/dist/数据底座)
    /数据底座/api/*         数据底座 API(原 :18766,同一进程内挂载)
    /行为分析/  /行为分析/api/*
    /指数预测/  /指数预测/api/*
    /健康                    三域装配与站点产物状态

设计要点:
1. **向后兼容**:三域各自独立服务(:18766 / :8766 / :18765)不受影响;
   门户只是把它们聚到一个入口,某一路失败不会拖垮其余。
2. **无重复调度**:门户不启动行为分析的内置自动更新线程
   (`enable_scheduler=False`),定时任务只由 systemd timer / CLI 单点触发。
3. **路径唯一真相**:站点产物位置来自 `core.路径.前端产物()`,不写死相对层级。
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from market_platform.core.路径 import 前端产物

域_数据底座 = "数据底座"
域_行为分析 = "行为分析"
域_指数预测 = "指数预测"

版本 = "1.0.0"

# (域, 独立端口, 应用工厂)
域装配表: tuple[tuple[str, int, Any], ...] = (
    (域_数据底座, 18766, "market_platform.data.接口.应用"),
    (域_行为分析, 8766, "market_platform.agents.接口.应用"),
    (域_指数预测, 18765, "market_platform.predictor.接口.应用"),
)


def _取应用工厂(模块路径: str, 域: str):
    import importlib

    模块 = importlib.import_module(模块路径)
    选项: dict[str, Any] = {"serve_frontend": True, "static_dir": 前端产物() / 域}
    if 域 == 域_行为分析:
        选项["enable_scheduler"] = False
    return lambda: 模块.create_app(**选项)


def create_app() -> FastAPI:
    应用 = FastAPI(
        title="市场量化平台 · 统一门户",
        version=版本,
        description="统一站点 + 数据底座 / 行为分析 / 指数预测 三域 API",
    )
    应用.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    已装: list[str] = []
    失败: list[dict[str, str]] = []
    for 域, _端口, 模块路径 in 域装配表:
        try:
            子应用 = _取应用工厂(模块路径, 域)()
        except Exception as exc:  # noqa: BLE001 — 单域失败不拖垮门户
            失败.append({"域": 域, "错误": f"{type(exc).__name__}: {exc}"})
            print(f"[门户] 域 {域} 装配失败: {type(exc).__name__}: {exc}")
            continue
        应用.mount(f"/{域}", 子应用)
        已装.append(域)

    @应用.get("/健康")
    def 健康() -> dict[str, Any]:
        """门户自身与三域装配状态。"""
        产物 = 前端产物()
        return {
            "status": "ok",
            "version": 版本,
            "已装配域": 已装,
            "装配失败": 失败,
            "站点产物": str(产物) if 产物.exists() else None,
            "站点已构建": (产物 / "index.html").exists(),
            "域": [
                {"名": 域, "独立端口": 端口, "站点路径": f"/{域}/", "接口前缀": f"/{域}/api"}
                for 域, 端口, _模块 in 域装配表
            ],
        }

    # 站点壳最后挂载:先匹配三个域的挂载点,其余交给静态站点
    站点 = 前端产物()
    if (站点 / "index.html").exists():
        应用.mount("/", StaticFiles(directory=str(站点), html=True), name="站点")
    else:

        @应用.get("/")
        def 未构建() -> JSONResponse:  # pragma: no cover - 仅未构建时触发
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "统一站点尚未构建,请执行: cd web && node 构建.mjs",
                    "期望产物": str(站点),
                },
            )

    return 应用


app = create_app()


def main(argv: list[str] | None = None) -> int:
    """启动统一门户(默认 0.0.0.0:18800)。"""
    import uvicorn

    host = os.environ.get("门户_HOST", os.environ.get("API_HOST", "0.0.0.0"))
    port = int(os.environ.get("门户_PORT", "18800"))
    print(f"[门户] 统一站点与三域 API: http://{host}:{port}/")
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
