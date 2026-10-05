"""启动数据底座 Web 服务(独立端口;统一站点请用 `market-platform web`)。

用法: python -m market_platform.data.脚本.启动Web
"""
from __future__ import annotations
import os

import uvicorn


def main(argv: list[str] | None = None) -> int:
    port = int(os.environ.get("API_PORT", "18766"))
    host = os.environ.get("API_HOST", "127.0.0.1")
    os.environ.setdefault("SERVE_FRONTEND", "1")

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
