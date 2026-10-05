"""金融市场数据 全量增量更新(akshare/baostock 增量到最新)"""
import sys, time, traceback
sys.path.insert(0, "/home/zhuyizhou/my_projects/market-platform/src")

from market_platform.data.存储.数据更新器 import DataUpdater
from market_platform.data.存储 import 数据更新器_akshare as ak_u
from market_platform.data.存储 import 数据更新器_补充 as sup_u
from market_platform.data.存储 import 数据更新器_baostock as bs_u

DB_NAME = "金融市场数据"
DSN = "postgresql:///%E9%87%91%E8%9E%8D%E5%B8%82%E5%9C%BA%E6%95%B0%E6%8D%AE"

updater = DataUpdater(DB_NAME, DSN)

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
]

for name, fn in TASKS:
    t0 = time.time()
    try:
        msg = fn(updater)
        print(f"[OK]   {name}: {msg} ({time.time()-t0:.0f}s)", flush=True)
    except Exception as e:
        print(f"[FAIL] {name}: {type(e).__name__} {str(e)[:120]} ({time.time()-t0:.0f}s)", flush=True)
        traceback.print_exc(limit=2)

print("全部更新任务结束", flush=True)

# 全库水位重扫（可衡量追踪）
try:
    import subprocess
    r = subprocess.run(
        [sys.executable, "/home/zhuyizhou/my_projects/market-platform/src/market_platform/data/脚本/刷新全库水位.py"],
        check=False,
    )
    if r.returncode == 0:
        print("[OK]   行情增量全库水位已刷新", flush=True)
    else:
        print(f"[FAIL] 全库水位 exit={r.returncode}", flush=True)
except Exception as e:
    print(f"[FAIL] 全库水位: {type(e).__name__} {e}", flush=True)
