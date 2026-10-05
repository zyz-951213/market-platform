#!/usr/bin/env python3
"""修复模拟盘：补持仓行情、回放净值曲线、校正 pending 状态。"""

from __future__ import annotations

import json
import sys

from market_platform.agents.settings import AppConfig
from market_platform.agents.分析.模拟盘.引擎 import repair_paper_state
from market_platform.agents.核心.路径.项目 import profile_path


def main() -> None:
    cfg = AppConfig.from_yaml(profile_path("默认.yaml"))
    result = repair_paper_state(cfg, fetch_bars=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
    sys.exit(0)
