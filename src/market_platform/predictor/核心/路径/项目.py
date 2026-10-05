"""指数预测域路径解析 —— 全部委托 `market_platform.core.路径`(唯一根)。

保留原有函数名,使既有调用点零改动;语义变化:
- `data_dir()` / `models_dir()` / `logs_dir()` 由「包内目录」改为仓库运行态目录
  (`数据/指数预测`、`模型/指数预测`、`日志/指数预测`)。
- `config_dir()` / `profile_path()` 指向唯一配置根 `config/指数预测/配置档`。
"""

from __future__ import annotations

from pathlib import Path

from market_platform.core.路径 import (
    仓库根,
    前端产物,
    域_指数预测,
    域配置档,
    数据目录,
    数据库配置,
    确保目录,
    模型目录,
    日志目录,
)


def backend_root() -> Path:
    """指数预测域的“后端根”= 仓库根(历史语义为包内目录,已归一)。"""
    return 仓库根()


def repo_root() -> Path:
    """仓库根(含 web/、tests/、schema/)。"""
    return 仓库根()


def project_root() -> Path:
    return backend_root()


def config_dir() -> Path:
    from market_platform.core.路径 import 配置根

    return 配置根()


def database_config_path() -> Path:
    return 数据库配置()


def profile_path(name: str) -> Path:
    """指数预测配置档路径(如 默认.yaml)。"""
    return 域配置档(域_指数预测, name)


def data_dir() -> Path:
    return 数据目录(域_指数预测)


def models_dir() -> Path:
    return 模型目录(域_指数预测)


def checkpoints_dir() -> Path:
    return models_dir() / "检查点"


def logs_dir() -> Path:
    return 日志目录(域_指数预测)


def frontend_dir() -> Path:
    """前端产物目录(统一站点构建产物中的指数预测子应用)。"""
    return 前端产物() / 域_指数预测


def frontend_dist_dir() -> Path:
    """兼容旧名:现指统一站点的该域产物目录。"""
    return frontend_dir()


def resolve_data_root(path: str | Path | None = None) -> Path:
    """将配置或相对路径解析为运行态数据目录。"""
    if path is None:
        return data_dir()
    text = str(path).strip()
    if not text or text in {"data", "数据"}:
        return data_dir()
    p = Path(path)
    if p.is_absolute():
        return p
    if p.name in {"data", "数据"} or str(p) in {"data", "数据"}:
        return data_dir()
    return 仓库根() / p


def ensure(path: Path) -> Path:
    return 确保目录(path)
