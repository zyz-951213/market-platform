"""共享静态站点挂载 —— 三域应用统一用它托管前端产物。

背景(评审发现):三个应用各自用 `Path(__file__).parents[n] / "前端" / "dist"` 推导静态目录,
迁移后全部指向不存在的 `src/market_platform/前端/dist`,导致页面 404。
现统一改为由 `core.路径.前端产物()` 推导 `web/dist/<域>`,并接受显式覆盖。

对外契约:
    挂载静态站点(app, 目录, 资源前缀="")
    - 目录不存在 → 什么也不做(保持 API 可用,不报错)
    - 注册 `资源前缀/assets` 静态资源 + `资源前缀/` 与任意子路径的 SPA 回退
    - 必须在所有 API 路由注册**之后**调用
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

logger = logging.getLogger("market_platform.core.静态站点")


def 挂载静态站点(app: FastAPI, 目录: Path | str, *, 资源前缀: str = "") -> bool:
    """把前端产物挂到应用上;返回是否真的挂载。"""
    根 = Path(目录)
    if not (根 / "index.html").exists():
        logger.info("静态站点未构建,跳过挂载: %s", 根)
        return False

    前缀 = (资源前缀 or "").rstrip("/")
    资源目录 = 根 / "assets"
    if 资源目录.is_dir():
        app.mount(
            f"{前缀}/assets",
            StaticFiles(directory=str(资源目录)),
            name=f"资源{前缀 or '根'}",
        )

    @app.get(f"{前缀}/", include_in_schema=False)
    def _首页() -> FileResponse:  # pragma: no cover - 薄封装
        return FileResponse(根 / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get(f"{前缀}/{{full_path:path}}", include_in_schema=False)
    def _回退(full_path: str) -> FileResponse:  # pragma: no cover - 薄封装
        # 注意:路由占位符必须是 ASCII 名称,Starlette 的路径参数正则不认中文名,
        # 写成 `{{完整路径:path}}` 会退化为字面量路径,导致深链接 404。
        候选 = 根 / full_path
        if 候选.is_file():
            return FileResponse(候选)
        return FileResponse(根 / "index.html", headers={"Cache-Control": "no-cache"})

    return True


__all__ = ["挂载静态站点"]
