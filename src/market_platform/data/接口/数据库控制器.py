"""数据库控制器 - 只针对金融市场数据"""
from __future__ import annotations

from market_platform.core.配置 import 数据库设置
from market_platform.core.路径 import 域_数据底座
from ..存储.数据库 import DatabaseManager

DB_NAME = "金融市场数据"


class DBController:
    def __init__(self):
        self._dbs = self._load_config()
        self._manager = DatabaseManager(self._dbs)

    def _load_config(self) -> dict:
        """库配置唯一来源 = config/数据库.yaml(经 core.配置 解析)。

        历史版本自行 yaml 解析旧格式 `databases: {...}`,配置收敛后必须走内核,
        否则会静默变成 unconfigured。
        """
        设置 = 数据库设置(域_数据底座)
        if not 设置.dsn:
            return {}
        return {
            DB_NAME: {
                "dsn": 设置.dsn,
                "label": DB_NAME,
                "description": "统一 A 股量化库(公共 / 行情增量 / 行为分析 / 指数预测)",
            }
        }

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
