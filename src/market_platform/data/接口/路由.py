"""API 路由"""
from __future__ import annotations
import os
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from .数据库控制器 import DBController

router = APIRouter(prefix="/api")
controller = DBController()


def _update_enabled() -> bool:
    return os.getenv("UPDATE_API_ENABLED", "0").lower() in {"1", "true", "yes"}


def _require_update():
    if not _update_enabled():
        raise HTTPException(
            403,
            "更新 API 未启用。在 .env 设置 UPDATE_API_ENABLED=1 后重启 API。",
        )


class TableQueryReq(BaseModel):
    table: str
    schema: Optional[str] = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)
    order_by: Optional[str] = None
    where: Optional[str] = None


class SqlReq(BaseModel):
    sql: str
    max_rows: int = Field(default=500, ge=1, le=2000)


class UpdateTarget(BaseModel):
    数据源: str
    数据集: str


class UpdateJobReq(BaseModel):
    targets: Optional[list[UpdateTarget]] = None
    tags: Optional[list[str]] = None
    all_schedulable: bool = False
    only_stale: bool = False
    trigger: str = "前端"


@router.get("/health")
def health():
    return {
        "status": "ok",
        "version": "0.3.0",
        "mode": "update" if _update_enabled() else "readonly",
        "update_api": _update_enabled(),
    }


@router.get("/db")
def get_db():
    return controller.get_db_info()


@router.get("/schemas")
def get_schemas():
    return controller.list_schemas()


@router.get("/tables")
def list_tables(schema: Optional[str] = None):
    tables = controller.list_tables()
    if schema:
        tables = [t for t in tables if t.get("schema_name") == schema]
    return tables


@router.get("/schema/{schema_name}/tables/{table_name}")
def get_table_info(schema_name: str, table_name: str):
    try:
        return controller.get_table_info(schema_name, table_name)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/schema/{schema_name}/tables/{table_name}/stats")
def get_table_stats(
    schema_name: str,
    table_name: str,
    exact: bool = Query(False, description="是否精确 COUNT(*)，大表慎用"),
):
    try:
        return controller.get_table_stats(schema_name, table_name, exact=exact)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/schema/{schema_name}/tables/{table_name}/preview")
def preview_table(schema_name: str, table_name: str, limit: int = Query(20, ge=1, le=200)):
    try:
        return controller.query_table(schema_name, table_name, limit=limit)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/query")
def query_table(req: TableQueryReq):
    try:
        return controller.query_table(
            req.schema, req.table, req.limit, req.offset, req.order_by, req.where
        )
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/sql")
def run_sql(req: SqlReq):
    try:
        return controller.execute_sql(req.sql, max_rows=req.max_rows)
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/update/console")
def update_console(
    owner: Optional[str] = None,
    tag: Optional[str] = None,
    freshness: Optional[str] = None,
    schedulable_only: bool = Query(False),
):
    """控制台只读，不要求 UPDATE_API_ENABLED。"""
    from ..存储.更新作业 import list_console

    try:
        return list_console(
            owner=owner,
            tag=tag,
            freshness=freshness,
            schedulable_only=schedulable_only,
        )
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/update/jobs")
def update_jobs(limit: int = Query(20, ge=1, le=100)):
    from ..存储.更新作业 import list_jobs

    return list_jobs(limit=limit)


@router.get("/update/jobs/{job_id}")
def update_job(job_id: str):
    from ..存储.更新作业 import get_job

    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "作业不存在")
    return job


@router.post("/update/jobs")
def create_update_job(req: UpdateJobReq):
    _require_update()
    from ..存储.更新作业 import create_job

    try:
        return create_job(
            targets=[t.model_dump() for t in req.targets] if req.targets else None,
            tags=req.tags,
            all_schedulable=req.all_schedulable,
            only_stale=req.only_stale,
            trigger=req.trigger or "前端",
        )
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/update/watermarks/refresh")
def refresh_watermarks():
    _require_update()
    from ..存储.更新作业 import refresh_all_watermarks

    try:
        return refresh_all_watermarks()
    except Exception as e:
        raise HTTPException(500, str(e))
