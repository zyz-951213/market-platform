"""数据体检 —— 把"能悄悄污染研究结论"的数据缺陷变成可自动发现的检查项。

由来(2026-10-06 三起真实事故):
  ① **量纲混用**:`个股日行情文件.涨跌幅` 混了小数(CSMAR/akshare)与百分数(baostock),
     47 个交易日的增量行被放大 100 倍,顺着等权/市值加权口径把市场收益计算段
     算成 +110%(见 schema/迁移/015、016)。
  ② **numeric 'NaN'**:源数据 'NaN' 入库后沿 avg/sum **传播**,一个股票的异常派息
     让整个市场分段一整段变成 NaN(见 schema/迁移/013)。
  ③ **权重口径缺失**:增量段没有市值列时,市值加权整段为 NULL 却没人知道
     (见 schema/迁移/014 与 市场收益构建.权重口径)。

设计:每个检查项返回 (级别, 说明) 列表;级别取 `错误` / `警告` / `提示`。
默认只读,不做任何写入。CLI:`market-platform data 数据体检`。
"""

from __future__ import annotations

from typing import Any, Callable

# 日涨跌幅的合理区间(小数口径):日均绝对涨跌幅低于下界基本可判定量纲被放大了 100 倍,
# 高于上界说明该日由极少数异常值主导(新股首日、退市整理期等)。
涨跌幅下界 = 0.002
涨跌幅上界 = 0.12


def 检查涨跌幅量纲(天数: int = 400) -> list[tuple[str, str]]:
    """按日统计 avg(abs(pct_chg)),越界即报 —— 这是"单位混用"最灵敏的探针。"""
    import psycopg

    from market_platform.core.配置 import 数据库DSN

    with psycopg.connect(数据库DSN()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT trade_date, avg(abs(pct_chg)) AS 均绝对, max(abs(pct_chg)) AS 最大
            FROM "公共"."日行情"
            WHERE pct_chg IS NOT NULL
              AND trade_date > (SELECT max(trade_date) FROM "公共"."日行情") - %s::int
            GROUP BY 1 ORDER BY 1
            """,
            (天数,),
        )
        行 = cur.fetchall()

    问题: list[tuple[str, str]] = []
    高 = [(str(d), float(m)) for d, m, _ in 行 if m is not None and float(m) > 涨跌幅上界]
    零 = [str(d) for d, m, _ in 行 if m is not None and float(m) < 1e-9]
    if 高:
        # 量纲被放大 100 倍时,日均|涨跌幅| 会从 ~0.02 跳到 ~2(2026-07-27 起 47 天实际发生)
        样例 = ", ".join(f"{d}({m:.4f})" for d, m in 高[:5])
        问题.append(
            (
                "错误",
                f"{len(高)} 个交易日的日均|涨跌幅| > {涨跌幅上界} —— 疑似量纲被放大 100 倍"
                f"(或异常值主导): {样例}(修复: 迁移 015/016)",
            )
        )
    if 零:
        样例 = ", ".join(零[:5])
        问题.append(
            ("提示", f"{len(零)} 个交易日全部涨跌幅为 0(非交易日/节假日仍有行): {样例}")
        )
    return 问题


def 检查数值NaN(表列表: list[str] | None = None) -> list[tuple[str, str]]:
    """numeric 'NaN' 存量(`IS NULL` 查不出来,只能显式比 'NaN'::numeric)。"""
    from market_platform.data.存储.数值清洗 import 扫描数值NaN

    kwargs: dict[str, Any] = {"表列表": 表列表} if 表列表 else {}
    报告 = 扫描数值NaN(**kwargs)
    总计 = 报告["总计"]
    if not 总计["NaN行"]:
        return []
    样例 = "; ".join(
        f"{表}({', '.join(f'{c}×{n}' for c, n in 列.items())})"
        for 表, 列 in list(报告["明细"].items())[:3]
    )
    return [
        (
            "错误",
            f"{总计['命中表']} 表 / {总计['命中列']} 列 / {总计['NaN行']} 行含 numeric 'NaN' —— "
            f"聚合会把它传播出去: {样例}(修复: market-platform data 清洗NaN --apply)",
        )
    ]


def 检查市场收益计算段(最低覆盖: float = 0.90) -> list[tuple[str, str]]:
    """计算段:完整性(NaN/NULL)、权重口径分布、覆盖不足日。"""
    import psycopg

    from market_platform.core.配置 import 数据库DSN

    with psycopg.connect(数据库DSN()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*),
                   count(ret_ew), count(ret_float_vw), count(ret_total_vw), count(ret_ew_div),
                   count(*) FILTER (WHERE ret_ew = 'NaN'::numeric
                                      OR ret_float_vw = 'NaN'::numeric
                                      OR ret_total_vw = 'NaN'::numeric),
                   count(*) FILTER (WHERE 权重口径 = '股本结转'),
                   count(DISTINCT trade_date) FILTER (WHERE NOT 覆盖完整)
            FROM "公共"."市场收益文件" WHERE 数据段 = '计算'
            """
        )
        总, 等权, 流通, 总市值, div, nan数, 结转, 覆盖不足日 = cur.fetchone()

    问题: list[tuple[str, str]] = []
    if nan数:
        问题.append(("错误", f"计算段有 {nan数} 行 ret_* 为 numeric 'NaN'(聚合传播,必须清零)"))
    if 总 and (等权 != 总 or 流通 != 总 or 总市值 != 总 or div != 总):
        问题.append(
            (
                "警告",
                f"计算段列不完整: 行数 {总}, 等权 {等权}, 流通加权 {流通}, 总市值加权 {总市值}, "
                f"分红口径 {div}(市值缺口由 权重口径='股本结转' 回退重建)",
            )
        )
    if 结转:
        问题.append(("提示", f"计算段 {结转} 行的市值权重来自『股本结转』(当日市值列缺失,已用最近已知股本×当日收盘重建)"))
    if 覆盖不足日:
        问题.append(
            ("警告", f"{覆盖不足日} 个交易日覆盖度 < {最低覆盖:.0%}(当日股票数不足),其计算段不可与档案段直接比较")
        )
    return 问题


def 检查业务键唯一性() -> list[tuple[str, str]]:
    """可调度表是否都有"业务键唯一索引"。

    没有唯一索引时 `_insert` 只能 `ON CONFLICT DO NOTHING` —— 而 DO NOTHING 没有约束可冲突,
    等于"重复插入不报错":重跑或同批重复取数会把整行再写一遍
    (2026-10-06 实测:龙虎榜营业部 1810 行完全重复、其回填还因返回值误判而漏登记去重集合)。
    """
    import psycopg

    from market_platform.core.配置 import 数据库DSN

    with psycopg.connect(数据库DSN()) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT r."数据源" || '.' || r."数据集"
            FROM 行情增量.数据集登记 r
            WHERE r."可调度"
              AND NOT EXISTS (
                SELECT 1 FROM pg_index i
                  JOIN pg_class c ON c.oid = i.indrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = r."数据源" AND c.relname = r."数据集"
                   AND i.indisunique AND i.indnatts > 1)
            ORDER BY 1
            """
        )
        无键 = [r[0] for r in cur.fetchall()]

    if not 无键:
        return []
    return [
        (
            "警告",
            f"{len(无键)} 张可调度表没有业务键唯一索引(重跑可能重复写入整行): "
            + ", ".join(无键[:8])
            + ("…" if len(无键) > 8 else ""),
        )
    ]


def 检查日覆盖完整度(覆盖阈值: float = 0.25, 天数: int = 120) -> list[tuple[str, str]]:
    """可调度数据集的"每日行数"是否塌陷(残缺日)。

    由来:个股资金流向的缺口日里只有 5 行(参照 5200 行),而"当天有行"的判据
    会让缺口被误判为已补齐(2026-10-06 实际发生)。
    """
    import psycopg

    from market_platform.core.配置 import 数据库DSN

    问题: list[tuple[str, str]] = []
    with psycopg.connect(数据库DSN()) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT "数据源", "数据集", "水位列" FROM 行情增量.数据集登记
                WHERE "可调度" AND "水位列" IS NOT NULL ORDER BY 1, 2"""
        )
        登记 = cur.fetchall()

        for 数据源, 数据集, 水位列 in 登记:
            try:
                cur.execute(
                    f'SELECT "{水位列}"::date AS d, count(*) AS n FROM "{数据源}"."{数据集}" '
                    f'WHERE "{水位列}"::date > (SELECT max("{水位列}"::date) FROM "{数据源}"."{数据集}") - %s::int '
                    f'GROUP BY 1 ORDER BY 1',
                    (天数,),
                )
                每日 = [(r[0], int(r[1])) for r in cur.fetchall()]
            except Exception:
                conn.rollback()
                continue
            if len(每日) < 3:
                continue
            # 参照用 **p90** 而非最大值:龙虎榜这类"事件驱动"表每日行数天然波动(50–170 只),
            # 拿最大值当参照会误报;也不能用中位数 —— 有些表大多数日子本身就是残缺的
            # (个股资金流向近 180 天中位 57 行 vs 完整日 5213 行)。
            有序 = sorted(n for _, n in 每日)
            参照 = 有序[min(len(有序) - 1, int(len(有序) * 0.9))]
            if 参照 < 10:
                continue
            残缺 = [(d, n) for d, n in 每日 if n < 参照 * 覆盖阈值]
            if 残缺:
                样例 = ", ".join(f"{d}({n})" for d, n in 残缺[:3])
                问题.append(
                    (
                        "警告",
                        f"{数据源}.{数据集}: 近 {天数} 天里有 {len(残缺)} 个残缺日"
                        f"(< 参照 {参照} 行的 {覆盖阈值:.0%}): {样例}",
                    )
                )
    return 问题


检查项: dict[str, Callable[[], list[tuple[str, str]]]] = {
    "涨跌幅量纲": 检查涨跌幅量纲,
    "numeric NaN 存量": 检查数值NaN,
    "市场收益计算段": 检查市场收益计算段,
    "业务键唯一性": 检查业务键唯一性,
    "日覆盖完整度": 检查日覆盖完整度,
}


def 运行数据体检(仅: list[str] | None = None) -> dict[str, list[tuple[str, str]]]:
    报告: dict[str, list[tuple[str, str]]] = {}
    for 名, 函数 in 检查项.items():
        if 仅 and 名 not in 仅:
            continue
        try:
            报告[名] = 函数()
        except Exception as exc:  # 体检本身不能因为某一项失败而整体不可用
            报告[名] = [("错误", f"检查执行失败: {type(exc).__name__}: {exc}")]
    return 报告


def 打印数据体检(报告: dict[str, list[tuple[str, str]]], 输出: Any = print) -> int:
    """打印结果;只有存在『错误』时返回 1(『警告』『提示』仅展示,不算失败)。"""
    有错 = 0
    for 名, 条目 in 报告.items():
        if not 条目:
            输出(f"✓ {名}:正常")
            continue
        错误 = any(lv == "错误" for lv, _ in 条目)
        输出(f"{'✗' if 错误 else '⚠'} {名}:")
        for lv, m in 条目:
            输出(f"    [{lv}] {m}")
        有错 |= 1 if 错误 else 0
    return 有错


__all__ = [
    "运行数据体检",
    "打印数据体检",
    "检查涨跌幅量纲",
    "检查数值NaN",
    "检查市场收益计算段",
    "检查业务键唯一性",
    "检查日覆盖完整度",
    "涨跌幅下界",
    "涨跌幅上界",
]
