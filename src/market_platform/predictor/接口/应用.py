"""指数预测 FastAPI 应用。

静态托管:默认挂载 `web/dist/指数预测`(统一门户以 `/指数预测` 前缀访问);
产物不存在时只提供 API。历史版本用 `frontend_dist_dir()`(迁移后指向不存在的
`src/market_platform/前端/dist`)。
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from market_platform.core.配置 import 加载环境变量
from market_platform.core.路径 import 前端产物
from market_platform.core.静态站点 import 挂载静态站点
from market_platform.predictor.接口.路由 import router

域 = "指数预测"

# 环境变量(LLM 密钥等)来自 config/.env
加载环境变量()


def create_app(
    *,
    serve_frontend: bool | None = None,
    static_dir: Path | str | None = None,
) -> FastAPI:
    if serve_frontend is None:
        serve_frontend = os.getenv("SERVE_FRONTEND", "").lower() in {"1", "true", "yes"}

    app = FastAPI(title="Market Predictor API", version="0.3.0")
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
