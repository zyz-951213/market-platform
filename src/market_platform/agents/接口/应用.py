"""行为分析 FastAPI 应用。

静态托管:默认挂载 `web/dist/行为分析`(统一门户以 `/行为分析` 前缀访问);
产物不存在时只提供 API。历史版本用 `frontend_dir()`(迁移后指向不存在的
`src/market_platform/前端`)且只挂 `/static`,无法承载 Vite 的 `/assets` 产物。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from market_platform.agents.接口.路由 import router
from market_platform.agents.核心.任务.调度器 import get_auto_update_scheduler
from market_platform.core.路径 import 前端产物
from market_platform.core.静态站点 import 挂载静态站点

域 = "行为分析"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    get_auto_update_scheduler().start()
    yield
    get_auto_update_scheduler().stop()


def create_app(
    *,
    serve_frontend: bool | None = None,
    static_dir: Path | str | None = None,
    enable_scheduler: bool = True,
) -> FastAPI:
    """创建 FastAPI 应用。

    enable_scheduler=False 时不启动内置自动更新调度线程(统一门户使用该模式:
    定时任务只应由 timer/CLI 单点触发,避免多进程重复调度)。
    """
    if serve_frontend is None:
        serve_frontend = os.getenv("SERVE_FRONTEND", "").lower() in {"1", "true", "yes"}

    app = FastAPI(
        title="Market Agents API",
        version="0.2.0",
        lifespan=_lifespan if enable_scheduler else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    if serve_frontend:
        挂载静态站点(app, static_dir or (前端产物() / 域))
    return app


app = create_app()
