"""行情缺口扫描 —— 把"数据集内部缺了哪些交易日"写进 `行情增量.缺口登记`。

背景(评审 D4):`行情增量.缺口登记` 此前只有一行人工登记,`src` 里**没有任何读写代码**,
缺口靠零散的 ad-hoc SQL 与 `update_daily_bars` 内嵌查询发现 ⇒ 机制形同空转。

本模块补齐这条链路:
    扫描缺口(域=None) → 逐个已登记数据集(有水位列且可调度)对比 A 股交易日历
                      → 把连续缺失交易日聚成区间,幂等写入 缺口登记(状态='待处理')
                      → 复扫时已修复的缺口置为 '已修复'

判定口径:
  · 交易日来源 = `公共.日历文件`(与调度一致,而非 `公共.交易日历`);
  · 只在 [最早日, 最后日] 区间内找缺口(区间外不算缺口,避免把未开始/已停更算进来);
  · 允许 `允许缺失天数` 容差:区间长度 ≤ 容差的不登记(默认 0,即任何缺失都登记)。

用法:
    market-platform data 扫描行情缺口
"""

from __future__ import annotations

from datetime import date
from typing import Any

from market_platform.core.配置 import 数据库DSN


def _交易日集合(conn, 起: date, 止: date) -> set[date]:
    """A 股开市日(与调度同源:`公共.日历文件`)。

    注意日历表里有两套**互补**的口径(2026-10-06 查实):
      · `市场类型='上证A股'/'深证A股'` —— 历史段(1990 → 2026-07-25);
      · `市场类型='A股'`               —— 当前/未来段(2026-07-26 起)。
    只取其中一个会让另一段的交易日集合为空:历史区间扫不出缺口、近期区间判不出补齐
    (2026-10-06 之前只用了 'A股',历史区间的 `应有交易日` 恒为空)。
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT "日历日期"
            FROM "公共"."日历文件"
            WHERE "市场类型" IN ('A股', '上证A股', '深证A股')
              AND ("开市情况" = '1' OR "开市情况" = '开市')
              AND "日历日期" BETWEEN %s AND %s
            """,
            (起, 止),
        )
        return {r[0] for r in cur.fetchall() if r[0] is not None}


def _登记数据集(conn, 域: str | None) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r."数据源", r."数据集", r."水位列", r."更新策略", COALESCE(r."可调度", false),
                   COALESCE(r."是否启用", true)
            FROM "行情增量"."数据集登记" r
            WHERE r."水位列" IS NOT NULL AND r."水位列" <> ''
              AND r."期望锚点" = '交易日'          -- 只扫"应有每个交易日"的序列(月频/季频不适用)
              AND (%s::text IS NULL OR r."责任方" = %s::text)
            ORDER BY r."数据源", r."数据集"
            """,
            (域, 域),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _实测区间(conn, 数据源: str, 数据集: str, 水位列: str) -> tuple[date | None, date | None, int]:
    """返回 (最早日, 最后日, 行数);对象不是物理表时返回 (None, None, 0)。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT (SELECT 最早日 FROM 行情增量.同步水位 WHERE 数据源=%s AND 数据集=%s),
                   (SELECT 最后交易日 FROM 行情增量.同步水位 WHERE 数据源=%s AND 数据集=%s)
            """,
            (数据源, 数据集, 数据源, 数据集),
        )
        行 = cur.fetchone()
    if not 行:
        return None, None, 0
    return 行[0], 行[1], 0


# 登记表里的"责任方"沿用旧英文代号(database 等),这里做一次别名映射
责任方别名 = {
    "数据底座": "database",
    "database": "database",
    "行为分析": "行为分析",
    "指数预测": "指数预测",
    "全部": None,
    "all": None,
}


def 扫描缺口(*, 域: str | None = None, 允许缺失天数: int = 0, 上限: int = 500) -> dict[str, Any]:
    """扫描已登记数据集的交易日缺口,幂等写入 `行情增量.缺口登记`。

    返回统计:{"扫描数据集": n, "新登记缺口": m, "已修复": k, "明细": [...]}
    """
    import psycopg

    统计: dict[str, Any] = {
        "扫描数据集": 0, "新登记缺口": 0, "已存在": 0, "已修复": 0, "跳过": 0, "明细": []
    }
    with psycopg.connect(数据库DSN(), autocommit=True) as conn:
        数据集列表 = _登记数据集(conn, 域)
        统计["扫描数据集"] = len(数据集列表)

        for 项 in 数据集列表:
            数据源, 数据集, 水位列 = 项["数据源"], 项["数据集"], 项["水位列"]
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = %s AND c.relname = %s
                    """,
                    (数据源, 数据集),
                )
                行 = cur.fetchone()
            if not 行 or 行[0] != "r":
                统计["跳过"] += 1
                continue  # 视图/逻辑接口不适合按日期逐日核对

            最早, 最后, _ = _实测区间(conn, 数据源, 数据集, 水位列)
            if 最早 is None or 最后 is None or 最早 >= 最后:
                统计["跳过"] += 1
                continue

            应有 = sorted(_交易日集合(conn, 最早, 最后))
            with conn.cursor() as cur:
                cur.execute(
                    f'SELECT DISTINCT "{水位列}"::date FROM "{数据源}"."{数据集}" '
                    f'WHERE "{水位列}"::date BETWEEN %s AND %s',
                    (最早, 最后),
                )
                实有 = {r[0] for r in cur.fetchall() if r[0] is not None}

            缺 = [d for d in 应有 if d not in 实有]
            if not 缺:
                # 该数据集已无缺口:把历史登记标记为已修复
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE 行情增量.缺口登记
                           SET 状态='已修复', 更新时间=now()
                         WHERE 数据源=%s AND 数据集=%s AND 状态='待处理'
                        """,
                        (数据源, 数据集),
                    )
                    统计["已修复"] += cur.rowcount or 0
                continue

            # 聚成连续区间
            区间: list[list[date]] = []
            for d in 缺:
                if 区间 and (d - 区间[-1][1]).days <= 1:
                    区间[-1][1] = d
                else:
                    区间.append([d, d])

            for 起, 止 in 区间[:上限]:
                长度 = (止 - 起).days + 1
                if 长度 <= 允许缺失天数:
                    continue
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO 行情增量.缺口登记
                          (数据源, 数据集, 起始日, 结束日, 状态, 备注)
                        VALUES (%s, %s, %s, %s, '待处理', %s)
                        ON CONFLICT (数据源, 数据集, 起始日, 结束日) DO UPDATE
                          SET 状态 = CASE WHEN 行情增量.缺口登记.状态 = '已修复' THEN '待处理'
                                          ELSE 行情增量.缺口登记.状态 END,
                              备注 = EXCLUDED.备注,
                              更新时间 = now()
                        RETURNING (xmax = 0) AS 新增
                        """,
                        (数据源, 数据集, 起, 止, f"自动扫描:{len(缺)} 个交易日缺失,本区间 {长度} 天"),
                    )
                    行结果 = cur.fetchone()
                    if 行结果 and 行结果[0]:
                        统计["新登记缺口"] += 1
                        统计["明细"].append(f"{数据源}.{数据集} {起}~{止}({长度}天)")
                    else:
                        统计["已存在"] += 1
    return 统计


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="扫描行情缺口并登记")
    parser.add_argument("--域", default="全部", help="责任方:全部|数据底座(database)|行为分析|指数预测")
    parser.add_argument("--允许缺失天数", type=int, default=0, help="长度不超过该值的缺口不登记")
    args = parser.parse_args(argv)

    域 = 责任方别名.get(str(args.域).strip(), args.域) or None
    统计 = 扫描缺口(域=域, 允许缺失天数=args.允许缺失天数)
    print(f"扫描数据集 {统计['扫描数据集']} 个;新登记缺口 {统计['新登记缺口']} 条;"
          f"已存在 {统计['已存在']} 条;标记已修复 {统计['已修复']} 条;"
          f"跳过(视图/无区间) {统计['跳过']} 个")
    for 条 in 统计["明细"][:20]:
        print("  -", 条)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
