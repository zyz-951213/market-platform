"""FastAPI 应用"""
from __future__ import annotations
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .路由 import router


def create_app(*, serve_frontend: bool | None = None) -> FastAPI:
    if serve_frontend is None:
        serve_frontend = os.getenv("SERVE_FRONTEND", "1").lower() in {"1", "true", "yes"}

    app = FastAPI(title="金融市场数据管理", version="0.2.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    if serve_frontend:
        frontend_dist = Path(__file__).resolve().parents[2] / "前端" / "dist"
        if frontend_dist.exists() and (frontend_dist / "index.html").exists():
            assets = frontend_dist / "assets"
            if assets.is_dir():
                app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

            @app.get("/")
            def index():
                return FileResponse(
                    frontend_dist / "index.html",
                    headers={"Cache-Control": "no-cache"},
                )

            @app.get("/{full_path:path}")
            def spa_fallback(full_path: str):
                candidate = frontend_dist / full_path
                if candidate.is_file():
                    return FileResponse(candidate)
                return FileResponse(
                    frontend_dist / "index.html",
                    headers={"Cache-Control": "no-cache"},
                )

    return app


app = create_app()
