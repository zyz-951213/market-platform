from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
import yaml

@dataclass
class DatabaseInfo:
    dsn: str
    label: str
    description: str = ""

@dataclass
class AppSettings:
    databases: dict[str, DatabaseInfo] = None
    
    @classmethod
    def load(cls) -> AppSettings:
        cfg_path = Path(__file__).resolve().parents[1] / "配置" / "数据库.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        dbs = {}
        for key, val in (raw.get("databases") or {}).items():
            dbs[key] = DatabaseInfo(
                dsn=val.get("dsn", ""),
                label=val.get("label", key),
                description=val.get("description", ""),
            )
        env_dsn = os.environ.get("DATABASE_URL", "").strip()
        if env_dsn:
            dbs["default"] = DatabaseInfo(dsn=env_dsn, label="环境变量数据库")
        return cls(databases=dbs)

settings = AppSettings.load()