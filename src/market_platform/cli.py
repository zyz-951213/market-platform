"""统一 CLI 入口：market-platform <agents|predictor> [参数]。"""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("用法: market-platform <agents|predictor> [参数]")
        print("  agents      行为分析（原 market-agents）")
        print("  predictor   指数预测（原 market-predictor）")
        return 0

    module = sys.argv[1]
    rest = sys.argv[2:]

    if module == "agents":
        from market_platform.agents.cli import main as agents_main

        sys.argv = ["market-agents", *rest]
        return int(agents_main() or 0)

    if module == "predictor":
        from market_platform.predictor.命令行 import main as predictor_main

        sys.argv = ["market-predictor", *rest]
        return int(predictor_main() or 0)

    print(f"未知模块: {module}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
