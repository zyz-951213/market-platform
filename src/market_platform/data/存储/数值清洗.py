"""数值列 NaN 体检与清洗。

背景(真实事故):源数据里的 `'NaN'` 字符串经 `float()` 变成 `float('nan')`,
psycopg 会把它作为 PostgreSQL `numeric` 的**特殊值 `NaN`** 写入。`NaN` 不是 NULL,
`avg`/`sum` 等聚合会把它**传播**出去,于是"一个股票的异常派息"能把整个市场分段
一整段区间的收益率全变成 NaN(2026-05 深A/创业板/科创板即如此)。

`IS NULL` 查不出它,`count(col)` 也照样计数 ⇒ 必须显式用 `col = 'NaN'::numeric` 判定。

用法:
    扫描: 市场平台 data 清洗NaN                 # 只报告,不写库
    清理: 市场平台 data 清洗NaN --apply         # NaN → NULL,并留整表备份
    限表: ... --表 公共.分配文件 --表 公共.日回报文件
    限模式: ... --模式 公共 --模式 行情增量

安全:默认 `--模式 公共 行情增量`(原始层);清洗前对命中的表建
`<表名>_NaN清洗备份_<日期>` 快照表,可回滚。
"""

from __future__ import annotations

import sys
from typing import Any, Iterable

# 默认不扫的巨表(单表 GB 级,全表顺序扫描代价过高;如需扫用 --表 显式指定)
默认跳过: set[str] = set()

_跳过表名片段 = ("清洗备份", "_bak", "_backup")


def _数值列(conn, 模式: str, 跳过: set[str]) -> list[tuple[str, str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_schema, table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND data_type = 'numeric'
            ORDER BY table_name, ordinal_position
            """,
            (模式,),
        )
        行 = cur.fetchall()
    结果 = []
    for 模式名, 表名, 列名 in 行:
        if any(s in 表名 for s in _跳过表名片段):
            continue
        if 表名 in 跳过:
            continue
        结果.append((模式名, 表名, 列名))
    return 结果


def 扫描数值NaN(
    *,
    模式列表: Iterable[str] = ("公共", "行情增量"),
    表列表: Iterable[str] | None = None,
    跳过表: Iterable[str] | None = None,
) -> dict[str, Any]:
    """逐表一次扫描,返回 {表: {列: NaN 行数}}。每表只做一次顺序扫描。"""
    import psycopg

    from market_platform.core.配置 import 数据库DSN

    指定表 = {t.split(".")[-1] for t in 表列表} if 表列表 else None
    指定模式 = {t.split(".")[0] for t in 表列表} if 表列表 else set(模式列表)
    跳过 = set(跳过表 or ()) | 默认跳过

    结果: dict[str, Any] = {}
    总计 = {"表数": 0, "命中表": 0, "命中列": 0, "NaN行": 0}

    with psycopg.connect(数据库DSN()) as conn:
        分组: dict[tuple[str, str], list[str]] = {}
        for 模式 in sorted(指定模式):
            for 模式名, 表名, 列名 in _数值列(conn, 模式, 跳过):
                if 指定表 is not None and 表名 not in 指定表:
                    continue
                分组.setdefault((模式名, 表名), []).append(列名)

        for (模式名, 表名), 列列表 in 分组.items():
            总计["表数"] += 1
            选择 = ", ".join(
                f'count(*) FILTER (WHERE "{c}" = \'NaN\'::numeric) AS "{i}"'
                for i, c in enumerate(列列表)
            )
            with conn.cursor() as cur:
                cur.execute(f'SELECT {选择} FROM "{模式名}"."{表名}"')
                行 = cur.fetchone()
            命中 = {列列表[i]: int(v or 0) for i, v in enumerate(行) if v}
            if 命中:
                全名 = f"{模式名}.{表名}"
                结果[全名] = 命中
                总计["命中表"] += 1
                总计["命中列"] += len(命中)
                总计["NaN行"] += sum(命中.values())

    return {"总计": 总计, "明细": 结果}


def 清洗数值NaN(
    *,
    模式列表: Iterable[str] = ("公共", "行情增量"),
    表列表: Iterable[str] | None = None,
    跳过表: Iterable[str] | None = None,
    备份: bool = True,
) -> dict[str, Any]:
    """把命中列的 `'NaN'::numeric` 置为 NULL。备份表名带日期,可整表回滚。"""
    from datetime import date

    import psycopg

    from market_platform.core.配置 import 数据库DSN

    报告 = 扫描数值NaN(模式列表=模式列表, 表列表=表列表, 跳过表=跳过表)
    总清洗 = 0
    已备份: list[str] = []

    if not 报告["明细"]:
        return {"清洗": 报告, "置空行数": 0, "备份表": []}

    日期 = date.today().strftime("%Y%m%d")
    with psycopg.connect(数据库DSN()) as conn:
        conn.autocommit = False
        for 全名, 命中 in 报告["明细"].items():
            模式名, 表名 = 全名.split(".", 1)
            条件 = " OR ".join(f'"{c}" = \'NaN\'::numeric' for c in 命中)
            if 备份:
                备份名 = f"{表名}_NaN清洗备份_{日期}"
                with conn.cursor() as cur:
                    cur.execute(
                        f'CREATE TABLE IF NOT EXISTS "{模式名}"."{备份名}" AS '
                        f'SELECT * FROM "{模式名}"."{表名}" WHERE {条件}'
                    )
                已备份.append(f"{模式名}.{备份名}")
            with conn.cursor() as cur:
                列赋值 = ", ".join(f'"{c}" = NULL' for c in 命中)
                cur.execute(
                    f'UPDATE "{模式名}"."{表名}" SET {列赋值} WHERE {条件}'
                )
                总清洗 += cur.rowcount or 0
        conn.commit()

    return {"清洗": 报告, "置空行数": 总清洗, "备份表": 已备份}


def 格式化报告(报告: dict[str, Any]) -> str:
    行 = []
    总计 = 报告["总计"]
    行.append(
        f"扫描 {总计['表数']} 张表: 命中 {总计['命中表']} 表 / "
        f"{总计['命中列']} 列 / {总计['NaN行']} 行含 numeric 'NaN'"
    )
    for 全名, 命中 in sorted(报告["明细"].items(), key=lambda kv: -sum(kv[1].values())):
        列述 = ", ".join(f"{c}={n}" for c, n in sorted(命中.items(), key=lambda kv: -kv[1]))
        行.append(f"  {全名}: {列述}")
    if not 报告["明细"]:
        行.append("  (无命中)")
    return "\n".join(行)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    apply_ = "--apply" in argv or "--应用" in argv
    表列表 = [argv[i + 1] for i, a in enumerate(argv) if a in ("--表",) and i + 1 < len(argv)]
    模式列表 = [argv[i + 1] for i, a in enumerate(argv) if a in ("--模式",) and i + 1 < len(argv)]
    跳过表 = [argv[i + 1] for i, a in enumerate(argv) if a in ("--跳过表",) and i + 1 < len(argv)]
    kwargs: dict[str, Any] = {"跳过表": 跳过表} if 跳过表 else {}
    if 表列表:
        kwargs["表列表"] = 表列表
    if 模式列表:
        kwargs["模式列表"] = 模式列表

    if not apply_:
        print(格式化报告(扫描数值NaN(**kwargs)))
        print("\n(只读体检;加 --apply 执行 NaN → NULL 清洗)")
        return 0

    结果 = 清洗数值NaN(**kwargs)
    print(格式化报告(结果["清洗"]))
    print(f"\n已置空 {结果['置空行数']} 行;备份表: {', '.join(结果['备份表']) or '(无)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
