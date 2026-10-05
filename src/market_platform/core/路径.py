"""统一路径内核 —— 全仓唯一的根目录/资源目录解析。

设计原则:
1. **只有一个根**:`仓库根()` 靠 `pyproject.toml` 向上定位,可用环境变量
   `MARKET_PLATFORM_ROOT` 覆盖(便于部署到其它机器)。
2. **代码与运行态分离**:包目录只放代码与资源;数据/输出/模型/日志在仓库根的
   `数据/ 输出/ 模型/ 日志/` 下,按域分子目录(可从源重建,不入库)。
3. **配置唯一**:所有 YAML/.env 都在 `config/` 下;包内不再各自持有一份。
4. **迁移兼容**:新位置缺失时可回退到旧位置(包内),回退会打 warning,
   便于迁移期平滑过渡;迁移完成后可删掉 `_兼容旧位置` 分支。

本模块**不导入任何业务模块**,可被任意层安全引用。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("market_platform.core.路径")

# 域中文名(与 schema/目录命名一致)
域_数据底座 = "数据底座"
域_行为分析 = "行为分析"
域_指数预测 = "指数预测"
域列表 = (域_数据底座, 域_行为分析, 域_指数预测)

# 旧位置(迁移期兼容):包内各自的 配置/ 数据/ 模型/ 日志/
_包根缓存: Path | None = None


def _定位包根() -> Path:
    global _包根缓存
    if _包根缓存 is None:
        # 本文件位于 <仓库根>/src/market_platform/core/路径.py
        _包根缓存 = Path(__file__).resolve().parents[1]
    return _包根缓存


def 仓库根() -> Path:
    """仓库根(含 pyproject.toml)。"""
    override = os.environ.get("MARKET_PLATFORM_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    for parent in _定位包根().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    # 兜底:src 的上一级
    return _定位包根().parent.parent


def 源码根() -> Path:
    return 仓库根() / "src"


def 包根() -> Path:
    return _定位包根()


# ── 配置 ──────────────────────────────────────────────────────────────
def 配置根() -> Path:
    return 仓库根() / "config"


def 数据库配置() -> Path:
    return 配置根() / "数据库.yaml"


def 配置档目录(域: str) -> Path:
    return 配置根() / 域 / "配置档"


def 环境文件() -> Path:
    """环境变量文件优先级:config/.env → 仓库根/.env。"""
    for candidate in (配置根() / ".env", 仓库根() / ".env"):
        if candidate.exists():
            return candidate
    return 配置根() / ".env"


# ── 静态资源与前端 ────────────────────────────────────────────────────
def 前端根() -> Path:
    return 仓库根() / "web"


def 前端产物() -> Path:
    return 前端根() / "dist"


def 部署根() -> Path:
    return 仓库根() / "deploy"


def 文档根() -> Path:
    return 仓库根() / "docs"


# ── 数据库 DDL 与迁移 ─────────────────────────────────────────────────
def schema根() -> Path:
    return 仓库根() / "schema"


def 迁移目录() -> Path:
    return schema根() / "迁移"


def 测试根() -> Path:
    return 仓库根() / "tests"


# ── 运行态(不入库) ────────────────────────────────────────────────────
def 运行态根() -> Path:
    return 仓库根()


def 数据目录(域: str | None = None) -> Path:
    return 运行态根() / "数据" / 域 if 域 else 运行态根() / "数据"


def 输出目录(域: str | None = None) -> Path:
    return 运行态根() / "输出" / 域 if 域 else 运行态根() / "输出"


def 模型目录(域: str | None = None) -> Path:
    return 运行态根() / "模型" / 域 if 域 else 运行态根() / "模型"


def 日志目录(域: str | None = None) -> Path:
    return 运行态根() / "日志" / 域 if 域 else 运行态根() / "日志"


# ── 工具 ──────────────────────────────────────────────────────────────
def 确保目录(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def 兼容旧位置(新: Path, 旧: Path, *, 说明: str = "") -> Path:
    """新位置不存在而旧位置存在时回退(迁移期使用),并给出提示。"""
    if 新.exists():
        return 新
    if 旧.exists():
        logger.warning("路径迁移期回退: %s → %s %s", 新, 旧, 说明)
        return 旧
    return 新


def 域配置档(域: str, 名称: str) -> Path:
    """按域取配置档,迁移期可回退到包内旧位置。"""
    新 = 配置档目录(域) / 名称
    if 新.exists():
        return 新
    旧域 = {域_数据底座: "data", 域_行为分析: "agents", 域_指数预测: "predictor"}.get(域, 域)
    旧 = 包根() / 旧域 / "配置" / "配置档" / 名称
    return 兼容旧位置(新, 旧, 说明=f"配置档 {域}/{名称}")


def 域数据库配置(域: str) -> Path:
    """按域取库配置:优先唯一根 config/数据库.yaml。"""
    新 = 数据库配置()
    if 新.exists():
        return 新
    旧域 = {域_数据底座: "data", 域_行为分析: "agents", 域_指数预测: "predictor"}.get(域, 域)
    旧 = 包根() / 旧域 / "配置" / "数据库.yaml"
    return 兼容旧位置(新, 旧, 说明=f"库配置 {域}")


__all__ = [
    "仓库根", "源码根", "包根",
    "配置根", "数据库配置", "配置档目录", "环境文件",
    "前端根", "前端产物", "部署根", "文档根",
    "schema根", "迁移目录", "测试根",
    "数据目录", "输出目录", "模型目录", "日志目录",
    "确保目录", "兼容旧位置", "域配置档", "域数据库配置",
    "域_数据底座", "域_行为分析", "域_指数预测", "域列表",
]
