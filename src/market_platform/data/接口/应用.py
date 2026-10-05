"""数据底座 FastAPI 应用。

静态托管:默认挂载 `web/dist/数据底座`(由统一门户以 `/<域>` 前缀访问);
产物不存在时只提供 API。历史版本硬编码 `parents[2]/前端/dist`,迁移后该路径
不存在、页面 404 —— 现统一走 `core.路径.前端产物()`。
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from market_platform.core.路径 import 前端产物
from market_platform.core.静态站点 import 挂载静态站点
from market_platform.data.接口.路由 import router

域 = "数据底座"


def create_app(
    *,
    serve_frontend: bool | None = None,
    static_dir: Path | str | None = None,
) -> FastAPI:
    if serve_frontend is None:
        serve_frontend = os.getenv("SERVE_FRONTEND", "0").lower() in {"1", "true", "yes"}

    app = FastAPI(title="金融市场数据管理", version="0.4.0")
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
