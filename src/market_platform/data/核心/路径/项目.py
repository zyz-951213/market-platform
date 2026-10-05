"""项目根目录与配置路径解析。"""
from __future__ import annotations
from pathlib import Path

def backend_root() -> Path:
    return Path(__file__).resolve().parents[3]

def repo_root() -> Path:
    return backend_root()
