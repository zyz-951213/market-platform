"""CLI:数据体检(只读;检查涨跌幅量纲 / numeric NaN / 市场收益计算段)。

    market-platform data 数据体检
    market-platform data 数据体检 --仅 涨跌幅量纲
"""

from __future__ import annotations

import sys

from market_platform.data.存储.数据体检 import 打印数据体检, 运行数据体检


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    仅: list[str] = []
    for i, a in enumerate(argv):
        if a in ("--仅", "--only") and i + 1 < len(argv):
            仅.append(argv[i + 1])
    报告 = 运行数据体检(仅 or None)
    return 打印数据体检(报告)


if __name__ == "__main__":
    raise SystemExit(main())
