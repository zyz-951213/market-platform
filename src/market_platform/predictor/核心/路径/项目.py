"""项目根目录与配置路径解析。"""

from __future__ import annotations

from pathlib import Path


def backend_root() -> Path:
    """后端根目录（含 配置/、数据/、脚本/）。"""
    return Path(__file__).resolve().parents[2]


def repo_root() -> Path:
    """仓库根目录（含 前端/）。"""
    return backend_root().parent


def project_root() -> Path:
    return backend_root()


def config_dir() -> Path:
    return backend_root() / "配置"


def database_config_path() -> Path:
    return config_dir() / "数据库.yaml"


def profile_path(name: str) -> Path:
    return config_dir() / "配置档" / name


def data_dir() -> Path:
    primary = backend_root() / "数据"
    legacy = repo_root() / "data"
    if primary.exists() and any(primary.iterdir()):
        return primary
    if legacy.exists() and any(legacy.iterdir()):
        return legacy
    return primary


def models_dir() -> Path:
    return backend_root() / "模型"


def checkpoints_dir() -> Path:
    return models_dir() / "检查点"


def logs_dir() -> Path:
    return backend_root() / "日志"


def frontend_dir() -> Path:
    return repo_root() / "前端"


def frontend_dist_dir() -> Path:
    return frontend_dir() / "dist"


def resolve_data_root(path: str | Path | None = None) -> Path:
    """将配置或相对路径解析为后端数据目录。"""
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
    return backend_root() / p
