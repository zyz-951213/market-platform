"""CLI:数值列 NaN 体检与清洗(见 data/存储/数值清洗.py 的说明)。"""

from __future__ import annotations

import sys

from market_platform.data.存储.数值清洗 import main as _main


def main(argv: list[str] | None = None) -> int:
    return _main(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
