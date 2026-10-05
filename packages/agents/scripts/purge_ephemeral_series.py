#!/usr/bin/env python3
"""清理已停写的衍生时序。

当前库已改为窄表（分钟日特征 / 参与者活跃度），ephemeral 系列不再落库；
本脚本仅做状态检查。

  PYTHONPATH=后端 python scripts/purge_ephemeral_series.py
"""

from __future__ import annotations

import argparse
import sys

from market_platform.agents.存储 import data_access as store
from market_platform.agents.存储.数据库 import PostgresStore, load_database_settings

TYPES = tuple(sorted(store.SERIES_EPHEMERAL))


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 ephemeral symbol_series（中文窄表库已无此类数据）")
    parser.add_argument("--execute", action="store_true", help="兼容旧参数（无操作）")
    parser.add_argument("--vacuum", action="store_true", help="兼容旧参数（无操作）")
    args = parser.parse_args()
    del args  # 窄表库无需 DELETE

    settings = load_database_settings()
    if not settings.enabled or not settings.dsn:
        print("数据库未启用", file=sys.stderr)
        return 1
    st = PostgresStore(settings)
    conn = st._conn()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pg_size_pretty(pg_total_relation_size('行为分析.分钟日特征')),
                   pg_size_pretty(pg_total_relation_size('行为分析.参与者活跃度'))
            """
        )
        feat, act = cur.fetchone()
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema='行为分析' AND table_name='symbol_series'
                  AND table_type='BASE TABLE'
            )
            """
        )
        has_base = bool(cur.fetchone()[0])

    print(f"分钟日特征={feat}; 参与者活跃度={act}")
    print(f"ephemeral 类型 {', '.join(TYPES)} 已停写")
    if has_base:
        print("警告: 仍存在旧基表 symbol_series，请手工评估后删除")
        return 1
    print("无需清理（窄表 schema，无 JSONB symbol_series 基表）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
