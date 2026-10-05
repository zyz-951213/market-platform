"""金融市场数据 补拉: 公司文件/龙虎榜/汇率/无风险利率 (数据源有 08-05 数据)"""
import sys, time, traceback
sys.path.insert(0, "/home/zhuyizhou/my_projects/market-platform/src")
from market_platform.data.存储.数据更新器 import DataUpdater
from market_platform.data.存储 import 数据更新器_akshare as ak_u
from market_platform.data.存储 import 数据更新器_补充 as sup_u

DB_NAME = "金融市场数据"
DSN = "postgresql:///%E9%87%91%E8%9E%8D%E5%B8%82%E5%9C%BA%E6%95%B0%E6%8D%AE"
updater = DataUpdater(DB_NAME, DSN)

TASKS = [
    ("公司文件", sup_u.update_company_info),
    ("龙虎榜", sup_u.update_dragon_tiger),
    ("龙虎榜营业部", sup_u.update_dragon_tiger_dept),
    ("汇率", ak_u.update_exchange_rate),
    ("无风险利率", ak_u.update_risk_free),
    ("上市状态变更", sup_u.update_list_status),
    ("指数基本信息", sup_u.update_index_info),
]

for name, fn in TASKS:
    t0 = time.time()
    try:
        msg = fn(updater)
        print(f"[OK]   {name}: {msg} ({time.time()-t0:.0f}s)", flush=True)
    except Exception as e:
        print(f"[FAIL] {name}: {type(e).__name__} {str(e)[:120]} ({time.time()-t0:.0f}s)", flush=True)
        traceback.print_exc(limit=2)
print("补拉结束", flush=True)
