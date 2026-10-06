"""市场收益增量段构建(薄封装;实现在 `market_platform.data.存储.市场收益构建`)。

用法:
    market-platform data 构建市场收益 [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--dry-run] [--校验]
"""

from __future__ import annotations

from market_platform.data.存储.市场收益构建 import main

if __name__ == "__main__":
    raise SystemExit(main())
