#!/usr/bin/env python3
"""扫描统一库全部物理表（+关键逻辑接口），登记并刷新 行情增量.同步水位。

用法:
  python -m market_platform.data.脚本.刷新全库水位
  python -m market_platform.data.脚本.刷新全库水位 --dry-run
"""
from __future__ import annotations

from market_platform.core.配置 import 数据库DSN

import argparse
import sys
from datetime import date, datetime
from typing import Any

import psycopg

DSN = 数据库DSN()

SCHEMAS = (
    "公共",
    "行为分析",
    "指数预测",
    "隐因子",
    "微盘股研究",
    "特别处理研究",
)

# 水位列优先级（命中即用）
DATE_COL_PRIORITY = (
    "交易日期",
    "trade_date",
    "trade_month",
    "统计截止日期",
    "日历日期",
    "cal_date",
    "月度",
    "月度标识",
    "季度",
    "日期标识",
    "汇率日期",
    "统计日期",
    "行情日期",
    "snapshot_date",
    "as_of_date",
    "asof_date",
    "event_date",
    "change_date",
    "announce_date",
    "effective_date",
    "list_date",
    "数据截至",
    "更新时间",
    "updated_at",
    "创建时间",
    "created_at",
)

# (schema, table) -> overrides
OVERRIDES: dict[tuple[str, str], dict[str, str]] = {
    ("公共", "个股日行情文件"): {
        "责任方": "database",
        "更新策略": "增量",
        "水位列": "交易日期",
        "更新入口": "数据更新器_baostock.update_daily_bars",
        "备注": "仅保留>2026-05-20；档案在微盘.日行情",
    },
    ("公共", "日行情"): {
        "对象类型": "逻辑接口",
        "责任方": "database",
        "更新策略": "派生",
        "水位列": "trade_date",
        "更新入口": "视图=档案∪增量",
        "备注": "非物理表；衡量接口覆盖",
    },
    ("微盘股研究", "日行情"): {
        "责任方": "微盘股研究",
        "更新策略": "冻结",
        "水位列": "trade_date",
        "更新入口": "import_csmar --force-archive-daily",
        "备注": "CSMAR档案 ≤2026-05-20",
    },
    ("微盘股研究", "日回报"): {
        "责任方": "微盘股研究",
        "更新策略": "冻结",
        "水位列": "trade_date",
        "更新入口": "import_csmar daily_ret",
        "备注": "回报窄表；OHLCV 来自日行情 JOIN",
    },
    ("行为分析", "日行情"): {
        "责任方": "行为分析",
        "更新策略": "增量",
        "水位列": "交易日期",
        "更新入口": "market-agents sync",
        "备注": "应用瘦表，复权口径独立",
    },
    ("隐因子", "因子取值"): {
        "责任方": "隐因子",
        "更新策略": "增量",
        "水位列": "交易日期",
        "更新入口": "factor-latent pipeline",
    },
    ("隐因子", "隐空间坐标"): {
        "责任方": "隐因子",
        "更新策略": "增量",
        "水位列": "交易日期",
        "更新入口": "factor-latent 运行PCA",
    },
    ("隐因子", "行情补丁"): {
        "责任方": "隐因子",
        "更新策略": "增量",
        "水位列": "交易日期",
        "更新入口": "增量补行情 --fill-gaps",
        "备注": "公共断档桥接",
    },
    ("指数预测", "指数特征"): {
        "责任方": "指数预测",
        "更新策略": "增量",
        "水位列": "交易日期",
        "更新入口": "market-predictor",
    },
    ("指数预测", "时序数据"): {
        "责任方": "指数预测",
        "更新策略": "增量",
        "水位列": "交易日期",
        "更新入口": "market-predictor",
    },
}

OWNER_BY_SCHEMA = {
    "公共": "database",
    "行为分析": "行为分析",
    "指数预测": "指数预测",
    "隐因子": "隐因子",
    "微盘股研究": "微盘股研究",
    "特别处理研究": "特别处理研究",
}

# 公共表默认策略粗分
PUBLIC_FROZEN_HINTS = ("公司文件", "证券档案", "口径", "定义")
PUBLIC_FULL_HINTS = ("日历文件",)


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def pick_date_col(cols: list[str]) -> str | None:
    lower_map = {c.lower(): c for c in cols}
    for pref in DATE_COL_PRIORITY:
        if pref in cols:
            return pref
        if pref.lower() in lower_map:
            return lower_map[pref.lower()]
    return None


def default_strategy(schema: str, table: str) -> str:
    if schema == "微盘股研究":
        return "冻结"
    if schema == "特别处理研究":
        return "人工"
    if schema == "公共":
        if any(h in table for h in PUBLIC_FROZEN_HINTS):
            return "人工"
        if any(h in table for h in PUBLIC_FULL_HINTS):
            return "全量"
        return "增量"
    if schema in ("行为分析", "隐因子", "指数预测"):
        return "增量"
    return "人工"


def classify_status(
    strategy: str,
    n: int | None,
    d1: date | None,
    d0: date | None,
) -> str:
    if n is None:
        return "扫描失败"
    if n == 0:
        return "空表"
    if strategy == "冻结":
        return "冻结"
    if strategy == "派生":
        return "派生"
    if d1 is None:
        return "无日期水位"
    # 粗：最后日距今天超过 10 天标滞后（交易日历未精细化）
    lag = (date.today() - d1).days
    if strategy == "增量" and lag > 10:
        return f"疑似滞后({lag}天)"
    return "正常"


def fetch_relations(conn) -> list[dict[str, Any]]:
    sql = """
        SELECT n.nspname AS schema_name, c.relname AS table_name, c.relkind,
               pg_total_relation_size(c.oid) AS bytes
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = ANY(%s)
          AND c.relkind = 'r'
        ORDER BY 1, 2
    """
    with conn.cursor() as cur:
        cur.execute(sql, (list(SCHEMAS),))
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def fetch_columns(conn, schema: str, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema, table),
        )
        return [r[0] for r in cur.fetchall()]


def measure(
    conn, schema: str, table: str, date_col: str | None, bytes_: int
) -> dict[str, Any]:
    fq = f"{qident(schema)}.{qident(table)}"
    out: dict[str, Any] = {
        "最早日": None,
        "最后日": None,
        "行数": None,
        "表体积字节": bytes_,
        "水位列": date_col,
        "error": None,
    }
    try:
        with conn.cursor() as cur:
            if date_col:
                dc = qident(date_col)
                # YYYY-MM 月频文本
                if date_col in ("trade_month", "月度", "月度标识") or date_col.endswith("月"):
                    cur.execute(
                        f"""
                        SELECT COUNT(*)::bigint,
                               MIN(to_date(left(({dc})::text || '-01', 10), 'YYYY-MM-DD')),
                               MAX(to_date(left(({dc})::text || '-01', 10), 'YYYY-MM-DD'))
                        FROM {fq}
                        WHERE NULLIF(btrim(({dc})::text), '') IS NOT NULL
                        """
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT COUNT(*)::bigint,
                               MIN(({dc})::date),
                               MAX(({dc})::date)
                        FROM {fq}
                        """
                    )
            else:
                cur.execute(f"SELECT COUNT(*)::bigint, NULL::date, NULL::date FROM {fq}")
            n, d0, d1 = cur.fetchone()
            out["行数"] = int(n)
            out["最早日"] = d0
            out["最后日"] = d1
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        out["error"] = f"{type(e).__name__}: {e}"
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*)::bigint FROM {fq}")
                out["行数"] = int(cur.fetchone()[0])
            conn.commit()
        except Exception as e2:  # noqa: BLE001
            conn.rollback()
            out["error"] = f"{out['error']}; count:{type(e2).__name__}"
    return out


def upsert_registry(conn, rows: list[dict[str, Any]], dry_run: bool) -> None:
    sql = """
        INSERT INTO 行情增量.数据集登记 (
          数据源, 数据集, 对象类型, 水位列, 责任方, 更新策略, 更新入口, 是否启用, 备注
        ) VALUES (
          %(数据源)s, %(数据集)s, %(对象类型)s, %(水位列)s, %(责任方)s,
          %(更新策略)s, %(更新入口)s, true, %(备注)s
        )
        ON CONFLICT (数据源, 数据集) DO UPDATE SET
          对象类型 = EXCLUDED.对象类型,
          水位列 = COALESCE(EXCLUDED.水位列, 行情增量.数据集登记.水位列),
          责任方 = EXCLUDED.责任方,
          更新策略 = EXCLUDED.更新策略,
          更新入口 = COALESCE(EXCLUDED.更新入口, 行情增量.数据集登记.更新入口),
          备注 = COALESCE(EXCLUDED.备注, 行情增量.数据集登记.备注),
          是否启用 = true
    """
    if dry_run:
        return
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()


def upsert_watermark(conn, rows: list[dict[str, Any]], dry_run: bool) -> None:
    sql = """
        INSERT INTO 行情增量.同步水位 (
          数据源, 数据集, 最早日, 最后交易日, 行数, 水位列,
          责任方, 更新策略, 表体积字节, 状态, 备注, 更新时间
        ) VALUES (
          %(数据源)s, %(数据集)s, %(最早日)s, %(最后日)s, %(行数)s, %(水位列)s,
          %(责任方)s, %(更新策略)s, %(表体积字节)s, %(状态)s, %(备注)s, now()
        )
        ON CONFLICT (数据源, 数据集) DO UPDATE SET
          最早日 = EXCLUDED.最早日,
          最后交易日 = EXCLUDED.最后交易日,
          行数 = EXCLUDED.行数,
          水位列 = EXCLUDED.水位列,
          责任方 = EXCLUDED.责任方,
          更新策略 = EXCLUDED.更新策略,
          表体积字节 = EXCLUDED.表体积字节,
          状态 = EXCLUDED.状态,
          备注 = EXCLUDED.备注,
          更新时间 = now()
    """
    if dry_run:
        return
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = psycopg.connect(DSN)
    try:
        # ensure DDL
        with conn.cursor() as cur:
            cur.execute((schema根() / "行情增量全库水位.sql").read_text(encoding="utf-8"))
        conn.commit()

        relations = fetch_relations(conn)
        reg_rows: list[dict[str, Any]] = []
        wm_rows: list[dict[str, Any]] = []

        for rel in relations:
            schema, table = rel["schema_name"], rel["table_name"]
            cols = fetch_columns(conn, schema, table)
            ov = OVERRIDES.get((schema, table), {})
            date_col = ov.get("水位列") or pick_date_col(cols)
            owner = ov.get("责任方") or OWNER_BY_SCHEMA.get(schema, "系统")
            strategy = ov.get("更新策略") or default_strategy(schema, table)
            obj_type = ov.get("对象类型", "表")
            note = ov.get("备注")
            entry = ov.get("更新入口")

            reg_rows.append(
                {
                    "数据源": schema,
                    "数据集": table,
                    "对象类型": obj_type,
                    "水位列": date_col,
                    "责任方": owner,
                    "更新策略": strategy,
                    "更新入口": entry,
                    "备注": note,
                }
            )

            m = measure(conn, schema, table, date_col, int(rel["bytes"]))
            status = classify_status(strategy, m["行数"], m["最后日"], m["最早日"])
            if m["error"]:
                status = "扫描失败"
                note = (note + "; " if note else "") + m["error"][:200]

            wm_rows.append(
                {
                    "数据源": schema,
                    "数据集": table,
                    "最早日": m["最早日"],
                    "最后日": m["最后日"],
                    "行数": m["行数"],
                    "水位列": date_col,
                    "责任方": owner,
                    "更新策略": strategy,
                    "表体积字节": m["表体积字节"],
                    "状态": status,
                    "备注": note,
                }
            )
            print(
                f"{schema}.{table}: n={m['行数']} {date_col}={m['最早日']}..{m['最后日']} [{status}]",
                flush=True,
            )

        # 逻辑接口：公共.日行情
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)::bigint, MIN(trade_date), MAX(trade_date)
                FROM 公共.日行情
                """
            )
            n, d0, d1 = cur.fetchone()
        ov = OVERRIDES[("公共", "日行情")]
        reg_rows.append(
            {
                "数据源": "公共",
                "数据集": "日行情",
                "对象类型": "逻辑接口",
                "水位列": "trade_date",
                "责任方": ov["责任方"],
                "更新策略": ov["更新策略"],
                "更新入口": ov["更新入口"],
                "备注": ov["备注"],
            }
        )
        wm_rows.append(
            {
                "数据源": "公共",
                "数据集": "日行情",
                "最早日": d0,
                "最后日": d1,
                "行数": int(n),
                "水位列": "trade_date",
                "责任方": ov["责任方"],
                "更新策略": ov["更新策略"],
                "表体积字节": None,
                "状态": classify_status("派生", int(n), d1, d0),
                "备注": ov["备注"],
            }
        )
        print(f"公共.日行情(接口): n={n} trade_date={d0}..{d1}", flush=True)

        upsert_registry(conn, reg_rows, args.dry_run)
        upsert_watermark(conn, wm_rows, args.dry_run)

        if not args.dry_run:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM 行情增量.数据集登记")
                nr = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM 行情增量.同步水位")
                nw = cur.fetchone()[0]
                cur.execute(
                    """
                    SELECT 状态, COUNT(*) FROM 行情增量.同步水位
                    GROUP BY 1 ORDER BY 2 DESC
                    """
                )
                by_status = cur.fetchall()
            print(f"\n登记 {nr} 条，水位 {nw} 条", flush=True)
            for s, c in by_status:
                print(f"  {s}: {c}", flush=True)
            print("查看: SELECT * FROM 行情增量.水位总览;", flush=True)
        else:
            print(f"\n[dry-run] would upsert registry={len(reg_rows)} watermarks={len(wm_rows)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
