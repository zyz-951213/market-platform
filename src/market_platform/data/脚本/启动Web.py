"""启动数据库管理系统 Web 服务。

前端请先 `cd 前端 && npm run build`，或开发模式另开 `npm run dev`。
"""
from __future__ import annotations
import os
import sys
import uvicorn


def main():
    port = int(os.environ.get("API_PORT", "18766"))
    host = os.environ.get("API_HOST", "127.0.0.1")
    os.environ.setdefault("SERVE_FRONTEND", "1")

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    if root not in sys.path:
        sys.path.insert(0, root)

    print(f"金融市场数据管理: http://{host}:{port}")
    print(f"API 文档: http://{host}:{port}/docs")
    uvicorn.run(
        "market_platform.data.接口.应用:app",
        host=host,
        port=port,
        reload=os.environ.get("RELOAD", "").lower() in {"1", "true", "yes"},
        log_level="info",
    )


if __name__ == "__main__":
    main()
