"""缺口定向回填 —— 读 `行情增量.缺口登记`,按数据集重拉缺失交易日。

背景(TASK-003 D4 衍生的第三件):
  更新器都只拉"最近窗口"(如同花顺即时、东财龙虎榜近 10 天),加上数据层此前**没有定时任务**,
  漏掉的日子永远不会被重试 ⇒ 缺口只增不减。

本模块把缺口登记变成**可执行**的待办:
  1. 读 `缺口登记` 里状态='待处理' 的区间;
  2. 按下表的"可回填级别"决定动作:
       · `区间可回填`  —— 数据源接口支持 start/end,直接按缺口区间重拉(龙虎榜、ETF两融);
       · `仅即时快照`  —— 数据源只提供当前快照,历史无法取回(龙虎榜营业部、ETF清单、个股资金流向)
                         ⇒ 标记为 `不可回填` 并写明原因,不再冒充"待处理";
  3. 回填后重新判断:区间内缺失日补全则置 `已修复`,否则保留 `待处理`。

用法:
    market-platform data 缺口回填 [--dry-run] [--only 数据集]
"""

from __future__ import annotations

from typing import Any

from market_platform.core.配置 import 数据库DSN

# 数据集 → 回填策略
#   区间  : 数据源支持按区间重拉(用对应的 设...回填区间 注入)
#   快照  : 数据源只有即时快照,历史不可回填
策略表: dict[str, dict[str, str]] = {
    "龙虎榜": {"级别": "区间", "说明": "东财 stock_lhb_detail_em 支持 start/end,可按缺口区间重拉"},
    "ETF融资融券交易明细表": {"级别": "区间", "说明": "上交所两融明细按日可查,可按缺口区间重拉"},
    # 2026-10-06 复审:此前两个数据集被判"仅即时快照",实际都有历史接口 ⇒ 升级为可回填
    "龙虎榜营业部": {"级别": "逐股按日", "说明": "stock_lhb_detail_em(按日) + stock_lhb_stock_detail_em(逐股营业部明细)"},
    "个股资金流向": {"级别": "逐股历史", "说明": "stock_individual_fund_flow 提供约 120 天逐股历史(约 5000 只,耗时长)"},
    "ETF基金申购赎回清单": {"级别": "快照", "说明": "fund_etf_spot_em 只有当前快照;交易所份额接口不含申赎清单 ⇒ 确属不可回填"},
}


def _待处理缺口(conn, only: str | None, 重试不可回填: bool = False) -> list[dict[str, Any]]:
    """待处理缺口;`重试不可回填=True` 时把状态='不可回填' 的也纳入(策略升级后重试用)。"""
    状态集 = "('待处理', '不可回填')" if 重试不可回填 else "('待处理')"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, 数据源, 数据集, 起始日, 结束日, 状态
            FROM 行情增量.缺口登记
            WHERE 状态 IN {状态集} AND (%s::text IS NULL OR 数据集 = %s::text)
            ORDER BY 数据集, 起始日
            """,
            (only, only),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def 回填缺口(*, dry_run: bool = False, only: str | None = None,
             重试不可回填: bool = False) -> dict[str, Any]:
    """按缺口登记执行定向回填;返回统计与明细。"""
    import psycopg

    统计: dict[str, Any] = {"区间回填": [], "不可回填": [], "已修复": 0, "仍待处理": 0}

    with psycopg.connect(数据库DSN(), autocommit=True) as conn:
        缺口列表 = _待处理缺口(conn, only, 重试不可回填)
        if not 缺口列表:
            return 统计

        from market_platform.data.存储 import 数据更新器_补充 as 补充
        from market_platform.data.存储.数据更新器 import DataUpdater

        updater = DataUpdater("金融市场数据", 数据库DSN())

        # 逐股历史型数据集:一次遍历全市场即可覆盖所有缺口段 ⇒ 先合并区间跑一遍,
        # 避免"每段缺口都遍历 5000 只"(11 段 × 5000 只 ≈ 7 小时,2026-10-06 实测)。
        逐股数据集 = {k for k, v in 策略表.items() if v["级别"] == "逐股历史"}
        for 数据集 in sorted({g["数据集"] for g in 缺口列表} & 逐股数据集):
            属于 = [g for g in 缺口列表 if g["数据集"] == 数据集]
            起 = min(str(g["起始日"]) for g in 属于)
            止 = max(str(g["结束日"]) for g in 属于)
            动作 = f"{数据集} {起}~{止}(合并 {len(属于)} 段)"
            缺口列表 = [g for g in 缺口列表 if g["数据集"] != 数据集]
            if dry_run:
                统计["区间回填"].append(动作 + f"(dry-run;合并了 {len(属于)} 段)")
                continue
            消息 = 补充.回填个股资金流向(updater, 起, 止)
            统计["区间回填"].append(f"{动作} → {消息}")
            for g in 属于:
                if _区间已补齐(conn, g["数据源"], 数据集, g["起始日"], g["结束日"]):
                    统计["已修复"] += 1
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE 行情增量.缺口登记 SET 状态=\'已修复\', 更新时间=now() WHERE id=%s",
                            (g["id"],),
                        )
                else:
                    统计["仍待处理"] += 1

        for 缺口 in 缺口列表:
            数据集 = 缺口["数据集"]
            策略 = 策略表.get(数据集)
            if 策略 is None:
                统计["不可回填"].append(f"{数据集} {缺口['起始日']}~{缺口['结束日']}(未定义策略)")
                if not dry_run:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE 行情增量.缺口登记
                               SET 状态='不可回填', 备注 = COALESCE(备注,'') || ' | 未定义回填策略', 更新时间=now()
                             WHERE id=%s
                            """,
                            (缺口["id"],),
                        )
                continue

            if 策略["级别"] == "快照":
                统计["不可回填"].append(f"{数据集} {缺口['起始日']}~{缺口['结束日']}({策略['说明']})")
                if not dry_run:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE 行情增量.缺口登记
                               SET 状态='不可回填',
                                   备注 = COALESCE(备注,'') || ' | ' || %s,
                                   更新时间=now()
                             WHERE id=%s
                            """,
                            (策略["说明"], 缺口["id"]),
                        )
                continue

            # 区间回填
            动作 = f"{数据集} {缺口['起始日']}~{缺口['结束日']}"
            if dry_run:
                统计["区间回填"].append(动作 + "(dry-run)")
                continue

            if 数据集 == "龙虎榜":
                补充.设龙虎榜回填区间(缺口["起始日"], 缺口["结束日"])
                try:
                    消息 = 补充.update_dragon_tiger(updater)
                finally:
                    补充.设龙虎榜回填区间(None, None)
            elif 数据集 == "ETF融资融券交易明细表":
                补充.设ETF两融回填区间(缺口["起始日"], 缺口["结束日"])
                try:
                    消息 = 补充.update_etf_margin(updater)
                finally:
                    补充.设ETF两融回填区间(None, None)
            elif 数据集 == "个股资金流向":
                消息 = 补充.回填个股资金流向(updater, str(缺口["起始日"]), str(缺口["结束日"]))
            elif 数据集 == "龙虎榜营业部":
                消息 = 补充.回填龙虎榜营业部(updater, str(缺口["起始日"]), str(缺口["结束日"]))
            else:
                统计["不可回填"].append(动作 + "(无实现)")
                continue

            统计["区间回填"].append(f"{动作} → {消息}")

            # 复判:该区间内是否已无缺失
            if _区间已补齐(conn, 缺口["数据源"], 数据集, 缺口["起始日"], 缺口["结束日"]):
                统计["已修复"] += 1
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE 行情增量.缺口登记 SET 状态='已修复', 更新时间=now() WHERE id=%s",
                        (缺口["id"],),
                    )
            else:
                统计["仍待处理"] += 1
    return 统计


def _区间已补齐(conn, 数据源: str, 数据集: str, 起, 止, 覆盖阈值: float = 0.5) -> bool:
    """区间内的应有交易日是否都**有足量**数据。

    注意"当天有没有行"是不够的:个股资金流向的缺口日里有几天只有 5 行
    (旧即时口径写入的残留),按"存在即补齐"会把这些残缺日误判为已修复
    (2026-10-06 实际发生)。这里改为与区间内**最大日行数**比较,低于阈值即视为未补齐。
    """
    from market_platform.data.脚本.扫描行情缺口 import _交易日集合

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r."水位列" FROM 行情增量.数据集登记 r
            WHERE r."数据源" = %s AND r."数据集" = %s
            """,
            (数据源, 数据集),
        )
        行 = cur.fetchone()
        if not 行 or not 行[0]:
            return False
        水位列 = 行[0]
        cur.execute(
            f'SELECT "{水位列}"::date, count(*) FROM "{数据源}"."{数据集}" '
            f'WHERE "{水位列}"::date BETWEEN %s AND %s GROUP BY 1',
            (起, 止),
        )
        每日 = {r[0]: int(r[1]) for r in cur.fetchall() if r[0] is not None}

    应有 = _交易日集合(conn, 起, 止)
    if not 应有 or not 每日:
        return False
    参照 = max(每日.values())
    # 阈值取"参照日行数 × 覆盖阈值";参照日太小(如全表只有个位数行)则不设门槛
    门槛 = 参照 * 覆盖阈值 if 参照 >= 10 else 0
    return all(每日.get(d, 0) >= 门槛 for d in 应有)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="按 行情增量.缺口登记 定向回填")
    parser.add_argument("--dry-run", action="store_true", help="只列出将执行的动作")
    parser.add_argument("--only", default=None, help="只处理某个数据集")
    parser.add_argument("--重试不可回填", action="store_true",
                        help="把此前标为『不可回填』的也纳入(策略升级后重试用)")
    args = parser.parse_args(argv)

    统计 = 回填缺口(dry_run=args.dry_run, only=args.only, 重试不可回填=args.重试不可回填)
    print(f"区间回填 {len(统计['区间回填'])} 项;不可回填 {len(统计['不可回填'])} 项;"
          f"已修复 {统计['已修复']} 条;仍待处理 {统计['仍待处理']} 条")
    for 行 in 统计["区间回填"]:
        print("  ·", 行)
    for 行 in 统计["不可回填"][:10]:
        print("  ×", 行)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
