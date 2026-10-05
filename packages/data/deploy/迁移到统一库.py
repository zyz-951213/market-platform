#!/usr/bin/env python3
"""[历史] 将 market_agents / market_predictor 迁入「金融市场数据」中文 schema。

警告：三层收敛后目标表已变（主数据→公共，标的时序→窄表）。
本脚本仅作历史参考，勿对当前生产库再执行。

  python3 deploy/迁移到统一库.py
  python3 deploy/迁移到统一库.py --only 行为分析
  python3 deploy/迁移到统一库.py --only 指数预测
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

目标库 = "postgresql:///金融市场数据"
源_行为 = "postgresql:///market_agents"
源_预测 = "postgresql:///market_predictor"

DDL = Path(__file__).resolve().parents[1] / "schema" / "统一库.sql"

序列类型映射 = {
    "minute_daily_features": "分钟日特征",
    "agent_activity": "参与者活跃度",
    "daily_feature_matrix": "日特征矩阵",
    "state_features": "状态特征",
    "minute_bars": "分钟行情",
}
产物类型映射 = {
    "agent_patterns": "参与者模式",
    "agent_pattern_labels": "参与者标签",
    "agent_names": "参与者名称",
    "optimized_positions": "优化仓位",
    "transition_matrix": "转移矩阵",
    "next_state_probs": "下一状态概率",
}


def _exec_sql_file(conn: psycopg.Connection, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def _copy_query(src: psycopg.Connection, dst: psycopg.Connection, select_sql: str, copy_into: str) -> int:
    """流式 COPY：用两个 psql 进程管道传输，不占 /tmp，且不跨线程复用连接。"""
    import subprocess

    del src, dst  # 管道方式独立建连
    # 从 DSN 解析库名：postgresql:///name
    def _db(dsn: str) -> str:
        return dsn.rsplit("/", 1)[-1]

    # 调用方当前迁移方向固定：market_* → 金融市场数据；由环境变量可覆盖
    # 这里根据 copy_into 目标 schema 判断源库
    if copy_into.startswith("COPY 指数预测") or "指数预测." in copy_into:
        src_db = _db(源_预测)
    else:
        src_db = _db(源_行为)
    dst_db = _db(目标库)

    produce = subprocess.Popen(
        ["psql", "-d", src_db, "-v", "ON_ERROR_STOP=1", "-c", f"COPY ({select_sql}) TO STDOUT"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert produce.stdout is not None
    consume = subprocess.Popen(
        ["psql", "-d", dst_db, "-v", "ON_ERROR_STOP=1", "-c", copy_into],
        stdin=produce.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    produce.stdout.close()
    out, err2 = consume.communicate()
    _, err1 = produce.communicate()
    if produce.returncode != 0:
        raise RuntimeError(f"源 COPY 失败: {err1.decode('utf-8', errors='replace')}")
    if consume.returncode != 0:
        raise RuntimeError(f"目标 COPY 失败: {err2.decode('utf-8', errors='replace')}")
    # 管道模式不便精确字节数，返回 0 表示成功
    return 0


def _truncate(dst: psycopg.Connection, table: str) -> None:
    with dst.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {table} CASCADE")
    dst.commit()


def _count(conn: psycopg.Connection, sql: str) -> int:
    with conn.cursor() as cur:
        cur.execute(sql)
        return int(cur.fetchone()[0])


def migrate_agents_remainder(dst: psycopg.Connection) -> None:
    """续传：标的时序 / 标的产物（前面小表已迁完）。"""
    print("=== 续传行为分析：标的时序 / 标的产物 ===")
    with psycopg.connect(源_行为) as src:
        print("→ 行为分析.标的时序（含类型中文化）")
        _truncate(dst, "行为分析.标的时序")
        cases = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in 序列类型映射.items())
        select_sql = f"""
            SELECT symbol,
                   CASE series_type {cases} ELSE series_type END,
                   date, payload
            FROM symbol_series
        """
        nbytes = _copy_query(
            src,
            dst,
            select_sql,
            "COPY 行为分析.标的时序 (证券代码, 序列类型, 交易日期, 载荷) FROM STDIN",
        )
        print(f"  字节≈{nbytes:,} 行数={_count(dst, 'SELECT COUNT(*) FROM 行为分析.标的时序')}")

        print("→ 行为分析.标的产物（含类型中文化）")
        _truncate(dst, "行为分析.标的产物")
        cases = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in 产物类型映射.items())
        select_sql = f"""
            SELECT symbol,
                   CASE artifact_type {cases} ELSE artifact_type END,
                   payload, updated_at
            FROM symbol_artifacts
        """
        nbytes = _copy_query(
            src,
            dst,
            select_sql,
            "COPY 行为分析.标的产物 (证券代码, 产物类型, 载荷, 更新时间) FROM STDIN",
        )
        print(f"  字节≈{nbytes:,} 行数={_count(dst, 'SELECT COUNT(*) FROM 行为分析.标的产物')}")


def migrate_agents(dst: psycopg.Connection) -> None:
    print("=== 迁移行为分析（market_agents）===")
    with psycopg.connect(源_行为) as src:
        # 先清空子表，再清空档案，避免 CASCADE 误伤未拷数据
        for table in (
            "行为分析.日行情",
            "行为分析.资金流向",
            "行为分析.策略净值",
            "行为分析.标的时序",
            "行为分析.标的产物",
            "行为分析.标的摘要",
            "行为分析.行业摘要",
            "行为分析.市场时序",
            "行为分析.组合净值",
            "行为分析.组合",
            "行为分析.用户",
            "行为分析.应用状态",
            "主数据.证券档案",
        ):
            print(f"  truncate {table}")
            _truncate(dst, table)

        print("→ 主数据.证券档案")
        _copy_query(
            src,
            dst,
            """
            SELECT symbol, name, market, sector, sector_l1, sector_l2, sector_l3,
                   data_path, output_path, updated_at
            FROM symbols
            """,
            "COPY 主数据.证券档案 (证券代码, 证券简称, 交易市场, 所属行业, 一级行业, 二级行业, 三级行业, 数据路径, 输出路径, 更新时间) FROM STDIN",
        )
        print("  行数", _count(dst, "SELECT COUNT(*) FROM 主数据.证券档案"))

        jobs = [
            (
                "行为分析.日行情",
                "SELECT symbol, date, open, high, low, close, volume, amount FROM daily_bars",
                "COPY 行为分析.日行情 (证券代码, 交易日期, 开盘价, 最高价, 最低价, 收盘价, 成交量, 成交额) FROM STDIN",
            ),
            (
                "行为分析.资金流向",
                """
                SELECT symbol, date, close, pct_change, main_net_inflow, main_net_ratio,
                       super_large_net_inflow, super_large_net_ratio, large_net_inflow, large_net_ratio,
                       medium_net_inflow, medium_net_ratio, small_net_inflow, small_net_ratio
                FROM fund_flow
                """,
                "COPY 行为分析.资金流向 (证券代码, 交易日期, 收盘价, 涨跌幅, 主力净流入, 主力净占比, 超大单净流入, 超大单净占比, 大单净流入, 大单净占比, 中单净流入, 中单净占比, 小单净流入, 小单净占比) FROM STDIN",
            ),
            (
                "行为分析.策略净值",
                """
                SELECT symbol, date, suggested_position, market_state, state_score, market_return,
                       position, position_change, strategy_return, benchmark_return,
                       strategy_equity, benchmark_equity
                FROM equity_curves
                """,
                "COPY 行为分析.策略净值 (证券代码, 交易日期, 建议仓位, 市场状态, 状态得分, 市场收益率, 仓位, 仓位变动, 策略收益率, 基准收益率, 策略净值, 基准净值) FROM STDIN",
            ),
            (
                "行为分析.标的摘要",
                """
                SELECT symbol, sector, name, latest_state, suggested_position, strategy_return,
                       benchmark_return, excess_return, strategy_sharpe, anomalies, status,
                       output_path, analyzed_at
                FROM symbol_summary
                """,
                "COPY 行为分析.标的摘要 (证券代码, 所属行业, 证券简称, 最新状态, 建议仓位, 策略收益率, 基准收益率, 超额收益率, 策略夏普, 异常数, 状态, 输出路径, 分析时间) FROM STDIN",
            ),
            (
                "行为分析.行业摘要",
                """
                SELECT sector, n_symbols, mean_excess_return, median_excess_return, win_rate,
                       mean_strategy_return, mean_benchmark_return, mean_strategy_sharpe,
                       mean_suggested_position, updated_at
                FROM industry_summary
                """,
                "COPY 行为分析.行业摘要 (所属行业, 标的数量, 平均超额收益率, 中位超额收益率, 胜率, 平均策略收益率, 平均基准收益率, 平均策略夏普, 平均建议仓位, 更新时间) FROM STDIN",
            ),
            (
                "行为分析.市场时序",
                "SELECT series_name, date, payload FROM market_series",
                "COPY 行为分析.市场时序 (序列名称, 交易日期, 载荷) FROM STDIN",
            ),
            (
                "行为分析.组合",
                "SELECT portfolio_id, name, metadata, created_at FROM portfolios",
                "COPY 行为分析.组合 (组合编号, 组合名称, 元数据, 创建时间) FROM STDIN",
            ),
            (
                "行为分析.组合净值",
                """
                SELECT portfolio_id, date, portfolio_equity, benchmark_equity,
                       portfolio_return, benchmark_return, portfolio_position
                FROM portfolio_equity
                """,
                "COPY 行为分析.组合净值 (组合编号, 交易日期, 组合净值, 基准净值, 组合收益率, 基准收益率, 组合仓位) FROM STDIN",
            ),
            (
                "行为分析.应用状态",
                "SELECT key, payload, updated_at FROM app_state",
                "COPY 行为分析.应用状态 (键, 载荷, 更新时间) FROM STDIN",
            ),
        ]

        try:
            n_users = _count(src, "SELECT COUNT(*) FROM users")
        except Exception:
            n_users = 0
        if n_users:
            jobs.append(
                (
                    "行为分析.用户",
                    "SELECT id, username, email, hashed_password, is_active, created_at, updated_at FROM users",
                    "COPY 行为分析.用户 (编号, 用户名, 邮箱, 密码哈希, 是否启用, 创建时间, 更新时间) FROM STDIN",
                )
            )

        for table, select_sql, copy_into in jobs:
            print(f"→ {table}")
            nbytes = _copy_query(src, dst, select_sql, copy_into)
            print(f"  字节≈{nbytes:,} 行数={_count(dst, f'SELECT COUNT(*) FROM {table}')}")

        print("→ 行为分析.标的时序（含类型中文化）")
        cases = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in 序列类型映射.items())
        select_sql = f"""
            SELECT symbol,
                   CASE series_type {cases} ELSE series_type END,
                   date, payload
            FROM symbol_series
        """
        nbytes = _copy_query(
            src,
            dst,
            select_sql,
            "COPY 行为分析.标的时序 (证券代码, 序列类型, 交易日期, 载荷) FROM STDIN",
        )
        print(f"  字节≈{nbytes:,} 行数={_count(dst, 'SELECT COUNT(*) FROM 行为分析.标的时序')}")

        print("→ 行为分析.标的产物（含类型中文化）")
        cases = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in 产物类型映射.items())
        select_sql = f"""
            SELECT symbol,
                   CASE artifact_type {cases} ELSE artifact_type END,
                   payload, updated_at
            FROM symbol_artifacts
        """
        nbytes = _copy_query(
            src,
            dst,
            select_sql,
            "COPY 行为分析.标的产物 (证券代码, 产物类型, 载荷, 更新时间) FROM STDIN",
        )
        print(f"  字节≈{nbytes:,} 行数={_count(dst, 'SELECT COUNT(*) FROM 行为分析.标的产物')}")


def migrate_predictor(dst: psycopg.Connection) -> None:
    print("=== 迁移指数预测（market_predictor）===")
    with psycopg.connect(源_预测) as src:
        jobs = [
            (
                "指数预测.指数特征",
                "SELECT index_code, date, payload FROM index_features",
                "COPY 指数预测.指数特征 (指数代码, 交易日期, 载荷) FROM STDIN",
            ),
            (
                "指数预测.时序数据",
                "SELECT dataset_key, date, payload FROM time_series",
                "COPY 指数预测.时序数据 (数据集键, 交易日期, 载荷) FROM STDIN",
            ),
            (
                "指数预测.数据集快照",
                "SELECT dataset_key, records, updated_at FROM dataset_snapshots",
                "COPY 指数预测.数据集快照 (数据集键, 记录, 更新时间) FROM STDIN",
            ),
            (
                "指数预测.大模型缓存",
                "SELECT model, prompt_version, prompt_digest, bar_date, response, created_at FROM llm_cache",
                "COPY 指数预测.大模型缓存 (模型, 提示词版本, 提示词摘要, 行情日期, 响应, 创建时间) FROM STDIN",
            ),
            (
                "指数预测.回测记录",
                """
                SELECT run_id, is_latest, model_name, bar_freq, strategy_fingerprint,
                       data_as_of, saved_at, metrics, classification
                FROM backtest_runs
                """,
                "COPY 指数预测.回测记录 (运行编号, 是否最新, 模型名称, 行情频率, 策略指纹, 数据截至, 保存时间, 指标, 分类) FROM STDIN",
            ),
            (
                "指数预测.回测信号",
                "SELECT run_id, date, payload FROM backtest_signals",
                "COPY 指数预测.回测信号 (运行编号, 交易日期, 载荷) FROM STDIN",
            ),
            (
                "指数预测.拉取元数据",
                "SELECT dataset_key, payload, updated_at FROM fetch_meta",
                "COPY 指数预测.拉取元数据 (数据集键, 载荷, 更新时间) FROM STDIN",
            ),
            (
                "指数预测.应用状态",
                "SELECT key, payload, updated_at FROM app_state",
                "COPY 指数预测.应用状态 (键, 载荷, 更新时间) FROM STDIN",
            ),
        ]
        # 回测信号依赖回测记录：先记录
        order = [
            "指数预测.回测记录",
            "指数预测.回测信号",
            "指数预测.指数特征",
            "指数预测.时序数据",
            "指数预测.数据集快照",
            "指数预测.大模型缓存",
            "指数预测.拉取元数据",
            "指数预测.应用状态",
        ]
        by_name = {j[0]: j for j in jobs}
        for name in order:
            table, select_sql, copy_into = by_name[name]
            print(f"→ {table}")
            _truncate(dst, table)
            nbytes = _copy_query(src, dst, select_sql, copy_into)
            print(f"  字节≈{nbytes:,} 行数={_count(dst, f'SELECT COUNT(*) FROM {table}')}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["行为分析", "指数预测", "续传行为分析", ""], default="")
    args = parser.parse_args()

    if not DDL.exists():
        print(f"缺少 DDL: {DDL}", file=sys.stderr)
        return 1

    with psycopg.connect(目标库) as dst:
        print(f"应用 DDL: {DDL}", flush=True)
        _exec_sql_file(dst, DDL)
        only = args.only
        if only == "续传行为分析":
            migrate_agents_remainder(dst)
        elif only in ("", "行为分析"):
            migrate_agents(dst)
        if only in ("", "指数预测"):
            migrate_predictor(dst)

    print("迁移完成。请将三项目 DSN 指向 postgresql:///金融市场数据", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
