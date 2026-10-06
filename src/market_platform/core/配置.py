"""统一配置内核 —— 全仓唯一的配置读取入口。

职责:
1. 定位并加载环境变量文件(`config/.env`);
2. 加载 YAML,并展开 `${ENV_VAR}` 占位符;
3. 解析数据库设置(唯一真相源 `config/数据库.yaml`),迁移期可读旧格式;
4. 提供配置档(profile)加载:按域 + 名称取 `config/<域>/配置档/<名称>.yaml`。

本模块只依赖 `core.路径`,不依赖任何业务模块。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from market_platform.core.路径 import (
    域_数据底座,
    域配置档,
    域数据库配置,
    环境文件,
    数据库配置,
)
from market_platform.core.数据库配置 import (
    DEFAULT_DSN,
    DEFAULT_SEARCH_PATH,
    DatabaseSettings,
)

_环境已加载 = False


# ── 环境变量 ──────────────────────────────────────────────────────────
def 加载环境变量(*, 强制: bool = False) -> Path:
    """加载 `config/.env`(缺失则回退仓库根/.env)。只加载一次。"""
    global _环境已加载
    path = 环境文件()
    if _环境已加载 and not 强制:
        return path
    try:
        from dotenv import load_dotenv

        if path.exists():
            load_dotenv(path, override=False)
    except ImportError:  # dotenv 可选依赖
        pass
    _环境已加载 = True
    return path


# ── YAML ─────────────────────────────────────────────────────────────
def 展开环境变量(value: Any) -> Any:
    """递归展开字符串中的 ${VAR} / ${VAR:-默认值}。"""
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}") and value.count("${") == 1:
            inner = value[2:-1]
            name, _, default = inner.partition(":-")
            return os.environ.get(name.strip(), default)
        if "${" in value:
            out = value
            for name, default in _占位符列表(value):
                out = out.replace("${" + name + "}", os.environ.get(name, ""))
                if default is not None:
                    out = out.replace("${" + name + ":-" + default + "}", os.environ.get(name, default))
            return out
        return value
    if isinstance(value, dict):
        return {k: 展开环境变量(v) for k, v in value.items()}
    if isinstance(value, list):
        return [展开环境变量(v) for v in value]
    return value


def _占位符列表(text: str) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    rest = text
    while "${" in rest:
        start = rest.index("${")
        end = rest.find("}", start)
        if end == -1:
            break
        inner = rest[start + 2 : end]
        name, _, default = inner.partition(":-")
        out.append((name.strip(), default or None))
        rest = rest[end + 1 :]
    return out


def 加载YAML(path: Path, *, 必需: bool = False) -> dict:
    """加载 YAML 文件为 dict;不存在时按 `必需` 决定是否抛错。"""
    if not path.exists():
        if 必需:
            raise FileNotFoundError(f"配置文件不存在: {path}")
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"配置文件根节点必须是映射: {path}")
    return 展开环境变量(raw)


def 加载配置档(域: str, 名称: str = "默认.yaml", *, 必需: bool = False) -> dict:
    """加载某域的配置档(如 行为分析/默认.yaml)。"""
    return 加载YAML(域配置档(域, 名称), 必需=必需)


# ── 数据库 ────────────────────────────────────────────────────────────
def 数据库设置(域: str = 域_数据底座) -> DatabaseSettings:
    """解析数据库设置(优先唯一根 config/数据库.yaml,迁移期兼容旧格式)。"""
    加载环境变量()

    path = 数据库配置()
    if path.exists():
        raw = 加载YAML(path)
        settings = _解析统一格式(raw, 域)
    else:
        settings = _解析旧格式(域数据库配置(域), 域)

    env_url = os.environ.get("DATABASE_URL", "").strip()
    if env_url:
        settings.dsn = env_url
    if os.environ.get("DATABASE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        settings.enabled = True
    return settings


def _解析统一格式(raw: dict, 域: str) -> DatabaseSettings:
    """config/数据库.yaml(中文键) → DatabaseSettings。"""
    写入 = raw.get("写入") or {}
    域段 = {}
    if isinstance(写入, dict):
        域段 = 写入.get(域) or {}
    读表 = raw.get("读表") or {}
    dsn = str(raw.get("连接") or raw.get("dsn") or DEFAULT_DSN)

    enabled_default = bool(raw.get("启用", True))
    return DatabaseSettings(
        enabled=bool(域段.get("可写", enabled_default)),
        dsn=dsn,
        search_path=str(域段.get("搜索路径") or raw.get("搜索路径") or DEFAULT_SEARCH_PATH),
        read_from_pg=bool(读表.get("从库读", raw.get("read_from_pg", True))),
        batch_size=int(读表.get("批量大小", raw.get("batch_size", 2000))),
        connect_timeout=int(读表.get("连接超时", raw.get("connect_timeout", 10))),
        dual_write=bool(读表.get("双写文件", raw.get("dual_write", False))),
        write_parquet=bool(读表.get("写parquet", raw.get("write_parquet", False))),
        parquet_fallback=bool(读表.get("parquet回退", raw.get("parquet_fallback", False))),
        write_files=bool(读表.get("写文件", raw.get("write_files", False))),
        file_fallback=bool(读表.get("文件回退", raw.get("file_fallback", False))),
    )


def _解析旧格式(path: Path, 域: str) -> DatabaseSettings:
    """旧格式兼容:agents/predictor 的扁平式,以及 data 的 databases 映射式。"""
    raw = 加载YAML(path)
    if not raw:
        return DatabaseSettings(enabled=False, dsn=DEFAULT_DSN)

    if "databases" in raw and isinstance(raw["databases"], dict):
        # data 旧格式:{databases: {金融市场数据: {dsn, label, ...}}}
        for _, entry in raw["databases"].items():
            if isinstance(entry, dict) and entry.get("dsn"):
                return DatabaseSettings(
                    enabled=True,
                    dsn=str(entry["dsn"]),
                    search_path=str(entry.get("search_path", DEFAULT_SEARCH_PATH)),
                )
        return DatabaseSettings(enabled=False, dsn=DEFAULT_DSN)

    return DatabaseSettings.from_dict(raw)


def 数据库DSN(域: str = 域_数据底座) -> str:
    """便捷函数:只取 DSN。"""
    return 数据库设置(域).dsn


def 日行情档案分界日() -> str:
    """日行情「档案段/增量段」分界日(ISO 字符串),来自 config/数据库.yaml。

    评审 D7:此前 `2026-05-20` 硬编码在代码与 SQL 多处;现由配置唯一提供,
    默认值仅作为配置缺失时的兜底。
    """
    raw = 加载YAML(数据库配置())
    段 = raw.get("日行情") or {}
    值 = str(段.get("档案分界日") or "2026-05-20").strip()
    return 值


def 库名() -> str:
    raw = 加载YAML(数据库配置())
    return str(raw.get("库名") or "金融市场数据")


__all__ = [
    "加载环境变量",
    "日行情档案分界日",
    "加载YAML",
    "加载配置档",
    "展开环境变量",
    "数据库设置",
    "数据库DSN",
    "库名",
    "DatabaseSettings",
]
