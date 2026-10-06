"""缺口定向回填(薄封装;实现在 `market_platform.data.存储.缺口回填`)。

用法: market-platform data 缺口回填 [--dry-run] [--only 数据集]
"""

from __future__ import annotations

from market_platform.data.存储.缺口回填 import main

if __name__ == "__main__":
    raise SystemExit(main())
