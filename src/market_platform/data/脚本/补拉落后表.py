"""金融市场数据 补拉: 公司文件/龙虎榜/汇率/无风险利率等易滞后表。

用法:
    python -m market_platform.data.脚本.补拉落后表

注意:导入本模块不产生副作用(历史版本在模块顶层直接执行补拉,import 即写库)。
"""

from __future__ import annotations

import time
import traceback

from market_platform.core.配置 import 数据库DSN
from market_platform.data.存储.数据更新器 import DataUpdater
from market_platform.data.存储 import 数据更新器_akshare as ak_u
from market_platform.data.存储 import 数据更新器_补充 as sup_u

DB_NAME = "金融市场数据"

TASKS = [
    ("公司文件", sup_u.update_company_info),
    ("龙虎榜", sup_u.update_dragon_tiger),
    ("龙虎榜营业部", sup_u.update_dragon_tiger_dept),
    ("汇率", ak_u.update_exchange_rate),
    ("无风险利率", ak_u.update_risk_free),
    ("上市状态变更", sup_u.update_list_status),
    ("指数基本信息", sup_u.update_index_info),
]


def main() -> int:
    updater = DataUpdater(DB_NAME, 数据库DSN())
    失败 = 0
    for name, fn in TASKS:
        t0 = time.time()
        try:
            msg = fn(updater)
            print(f"[OK]   {name}: {msg} ({time.time() - t0:.0f}s)", flush=True)
        except Exception as e:  # noqa: BLE001 — 单项失败不阻断后续补拉
            失败 += 1
            print(
                f"[FAIL] {name}: {type(e).__name__} {str(e)[:120]} ({time.time() - t0:.0f}s)",
                flush=True,
            )
            traceback.print_exc(limit=2)
    print("补拉结束", flush=True)
    return 0 if 失败 == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
