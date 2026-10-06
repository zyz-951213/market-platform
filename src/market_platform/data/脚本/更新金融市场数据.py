"""金融市场数据 全量增量更新(akshare/baostock 增量到最新)。

用法:
    python -m market_platform.data.脚本.更新金融市场数据
    market data 更新金融市场数据.py

注意:本模块**导入不产生任何副作用**,所有更新动作只在 `main()` 中执行
(历史版本在模块顶层直接跑 22 项任务,任何 import/pytest 收集都会触发生产写入)。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import traceback

from market_platform.core.配置 import 数据库DSN
from market_platform.data.存储.数据更新器 import DataUpdater
from market_platform.data.存储 import 数据更新器_akshare as ak_u
from market_platform.data.存储 import 数据更新器_补充 as sup_u
from market_platform.data.存储 import 数据更新器_baostock as bs_u

DB_NAME = "金融市场数据"

# (名称, 函数) 顺序: 先日行情(最耗时) 再其它
TASKS = [
    ("公共-个股日行情(baostock)", bs_u.update_daily_bars),
    ("公共-市场日回报补充", sup_u.update_market_daily),
    ("公共-公司文件", sup_u.update_company_info),
    ("公共-上市状态", sup_u.update_list_status),
    ("公共-分配文件", sup_u.update_dividend),
    ("公共-指数基本信息", sup_u.update_index_info),
    ("公共-指数日行情", ak_u.update_index_daily),
    ("公共-汇率", ak_u.update_exchange_rate),
    ("公共-期货日交易", ak_u.update_futures_daily),
    ("公共-无风险利率", ak_u.update_risk_free),
    ("公共-宏观全部", ak_u.update_macro_all),
    ("公共-跨资产补充", ak_u.update_cross_market),
    ("公共-个股资金流向", sup_u.update_money_flow),
    ("公共-融资融券", sup_u.update_margin),
    ("公共-龙虎榜", sup_u.update_dragon_tiger),
    ("公共-龙虎榜营业部", sup_u.update_dragon_tiger_dept),
    ("公共-ETF清单", sup_u.update_etf_list),
    ("公共-ETF融资融券", sup_u.update_etf_margin),
    ("公共-财报利润表", bs_u.update_income),
    ("公共-财报资产负债表", bs_u.update_balance),
    ("公共-财报现金流量表", sup_u.update_cashflow),
    ("公共-预披露日期", ak_u.update_report_dates),
    # TASK-004:由 公共.日行情 聚合出市场收益增量段(补齐 公共.市场收益 视图的缺口)
    ("公共-市场收益(计算段)", lambda u: _市场收益(u)),
]


def _市场收益(_updater) -> str:
    from market_platform.data.存储.市场收益构建 import 构建市场收益

    统计 = 构建市场收益()
    return f"市场收益: 写入 {统计['写入行']} 行({统计['起']} → {统计['止']})"


def 运行更新(*, 只跑: tuple[str, ...] = (), 跳过水位: bool = False) -> tuple[int, int]:
    """顺序执行更新任务,返回 (成功数, 失败数)。"""
    updater = DataUpdater(DB_NAME, 数据库DSN())
    成功 = 失败 = 0

    for name, fn in TASKS:
        if 只跑 and not any(k in name for k in 只跑):
            print(f"[SKIP] {name}", flush=True)
            continue
        t0 = time.time()
        try:
            msg = fn(updater)
            成功 += 1
            print(f"[OK]   {name}: {msg} ({time.time() - t0:.0f}s)", flush=True)
        except Exception as e:  # noqa: BLE001 — 单项失败不阻断后续任务
            失败 += 1
            print(
                f"[FAIL] {name}: {type(e).__name__} {str(e)[:120]} ({time.time() - t0:.0f}s)",
                flush=True,
            )
            traceback.print_exc(limit=2)

    print("全部更新任务结束", flush=True)

    if not 跳过水位:
        重扫水位()
    return 成功, 失败


def 重扫水位() -> int:
    """调用水位重扫脚本(独立进程,避免模块耦合)。"""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "market_platform.data.脚本.刷新全库水位"],
            check=False,
        )
        if r.returncode == 0:
            print("[OK]   行情增量全库水位已刷新", flush=True)
        else:
            print(f"[FAIL] 全库水位 exit={r.returncode}", flush=True)
        return r.returncode
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] 全库水位: {type(e).__name__} {e}", flush=True)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="金融市场数据 增量更新")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="只跑名称包含该子串的任务(可重复),例如 --only 日行情",
    )
    parser.add_argument("--no-watermark", action="store_true", help="结束后不重扫水位")
    args = parser.parse_args(argv)

    成功, 失败 = 运行更新(只跑=tuple(args.only), 跳过水位=args.no_watermark)
    print(f"更新汇总: 成功 {成功} / 失败 {失败}", flush=True)
    return 0 if 失败 == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
