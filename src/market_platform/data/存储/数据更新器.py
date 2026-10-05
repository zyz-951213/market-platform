"""数据更新器基类 + 公共工具函数"""
from __future__ import annotations
from datetime import date, datetime
import psycopg

class DataUpdater:
    def __init__(self, db_name: str, dsn: str):
        self.db_name = db_name
        self.dsn = dsn

    def _get_conn(self):
        return psycopg.connect(self.dsn, connect_timeout=10, autocommit=True)

    def _execute(self, sql: str, params: tuple = None):
        conn = None
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                if params:
                    cur.execute(sql, params)
                else:
                    cur.execute(sql)
                if cur.description:
                    cols = [desc[0] for desc in cur.description]
                    return [dict(zip(cols, row)) for row in cur.fetchall()]
                return []
        finally:
            if conn and not conn.closed:
                conn.close()


# ── 公共工具函数 ──

def _next_id(conn, schema, table):
    with conn.cursor() as cur:
        cur.execute(f'SELECT COALESCE(MAX("序号"), 0) FROM "{schema}"."{table}"')
        return cur.fetchone()[0] + 1

def _to_n(v):
    if v is None or v == '' or v == '--' or v == '-': return None
    try: return float(v)
    except: return None

def _to_i(v):
    if v is None or v == '' or v == '--': return None
    try: return int(float(v))
    except: return None

def _parse_month(s):
    """2008年02月份 -> 2008-02-01"""
    if not s: return None
    s = str(s).replace("年", "-").replace("月", "").replace("月份", "").replace(" ", "").strip()
    parts = s.split("-")
    if len(parts) == 2 and len(parts[0]) == 4:
        return f"{parts[0]}-{parts[1].zfill(2)}-01"
    if len(s) >= 10:
        return s[:10]
    if len(s) >= 7:
        return s[:7] + "-01"
    return None

def _insert(conn, schema, table, col_vals, source="akshare"):
    """通用 INSERT ON CONFLICT DO NOTHING，自动序号+source+updated_at"""
    # 过滤掉值为 None 的字段（不写入 NULL 字段，让它们保持 DB 默认值）
    filtered = {k: v for k, v in col_vals.items() if v is not None and v != ""}
    if not filtered:
        return False

    nid = _next_id(conn, schema, table)
    filtered["序号"] = nid
    filtered["source"] = source
    filtered["updated_at"] = "NOW()"

    val_phs, db_vals = [], []
    for k, v in filtered.items():
        if v == "NOW()":
            val_phs.append("NOW()")
        else:
            val_phs.append("%s")
            db_vals.append(v)

    cols_str = ",".join([f'"{c.replace("%", "%%")}"' for c in filtered.keys()])
    ph_str = ",".join(val_phs)
    sql = f'INSERT INTO "{schema}"."{table}" ({cols_str}) VALUES ({ph_str}) ON CONFLICT DO NOTHING'
    try:
        with conn.cursor() as cur:
            cur.execute(sql, db_vals)
            return cur.rowcount > 0
    except Exception:
        # 避免后续 InFailedSqlTransaction（非 autocommit 或语句失败残留）
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def retry_call(fn, *, retries: int = 3, delay: float = 1.5, what: str = "接口"):
    """带退避的外部接口调用；全部失败则抛 RuntimeError。"""
    import time

    last = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(delay * (i + 1))
    raise RuntimeError(f"{what}失败(重试{retries}次): {type(last).__name__}: {last}")


def baostock_login_or_raise():
    """baostock 登录；失败抛错（含黑名单），禁止把登录失败当成功返回。"""
    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock登录失败[{lg.error_code}]: {lg.error_msg}")
    return bs

def _existing_set(conn, schema, table, key_col):
    """获取表已有值的集合"""
    with conn.cursor() as cur:
        cur.execute(f'SELECT DISTINCT "{key_col}"::text FROM "{schema}"."{table}"')
        return {str(r[0]) for r in cur.fetchall()}

def _existing_key_set(conn, schema, table, col1, col2=None):
    """获取表已有(主键)集合"""
    with conn.cursor() as cur:
        if col2:
            cur.execute(f'SELECT DISTINCT "{col1}"::text || \'|\' || "{col2}"::text FROM "{schema}"."{table}"')
        else:
            cur.execute(f'SELECT DISTINCT "{col1}"::text FROM "{schema}"."{table}"')
        return {str(r[0]) for r in cur.fetchall()}

def _existing_composite_set(conn, schema, table, cols):
    """多列联合去重"""
    with conn.cursor() as cur:
        sep = " || '|' || "
        parts = ["COALESCE(\"" + c + "\"::text, '')" for c in cols]
        expr = sep.join(parts)
        cur.execute(f'SELECT DISTINCT {expr} FROM "{schema}"."{table}"')
        return {str(r[0]) for r in cur.fetchall()}


def refresh_watermark(
    conn,
    *,
    source: str,
    dataset: str,
    schema: str,
    table: str,
    date_col: str,
    note: str = "",
) -> None:
    """刷新单表 行情增量.同步水位（更新脚本回写用）。"""
    rel = f'"{schema}"."{table}"'
    sql = f'''
        INSERT INTO "行情增量"."同步水位" (
          "数据源", "数据集", "最早日", "最后交易日", "行数", "水位列",
          "表体积字节", "备注", "更新时间", "状态"
        )
        SELECT
          %s, %s,
          MIN("{date_col}")::date,
          MAX("{date_col}")::date,
          COUNT(*),
          %s,
          pg_total_relation_size(%s::regclass),
          %s,
          now(),
          '正常'
        FROM {rel}
        ON CONFLICT ("数据源", "数据集") DO UPDATE SET
          "最早日" = EXCLUDED."最早日",
          "最后交易日" = EXCLUDED."最后交易日",
          "行数" = EXCLUDED."行数",
          "水位列" = EXCLUDED."水位列",
          "表体积字节" = EXCLUDED."表体积字节",
          "备注" = COALESCE(EXCLUDED."备注", "行情增量"."同步水位"."备注"),
          "更新时间" = now(),
          "状态" = EXCLUDED."状态"
    '''
    with conn.cursor() as cur:
        cur.execute(sql, (source, dataset, date_col, rel, note or None))


def get_watermark(conn, source: str, dataset: str):
    """读取水位；返回 dict 或 None。增量起点应优先用此值，勿各写各的 MAX。"""
    with conn.cursor() as cur:
        cur.execute(
            '''
            SELECT "数据源", "数据集", "最早日", "最后交易日", "行数",
                   "水位列", "责任方", "更新策略", "状态", "更新时间"
            FROM "行情增量"."同步水位"
            WHERE "数据源" = %s AND "数据集" = %s
            ''',
            (source, dataset),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d.name for d in cur.description]
        return dict(zip(cols, row))
