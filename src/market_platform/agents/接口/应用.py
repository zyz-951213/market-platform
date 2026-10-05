from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from market_platform.agents.接口.路由 import router
from market_platform.agents.核心.任务.调度器 import get_auto_update_scheduler


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    get_auto_update_scheduler().start()
    yield
    get_auto_update_scheduler().stop()


def create_app(*, serve_frontend: bool | None = None) -> FastAPI:
    """创建 FastAPI 应用。默认仅提供 REST API；设置 SERVE_FRONTEND=1 可挂载前端静态资源。"""
    if serve_frontend is None:
        serve_frontend = os.getenv("SERVE_FRONTEND", "").lower() in {"1", "true", "yes"}

    app = FastAPI(title="Market Agents API", version="0.1.0", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    if serve_frontend:
        from market_platform.agents.核心.路径.项目 import frontend_dir

        static = frontend_dir()
        if static.exists():
            app.mount("/static", StaticFiles(directory=str(static)), name="static")

            @app.get("/")
            def index():
                return FileResponse(
                    static / "index.html",
                    headers={"Cache-Control": "no-cache"},
                )

    return app


app = create_app()
