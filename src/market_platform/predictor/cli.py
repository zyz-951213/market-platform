"""兼容入口：systemd / 旧脚本使用 `python -m market_platform.predictor.cli`。"""

from market_platform.predictor.命令行 import main

if __name__ == "__main__":
    raise SystemExit(main())
