"""数据更新器基类 + 公共工具函数"""
from __future__ import annotations
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


# ── 写入统计(TASK-003 D2:区分"新增"与"覆盖修正")──
_insert_stats: dict[str, int] = {"插入": 0, "更新": 0, "跳过": 0}


def 重置写入统计() -> dict[str, int]:
    import copy

    snapshot = copy.deepcopy(_insert_stats)
    for k in _insert_stats:
        _insert_stats[k] = 0
    return snapshot


def 取写入统计() -> dict[str, int]:
    return dict(_insert_stats)


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

def _冲突策略() -> str:
    """写入冲突策略(TASK-003 D2)。

    历史行为恒为 `DO NOTHING` —— 意味着**数据源端的修正永远不会落地**
    (同一业务键第二次拉到的更正值被静默丢弃)。
    现支持两种策略,由 `config/数据库.yaml → 读表.冲突策略` 控制:

      更新(默认) —— `ON CONFLICT DO UPDATE`:冲突时用新值覆盖,源端修正能落地;
      忽略        —— 旧行为 `DO NOTHING`:冲突即丢弃(用于不愿被覆盖的数据集)。
    """
    try:
        from market_platform.core.配置 import 加载YAML
        from market_platform.core.路径 import 数据库配置

        raw = 加载YAML(数据库配置()) or {}
        读表 = raw.get("读表") or {}
        return str(读表.get("冲突策略", "更新")).strip()
    except Exception:  # noqa: BLE001 — 配置不可读时按安全默认
        return "更新"


_冲突目标缓存: dict[tuple[str, str], tuple[str, ...] | None] = {}


def _业务键唯一列(conn, schema: str, table: str) -> tuple[str, ...] | None:
    """找出该表**非主键**的唯一索引列(即业务键),作为 ON CONFLICT 目标。

    PostgreSQL 的 `ON CONFLICT DO UPDATE` 必须给出冲突目标(推断列或约束名),
    因此这里在运行时自动发现:
      · 优先取"非主键"的唯一索引 —— 那是业务键;
      · 只有代理主键(序号)而没有业务键唯一索引时返回 None,
        此时退回 `DO NOTHING`(缺业务键索引本身就是 D2 要补的债)。
    """
    键 = (schema, table)
    if 键 in _冲突目标缓存:
        return _冲突目标缓存[键]
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT array_agg(a.attname ORDER BY k.ord) AS cols
                FROM pg_index i
                JOIN pg_class c ON c.oid = i.indrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON true
                JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
                WHERE n.nspname = %s AND c.relname = %s
                  AND i.indisunique AND i.indisvalid AND NOT i.indisprimary
                  AND i.indpred IS NULL
                GROUP BY i.indexrelid
                ORDER BY count(*) ASC
                LIMIT 1
                """,
                (schema, table),
            )
            行 = cur.fetchone()
        列 = tuple(行[0]) if 行 and 行[0] else None
    except Exception:  # noqa: BLE001 — 查不到就退回 DO NOTHING
        列 = None
    _冲突目标缓存[键] = 列
    return 列


def _可更新列(filters) -> list[str]:
    """DO UPDATE 时允许被覆盖的列:排除代理键与时间戳列。"""
    排除 = {"序号", "updated_at", "创建时间", "登记时间"}
    return [c for c in filters if c not in 排除]


def _insert(conn, schema, table, col_vals, source="akshare"):
    """通用 INSERT,自动序号+source+updated_at;冲突策略见 `_冲突策略`。

    返回值仍为"是否发生写入"(插入或更新都为 True),但会顺带把
    (是否插入, 是否更新) 记到 `_insert_stats`,供调用方分别统计。
    """
    global _insert_stats
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

    策略 = _冲突策略()
    可更新 = _可更新列(filtered)
    目标 = _业务键唯一列(conn, schema, table) if 策略 == "更新" else None
    # 冲突目标必须是被插入列的子集,否则该索引对本次写入无意义
    if 目标 and not set(目标).issubset(set(filtered)):
        目标 = None
    if 目标 and 可更新:
        目标str = ", ".join(f'"{c}"' for c in 目标)
        赋值 = ", ".join(f'"{c}"=EXCLUDED."{c}"' for c in 可更新)
        # 用 xmax=0 判断本次是插入还是更新(PostgreSQL 惯用技巧)
        sql = (
            f'INSERT INTO "{schema}"."{table}" ({cols_str}) VALUES ({ph_str}) '
            f'ON CONFLICT ({目标str}) DO UPDATE SET {赋值} '
            f'RETURNING (xmax = 0) AS inserted'
        )
    else:
        sql = f'INSERT INTO "{schema}"."{table}" ({cols_str}) VALUES ({ph_str}) ON CONFLICT DO NOTHING'
    # 用 SAVEPOINT 包住:单条失败只回滚这一条,不炸掉调用方的整个事务。
    # 注意:autocommit 连接里没有事务块,SAVEPOINT 会报 NoActiveSqlTransaction ⇒ 需先判断。
    用保存点 = not bool(getattr(conn, "autocommit", False))
    if 用保存点:
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT _insert_sp")
    try:
        with conn.cursor() as cur:
            cur.execute(sql, db_vals)
            row = cur.fetchone() if cur.description else None
        if 用保存点:
            with conn.cursor() as cur:
                cur.execute("RELEASE SAVEPOINT _insert_sp")
        if row is None:
            _insert_stats["跳过"] += 1
            return False
        插入 = bool(row[0]) if isinstance(row, (tuple, list)) else True
        _insert_stats["插入" if 插入 else "更新"] += 1
        return True
    except Exception:
        if 用保存点:
            try:
                with conn.cursor() as cur:
                    cur.execute("ROLLBACK TO SAVEPOINT _insert_sp")
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
