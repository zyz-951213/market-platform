"""数据库控制器 - 只针对金融市场数据"""
from __future__ import annotations

from market_platform.core.路径 import 数据库配置
from pathlib import Path
import yaml
from ..存储.数据库 import DatabaseManager

DB_NAME = "金融市场数据"


class DBController:
    def __init__(self):
        self._dbs = self._load_config()
        self._manager = DatabaseManager(self._dbs)

    def _load_config(self) -> dict:
        cfg_path = 数据库配置()
        with open(cfg_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return raw.get("databases") or {}

    def get_db_info(self) -> dict:
        info = self._dbs.get(DB_NAME)
        if not info:
            return {
                "name": DB_NAME,
                "status": "unconfigured",
                "table_count": 0,
                "schema_count": 0,
                "size": "N/A",
                "label": DB_NAME,
                "description": "",
            }
        try:
            overview = self._manager.get_overview(DB_NAME)
            return {
                "name": DB_NAME,
                "label": info.get("label", DB_NAME),
                "description": info.get("description", ""),
                "status": "connected",
                "table_count": overview["table_count"],
                "schema_count": overview["schema_count"],
                "size": overview["size"],
                "size_bytes": overview["size_bytes"],
                "schemas": overview["schemas"],
                "top_tables": overview["top_tables"],
                "watermarks": overview.get("watermarks") or [],
            }
        except Exception as e:
            return {
                "name": DB_NAME,
                "label": info.get("label", DB_NAME),
                "description": info.get("description", ""),
                "status": "error",
                "error": str(e),
                "table_count": 0,
                "schema_count": 0,
                "size": "N/A",
            }

    def list_tables(self) -> list[dict]:
        return self._manager.list_tables(DB_NAME)

    def list_schemas(self) -> list[str]:
        return self._manager.list_schemas(DB_NAME)

    def get_table_info(self, schema_name: str, table_name: str) -> dict:
        schema_info = self._manager.get_table_schema(DB_NAME, schema_name, table_name)
        indexes = self._manager.get_indexes(DB_NAME, schema_name, table_name)
        fks = self._manager.get_foreign_keys(DB_NAME, schema_name, table_name)
        return {"columns": schema_info, "indexes": indexes, "foreign_keys": fks}

    def get_table_stats(self, schema_name: str, table_name: str, exact: bool = False) -> dict:
        return self._manager.get_table_stats(DB_NAME, schema_name, table_name, exact=exact)

    def query_table(
        self,
        schema_name: str = None,
        table_name: str = None,
        limit: int = 100,
        offset: int = 0,
        order_by: str = None,
        where: str = None,
    ) -> dict:
        return self._manager.query_table(
            DB_NAME, schema_name, table_name, limit, offset, order_by, where
        )

    def execute_sql(self, sql: str, max_rows: int = 500) -> dict:
        return self._manager.execute_sql(DB_NAME, sql, max_rows=max_rows)
