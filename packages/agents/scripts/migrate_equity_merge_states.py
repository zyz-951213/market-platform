#!/usr/bin/env python3
"""检查策略净值表结构（旧 market_states 合并迁移已完成）。

  PYTHONPATH=后端 python3 scripts/migrate_equity_merge_states.py
  PYTHONPATH=后端 python3 scripts/migrate_equity_merge_states.py --vacuum
"""

from __future__ import annotations

import argparse
import json
import sys

from market_platform.agents.存储.数据库 import PostgresStore, load_database_settings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vacuum", action="store_true", help="结束后 VACUUM FULL 行为分析.策略净值")
    args = parser.parse_args()

    settings = load_database_settings()
    if not settings.enabled or not settings.dsn:
        print("数据库未启用", file=sys.stderr)
        return 1

    st = PostgresStore(settings)
    before = {}
    conn = st._conn()
    with conn.cursor() as cur:
        cur.execute("SELECT pg_size_pretty(pg_total_relation_size('行为分析.策略净值'))")
        before["策略净值"] = cur.fetchone()[0]
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema IN ('public', '行为分析') AND table_name='market_states'
                  AND table_type='BASE TABLE'
            )
            """
        )
        before["has_states"] = bool(cur.fetchone()[0])
        cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
        before["db"] = cur.fetchone()[0]

    print("before:", json.dumps(before, ensure_ascii=False))
    stats = st.migrate_equity_merge_states()
    print("migrate:", json.dumps(stats, ensure_ascii=False))

    if args.vacuum:
        print("VACUUM FULL 行为分析.策略净值 …")
        old = conn.autocommit
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute('VACUUM FULL "行为分析"."策略净值"')
        finally:
            conn.autocommit = old

    with conn.cursor() as cur:
        cur.execute("SELECT pg_size_pretty(pg_total_relation_size('行为分析.策略净值'))")
        eq = cur.fetchone()[0]
        cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
        db = cur.fetchone()[0]
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='行为分析' AND table_name='策略净值'
            ORDER BY ordinal_position
            """
        )
        cols = [r[0] for r in cur.fetchall()]
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema IN ('public', '行为分析') AND table_name='market_states'
                  AND table_type='BASE TABLE'
            )
            """
        )
        still = bool(cur.fetchone()[0])
    print(
        "after:",
        json.dumps(
            {"策略净值": eq, "db": db, "cols": cols, "market_states_table": still},
            ensure_ascii=False,
        ),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
