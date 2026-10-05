"""PostgreSQL 数据库连接与查询层（支持多 schema）"""
from __future__ import annotations
import json
import re
from datetime import date, datetime
from decimal import Decimal
import psycopg

def _serialize(val):
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False, default=str)
    if isinstance(val, memoryview):
        return bytes(val).hex()
    if isinstance(val, (bytes, bytearray)):
        return val.hex()
    if isinstance(val, bool):
        return str(val)
    if val is None:
        return None
    return str(val)


class DatabaseManager:
    def __init__(self, dbs: dict[str, dict]):
        self._dbs = dbs

    def _get_conn(self, db_name: str) -> psycopg.Connection:
        info = self._dbs.get(db_name)
        if not info:
            raise ValueError(f"未知数据库: {db_name}")
        try:
            conn = psycopg.connect(info["dsn"], connect_timeout=5, autocommit=True)
            # 会话级只读，防止意外写库
            with conn.cursor() as cur:
                cur.execute("SET default_transaction_read_only = on")
                cur.execute("SET statement_timeout = '60s'")
            return conn
        except Exception as e:
            raise ConnectionError(f"连接 {db_name} 失败: {e}")

    def _execute(self, db_name: str, sql: str, params: tuple = None) -> dict:
        conn = None
        try:
            conn = self._get_conn(db_name)
            with conn.cursor() as cur:
                if params:
                    cur.execute(sql, params)
                else:
                    cur.execute(sql)
                if cur.description:
                    cols = [desc[0] for desc in cur.description]
                    return {"columns": cols, "rows": cur.fetchall()}
                return {"affected_rows": cur.rowcount}
        finally:
            if conn and not conn.closed:
                conn.close()

    def close_all(self):
        pass

    def list_schemas(self, db_name: str) -> list[str]:
        r = self._execute(db_name, """
            SELECT nspname FROM pg_namespace
            WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
              AND nspname NOT LIKE E'pg\\\\_toast%'
              AND nspname NOT LIKE E'pg\\\\_temp%'
            ORDER BY nspname
        """)
        return [row[0] for row in r["rows"]]

    def list_tables(self, db_name: str) -> list[dict]:
        r = self._execute(db_name, """
            SELECT t.tablename,
                   n.nspname AS schema_name,
                   pg_size_pretty(pg_total_relation_size(c.oid)) AS size,
                   pg_total_relation_size(c.oid) AS size_bytes,
                   COALESCE(s.n_live_tup, 0)::bigint AS row_estimate
            FROM pg_tables t
            JOIN pg_namespace n ON n.nspname = t.schemaname
            JOIN pg_class c ON c.relname = t.tablename AND c.relnamespace = n.oid
            LEFT JOIN pg_stat_user_tables s
              ON s.relid = c.oid
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
              AND n.nspname NOT LIKE E'pg\\\\_toast%'
              AND n.nspname NOT LIKE E'pg\\\\_temp%'
            ORDER BY n.nspname, t.tablename
        """)
        return [dict(zip(r["columns"], row)) for row in r["rows"]]

    def get_overview(self, db_name: str) -> dict:
        """概览：连接状态、schema 汇总、大表排行。"""
        schemas = []
        r = self._execute(db_name, """
            SELECT n.nspname AS schema_name,
                   COUNT(c.oid)::int AS table_count,
                   COALESCE(SUM(pg_total_relation_size(c.oid)), 0)::bigint AS size_bytes,
                   pg_size_pretty(COALESCE(SUM(pg_total_relation_size(c.oid)), 0)) AS size
            FROM pg_namespace n
            LEFT JOIN pg_class c
              ON c.relnamespace = n.oid AND c.relkind = 'r'
            WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
              AND n.nspname NOT LIKE E'pg\\\\_toast%'
              AND n.nspname NOT LIKE E'pg\\\\_temp%'
            GROUP BY n.nspname
            HAVING COUNT(c.oid) > 0
            ORDER BY size_bytes DESC
        """)
        for row in r["rows"]:
            schemas.append(dict(zip(r["columns"], row)))

        top = self._execute(db_name, """
            SELECT n.nspname AS schema_name,
                   c.relname AS tablename,
                   pg_total_relation_size(c.oid) AS size_bytes,
                   pg_size_pretty(pg_total_relation_size(c.oid)) AS size,
                   COALESCE(s.n_live_tup, 0)::bigint AS row_estimate
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
            WHERE c.relkind = 'r'
              AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
              AND n.nspname NOT LIKE E'pg\\\\_toast%'
            ORDER BY size_bytes DESC
            LIMIT 15
        """)
        top_tables = [dict(zip(top["columns"], row)) for row in top["rows"]]

        size_r = self._execute(db_name, "SELECT pg_size_pretty(pg_database_size(current_database())), pg_database_size(current_database())")
        db_size = size_r["rows"][0][0] if size_r.get("rows") else "N/A"
        db_size_bytes = size_r["rows"][0][1] if size_r.get("rows") else 0
        table_count = sum(s["table_count"] for s in schemas)

        watermarks: list[dict] = []
        try:
            wm = self._execute(db_name, """
                SELECT "数据源", "数据集", "责任方", "最早日", "最后日",
                       "行数", "状态", "更新策略"
                FROM "行情增量"."水位总览"
                WHERE "状态" IS DISTINCT FROM '正常'
                   OR "状态" IS NULL
                ORDER BY
                  CASE WHEN "状态" LIKE '疑似滞后%' THEN 0
                       WHEN "状态" = '冻结' THEN 2
                       ELSE 1 END,
                  "最后日" NULLS FIRST
                LIMIT 40
            """)
            watermarks = [dict(zip(wm["columns"], row)) for row in wm["rows"]]
        except Exception:
            watermarks = []

        return {
            "schema_count": len(schemas),
            "table_count": table_count,
            "size": db_size,
            "size_bytes": db_size_bytes,
            "schemas": schemas,
            "top_tables": top_tables,
            "watermarks": watermarks,
        }

    def get_table_schema(self, db_name: str, schema_name: str, table_name: str) -> list[dict]:
        full_name = f'"{schema_name}"."{table_name}"'
        r = self._execute(db_name, """
            SELECT a.attname as column_name,
                   format_type(a.atttypid, a.atttypmod) as data_type,
                   a.attnotnull as not_null,
                   col_description(a.attrelid, a.attnum) as description,
                   CASE WHEN a.attidentity = 'a' THEN 'YES' ELSE '' END as is_identity,
                   CASE WHEN ct.contype = 'p' THEN 'PK' ELSE '' END as is_pk
            FROM pg_catalog.pg_attribute a
            LEFT JOIN pg_catalog.pg_constraint ct
                ON ct.conrelid = a.attrelid AND ct.contype = 'p'
                AND a.attnum = ANY(ct.conkey)
            WHERE a.attrelid = %s::regclass
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
        """, (full_name,))
        return [dict(zip(r["columns"], row)) for row in r["rows"]]

    def get_indexes(self, db_name: str, schema_name: str, table_name: str) -> list[dict]:
        r = self._execute(db_name, """
            SELECT indexname, indexdef FROM pg_indexes
            WHERE tablename = %s AND schemaname = %s
            ORDER BY indexname
        """, (table_name, schema_name))
        return [dict(zip(r["columns"], row)) for row in r["rows"]]

    def get_foreign_keys(self, db_name: str, schema_name: str, table_name: str) -> list[dict]:
        r = self._execute(db_name, """
            SELECT tc.constraint_name, kcu.column_name,
                   ccu.table_name AS foreign_table_name,
                   ccu.column_name AS foreign_column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu
                ON tc.constraint_name = ccu.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_name = %s
              AND tc.table_schema = %s
        """, (table_name, schema_name))
        return [dict(zip(r["columns"], row)) for row in r["rows"]]

    def get_table_stats(self, db_name: str, schema_name: str, table_name: str, exact: bool = False) -> dict:
        full_name = f'"{schema_name}"."{table_name}"'
        info = {
            "row_count": 0,
            "row_estimate": True,
            "table_size": "",
            "table_size_data": "",
            "last_vacuum": None,
            "last_analyze": None,
        }
        try:
            r = self._execute(db_name, """
                SELECT COALESCE(s.n_live_tup, 0)::bigint,
                       pg_size_pretty(pg_total_relation_size(c.oid)),
                       pg_size_pretty(pg_table_size(c.oid)),
                       s.last_vacuum, s.last_autovacuum, s.last_analyze, s.last_autoanalyze
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
                WHERE n.nspname = %s AND c.relname = %s AND c.relkind = 'r'
            """, (schema_name, table_name))
            if r.get("rows") and r["rows"][0]:
                row = r["rows"][0]
                info["row_count"] = row[0]
                info["table_size"] = row[1]
                info["table_size_data"] = row[2]
                info["last_vacuum"] = str(row[3] or row[4] or "") or None
                info["last_analyze"] = str(row[5] or row[6] or "") or None
            if exact:
                r = self._execute(db_name, f"SELECT COUNT(*) FROM {full_name}")
                if r.get("rows"):
                    info["row_count"] = r["rows"][0][0]
                    info["row_estimate"] = False
        except Exception:
            pass
        return info

    @staticmethod
    def _validate_order_by(order_by: str | None) -> str | None:
        if not order_by or not order_by.strip():
            return None
        # 允许: col ASC/DESC, 多列逗号分隔；中文列名、括号等
        parts = [p.strip() for p in order_by.split(",")]
        cleaned = []
        for part in parts:
            m = re.match(
                r'^"?([\w\u4e00-\u9fff（）()\-./·]+)"?(?:\s+(ASC|DESC))?$',
                part,
                re.IGNORECASE,
            )
            if not m:
                raise ValueError(f"非法排序表达式: {part}")
            col, direction = m.group(1), (m.group(2) or "").upper()
            cleaned.append(f'"{col}"' + (f" {direction}" if direction else ""))
        return ", ".join(cleaned)

    def query_table(
        self,
        db_name: str,
        schema_name: str,
        table_name: str,
        limit: int = 100,
        offset: int = 0,
        order_by: str = None,
        where: str = None,
    ) -> dict:
        if not schema_name or not table_name:
            raise ValueError("schema 与 table 不能为空")
        limit = max(1, min(int(limit or 100), 1000))
        offset = max(0, int(offset or 0))
        full_name = f'"{schema_name}"."{table_name}"'
        order_sql = self._validate_order_by(order_by)

        # 无 WHERE 时用估算行数，避免大表 COUNT(*) 卡死
        if where:
            if ";" in where or "--" in where or "/*" in where:
                raise ValueError("WHERE 条件含非法字符")
            count_sql = f"SELECT COUNT(*) FROM {full_name} WHERE ({where})"
            r = self._execute(db_name, count_sql)
            total = r["rows"][0][0] if r.get("rows") else 0
            total_estimate = False
        else:
            r = self._execute(
                db_name,
                """
                SELECT COALESCE(s.n_live_tup, 0)::bigint
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
                WHERE n.nspname = %s AND c.relname = %s AND c.relkind = 'r'
                """,
                (schema_name, table_name),
            )
            total = r["rows"][0][0] if r.get("rows") else 0
            total_estimate = True

        sql = f"SELECT * FROM {full_name}"
        if where:
            sql += f" WHERE ({where})"
        if order_sql:
            sql += f" ORDER BY {order_sql}"
        sql += f" LIMIT {limit} OFFSET {offset}"

        r = self._execute(db_name, sql)
        cols = r.get("columns", [])
        clean_rows = [
            {cols[i]: _serialize(val) for i, val in enumerate(row)}
            for row in r.get("rows", [])
        ]
        return {
            "columns": cols,
            "rows": clean_rows,
            "total": total,
            "total_estimate": total_estimate,
            "limit": limit,
            "offset": offset,
            "schema": schema_name,
        }

    def execute_sql(self, db_name: str, sql: str, max_rows: int = 500) -> dict:
        sql = (sql or "").strip().rstrip(";")
        if not sql:
            raise ValueError("SQL 不能为空")
        if ";" in sql:
            raise ValueError("一次仅允许执行一条 SQL")
        max_rows = max(1, min(int(max_rows or 500), 2000))

        stripped = sql.lstrip().lower()
        # 只读：禁止增删改及 DDL
        forbidden = (
            "insert", "update", "delete", "merge", "upsert",
            "alter", "drop", "create", "truncate", "replace",
            "grant", "revoke", "comment", "vacuum", "analyze",
            "reindex", "cluster", "copy", "call", "do",
            "set", "reset", "discard", "lock", "listen", "notify",
        )
        first = stripped.split(None, 1)[0] if stripped else ""
        # WITH ... 可能跟写操作，额外检查
        if first in forbidden or any(
            re.search(rf"(^|;)\s*{kw}\b", stripped) for kw in (
                "insert", "update", "delete", "drop", "alter", "truncate", "create"
            )
        ):
            raise ValueError("当前为只读模式，仅允许 SELECT / WITH / SHOW / EXPLAIN 查询")

        is_select = first in {"select", "with", "show", "explain", "values", "table"}
        if not is_select:
            raise ValueError("当前为只读模式，仅允许 SELECT / WITH / SHOW / EXPLAIN 查询")

        wrapped = f"SELECT * FROM ({sql}) AS _q LIMIT {max_rows}"
        try:
            r = self._execute(db_name, wrapped)
        except Exception:
            r = self._execute(db_name, sql)
        cols = r.get("columns", [])
        clean_rows = [
            {cols[i]: _serialize(val) for i, val in enumerate(row)}
            for row in r.get("rows", [])
        ]
        return {
            "columns": cols,
            "rows": clean_rows,
            "type": "select",
            "row_count": len(clean_rows),
            "truncated": len(clean_rows) >= max_rows,
        }
