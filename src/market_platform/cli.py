"""统一 CLI —— `market-platform <域> [参数]`,四个入口收敛到一个命令。

    market-platform web                     启动统一站点 + 三域 API(门户,:18800)
    market-platform data  <命令>            数据底座(更新/水位/补拉/Web)
    market-platform agents <命令>           行为分析
    market-platform predictor <命令>        指数预测
    market-platform 健康                     三域库配置、search_path 与连通性一览

旧的 `market-agents` / `market-predictor` / `market` 入口仍可用(见 pyproject 与 bin/market)。
"""

from __future__ import annotations

import sys

用法 = """用法: market-platform <域> [参数]

  web                     统一站点与三域 API(门户服务,默认 :18800)
  data    <命令>          数据底座:更新金融市场数据 / 刷新全库水位 / 补拉落后表 / 扫描行情缺口 / 启动Web
  agents  <命令>          行为分析:fetch / features / nmf / classify / backtest / web …
  predictor <命令>        指数预测:web / update / paper / pipeline …
  健康                     三域库配置、search_path 与连通性一览

示例:
  market-platform web
  market-platform data  刷新全库水位
  market-platform agents run --backtest
  market-platform predictor web --port 18765
"""

# 数据底座命令别名 → 脚本模块名(允许中文与简写)
数据底座命令 = {
    "更新金融市场数据": "更新金融市场数据",
    "更新": "更新金融市场数据",
    "update": "更新金融市场数据",
    "刷新全库水位": "刷新全库水位",
    "水位": "刷新全库水位",
    "watermark": "刷新全库水位",
    "补拉落后表": "补拉落后表",
    "补拉": "补拉落后表",
    "扫描行情缺口": "扫描行情缺口",
    "缺口": "扫描行情缺口",
    "启动Web": "启动Web",
    "web": "启动Web",
}


def _数据底座(argv: list[str]) -> int:
    import importlib

    if not argv or argv[0] in {"-h", "--help"}:
        print("数据底座命令:")
        for 名 in ("更新金融市场数据", "刷新全库水位", "补拉落后表", "扫描行情缺口", "启动Web"):
            print(f"  {名}")
        return 0

    模块名 = 数据底座命令.get(argv[0])
    if 模块名 is None:
        print(f"未知数据底座命令: {argv[0]}", file=sys.stderr)
        print("可用: " + "、".join(sorted(set(数据底座命令))), file=sys.stderr)
        return 2

    模块 = importlib.import_module(f"market_platform.data.脚本.{模块名}")
    主函数 = getattr(模块, "main", None)
    if 主函数 is None:
        print(f"{模块名} 未提供 main()", file=sys.stderr)
        return 2
    return int(主函数(argv[1:]) or 0)


def _行为分析(argv: list[str]) -> int:
    from market_platform.agents.cli import main as 主函数

    旧 = sys.argv
    try:
        sys.argv = ["market-agents", *argv]
        return int(主函数() or 0)
    finally:
        sys.argv = 旧


def _指数预测(argv: list[str]) -> int:
    from market_platform.predictor.命令行 import main as 主函数

    旧 = sys.argv
    try:
        sys.argv = ["market-predictor", *argv]
        return int(主函数() or 0)
    finally:
        sys.argv = 旧


def _门户(argv: list[str]) -> int:
    from market_platform.门户.应用 import main as 主函数

    return int(主函数(argv) or 0)


def _健康(argv: list[str] | None = None) -> int:
    """打印三域配置与库连通性(只读探测)。"""
    from market_platform.core.配置 import 数据库设置, 加载环境变量
    from market_platform.core.路径 import (
        仓库根,
        前端产物,
        域_数据底座,
        域_行为分析,
        域_指数预测,
    )

    加载环境变量()
    print(f"仓库根: {仓库根()}")
    站点 = 前端产物()
    已构建 = (站点 / "index.html").exists()
    print(f"站点产物: {站点} ({'已构建' if 已构建 else '未构建'})")
    print()
    print(f"{'域':<10}{'启用':<8}{'search_path':<34}{'连通':<6}DSN")
    失败 = 0
    for 域 in (域_数据底座, 域_行为分析, 域_指数预测):
        设置 = 数据库设置(域)
        连通 = "—"
        if 设置.enabled and 设置.dsn:
            try:
                import psycopg

                with psycopg.connect(设置.dsn, connect_timeout=设置.connect_timeout) as 连:
                    with 连.cursor() as 游标:
                        游标.execute("SELECT 1")
                        游标.fetchone()
                连通 = "OK"
            except Exception as exc:  # noqa: BLE001
                连通 = "FAIL"
                失败 += 1
                print(f"  ({域} 连接失败: {type(exc).__name__}: {exc})", file=sys.stderr)
        print(f"{域:<10}{str(设置.enabled):<8}{设置.search_path:<34}{连通:<6}{设置.dsn}")
    return 1 if 失败 else 0


def _自检(argv: list[str] | None = None) -> int:
    """研究口径自检:前视开关与 unified 下失效的风控一览。"""
    from market_platform.predictor.自检 import 打印自检

    return 打印自检()


def main(argv: list[str] | None = None) -> int:
    参数 = list(sys.argv[1:] if argv is None else argv)
    if not 参数 or 参数[0] in {"-h", "--help", "help"}:
        print(用法)
        return 0

    域, 其余 = 参数[0], 参数[1:]
    if 域 in {"web", "门户", "portal"}:
        return _门户(其余)
    if 域 in {"data", "数据", "数据底座"}:
        return _数据底座(其余)
    if 域 in {"agents", "行为分析"}:
        return _行为分析(其余)
    if 域 in {"predictor", "指数预测"}:
        return _指数预测(其余)
    if 域 in {"健康", "health", "doctor"}:
        return _健康(其余)
    if 域 in {"自检", "lint", "检查"}:
        return _自检(其余)

    print(f"未知域: {域}", file=sys.stderr)
    print(用法, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
