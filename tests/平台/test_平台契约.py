"""平台契约测试 —— 守住「一个仓库、一个配置根、一个站点、一个 CLI」的不变量。

这些用例**不写库、不联网**,只做结构与装配校验,适合在 CI 里跑。
"""

from __future__ import annotations

from pathlib import Path

from market_platform.core.路径 import (
    仓库根,
    前端产物,
    域_数据底座,
    域_行为分析,
    域_指数预测,
    数据目录,
    数据库配置,
    模型目录,
    日志目录,
    测试根,
    配置根,
)
from market_platform.core.配置 import 数据库设置, 库名

源码根 = 仓库根() / "src" / "market_platform"


def test_仓库根定位正确():
    assert (仓库根() / "pyproject.toml").is_file()
    assert (仓库根() / "AGENTS.md").is_file()


def test_唯一配置根():
    assert 配置根() == 仓库根() / "config"
    assert 数据库配置().is_file(), "config/数据库.yaml 必须存在(唯一 DSN 真相源)"
    assert 库名() == "金融市场数据"


def test_三域_search_path_与宪法一致():
    期望 = {
        域_数据底座: "公共, 行情增量",
        域_行为分析: "行为分析, 公共, 行情增量",
        域_指数预测: "指数预测, 公共, 行情增量",
    }
    for 域, 路径 in 期望.items():
        设置 = 数据库设置(域)
        assert 设置.search_path == 路径, f"{域} search_path 漂移: {设置.search_path}"
        assert 设置.enabled, f"{域} 应在统一配置里启用"


def test_配置档已迁入配置根():
    for 域 in (域_行为分析, 域_指数预测):
        目录 = 配置根() / 域 / "配置档"
        assert 目录.is_dir(), f"缺少配置档目录: {目录}"
        assert list(目录.glob("*.yaml")) or list(目录.glob("*.yml")), f"{目录} 下没有配置档"


def test_运行态目录在仓库根而非包内():
    for 目录 in (数据目录(域_行为分析), 数据目录(域_指数预测), 模型目录(域_指数预测), 日志目录(域_行为分析)):
        assert 仓库根() in 目录.parents
        assert "src" not in 目录.parts, f"运行态目录不应位于源码树内: {目录}"
    # 包目录里不得再出现 数据/模型/日志/配置 之类的运行态目录
    for 名 in ("数据", "模型", "日志", "outputs", "配置"):
        assert not (源码根 / 名).exists(), f"包内仍有运行态目录: {源码根 / 名}"


def test_测试根统一():
    assert 测试根() == 仓库根() / "tests"
    assert 测试根().is_dir()


def test_源码内不得有硬编码家目录路径():
    违规 = []
    for p in 源码根.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        if "/home/" in p.read_text(encoding="utf-8", errors="ignore"):
            违规.append(str(p.relative_to(仓库根())))
    assert not 违规, f"发现硬编码家目录路径: {违规}"


def test_三个更新脚本导入不产生副作用():
    """历史事故:这两个脚本在模块顶层直接跑更新,任何 import 都会写生产库。"""
    import importlib

    for 模块 in (
        "market_platform.data.脚本.更新金融市场数据",
        "market_platform.data.脚本.补拉落后表",
    ):
        模块对象 = importlib.import_module(模块)
        assert hasattr(模块对象, "main"), f"{模块} 缺少 main() 入口"
        assert 模块对象.DB_NAME == "金融市场数据"


def test_门户装配三域且站点已构建():
    from fastapi.testclient import TestClient

    from market_platform.门户.应用 import create_app

    客户端 = TestClient(create_app())

    健康 = 客户端.get("/健康")
    assert 健康.status_code == 200
    体 = 健康.json()
    assert 体["已装配域"] == [域_数据底座, 域_行为分析, 域_指数预测]
    assert 体["装配失败"] == []

    if not (前端产物() / "index.html").exists():
        return  # 站点未构建时只校验 API 侧

    for 路径 in ("/", f"/{域_数据底座}/", f"/{域_行为分析}/", f"/{域_指数预测}/"):
        assert 客户端.get(路径).status_code == 200, f"{路径} 应返回站点页面"

    # 深链接应回退到 SPA 首页(验证静态挂载器与路由参数名)
    深链接 = 客户端.get(f"/{域_数据底座}/任意/深/链接")
    assert 深链接.status_code == 200

    # 域 API 在门户下可用(:数据底座 /api/health 不触库)
    域健康 = 客户端.get(f"/{域_数据底座}/api/health")
    assert 域健康.status_code == 200
    assert 域健康.json()["status"] == "ok"


def test_源码目录无遗留旧路径引用():
    关键词 = ("后端/", "packages/data", "packages/agents", "packages/predictor")
    命中 = []
    for p in 源码根.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        文本 = p.read_text(encoding="utf-8", errors="ignore")
        for 词 in 关键词:
            if 词 in 文本:
                命中.append(f"{p.relative_to(仓库根())}: {词}")
    assert not 命中, f"源码仍引用已退役路径: {命中[:5]}"


def test_统一_CLI_四域入口齐备(capsys=None):
    from market_platform import cli

    assert cli.main([]) == 0
    assert cli.main(["data", "--help"]) == 0
    assert cli.main(["不存在"]) == 2
