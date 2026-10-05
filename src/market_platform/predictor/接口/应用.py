"""FastAPI 应用"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from market_platform.predictor.配置 import get_config
from market_platform.predictor.接口.路由 import router
from market_platform.predictor.核心.路径.项目 import frontend_dist_dir, repo_root

try:
    from dotenv import load_dotenv
    load_dotenv(repo_root() / ".env")
except ImportError:
    pass


def create_app(*, serve_frontend: bool | None = None) -> FastAPI:
    """创建 FastAPI 应用。默认仅提供 REST API；设置 SERVE_FRONTEND=1 可挂载前端构建产物。"""
    if serve_frontend is None:
        serve_frontend = os.getenv("SERVE_FRONTEND", "").lower() in {"1", "true", "yes"}

    app = FastAPI(title="Market Predictor API", version="0.2.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    if serve_frontend:
        static = frontend_dist_dir()
        if static.exists():
            assets = static / "assets"
            if assets.is_dir():
                app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

            @app.get("/")
            def index():
                return FileResponse(
                    static / "index.html",
                    headers={"Cache-Control": "no-cache"},
                )

            @app.get("/{full_path:path}")
            def spa_fallback(full_path: str):
                candidate = static / full_path
                if candidate.is_file():
                    return FileResponse(candidate)
                return FileResponse(
                    static / "index.html",
                    headers={"Cache-Control": "no-cache"},
                )

    return app


app = create_app()
