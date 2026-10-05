"""行为分析域路径解析 —— 全部委托 `market_platform.core.路径`(唯一根)。

保留原有函数名,使既有调用点零改动;语义变化:
- `data_dir()` / `output_dir()` 由「包内目录」改为「仓库运行态目录」
  (`数据/行为分析`、`输出/行为分析`),包目录只放代码。
- `config_dir()` / `profile_path()` 指向唯一配置根 `config/行为分析/配置档`。
"""

from __future__ import annotations

from pathlib import Path

from market_platform.core.路径 import (
    仓库根,
    前端产物,
    域_行为分析,
    域配置档,
    数据目录,
    数据库配置,
    确保目录,
    输出目录,
)


def backend_root() -> Path:
    """行为分析域的“后端根”= 仓库根(历史语义为包内目录,已归一)。"""
    return 仓库根()


def repo_root() -> Path:
    """仓库根(含 web/、tests/、schema/)。"""
    return 仓库根()


def project_root() -> Path:
    return backend_root()


def config_dir() -> Path:
    """配置根(含 数据库.yaml 与各域配置档)。"""
    from market_platform.core.路径 import 配置根

    return 配置根()


def profile_path(name: str) -> Path:
    """行为分析配置档路径(如 默认.yaml / 自动更新.yaml)。"""
    return 域配置档(域_行为分析, name)


def database_config_path() -> Path:
    """库配置(全仓唯一)。"""
    return 数据库配置()


def data_dir() -> Path:
    return 数据目录(域_行为分析)


def output_dir() -> Path:
    return 输出目录(域_行为分析)


def resolve_data_root(path: str | Path | None = None) -> Path:
    """把配置里的相对根(如 `data`)解析为运行态数据目录。"""
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


def resolve_output_root(path: str | Path | None = None) -> Path:
    """把配置里的相对根(如 `outputs`)解析为运行态输出目录。"""
    if path is None:
        return output_dir()
    text = str(path).strip()
    if not text or text in {"outputs", "输出"}:
        return output_dir()
    p = Path(path)
    if p.is_absolute():
        return p
    if p.name in {"outputs", "输出"} or str(p) in {"outputs", "输出"}:
        return output_dir()
    return 仓库根() / p


def frontend_dir() -> Path:
    """前端产物目录(统一站点构建产物中的行为分析子应用)。"""
    return 前端产物() / 域_行为分析


def web_static_dir() -> Path:
    """兼容旧名。"""
    return frontend_dir()


def ensure(path: Path) -> Path:
    return 确保目录(path)
