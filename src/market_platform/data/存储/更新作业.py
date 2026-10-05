"""更新作业编排器：单表 / 按标签 / 全部可调度。"""
from __future__ import annotations

from market_platform.core.配置 import 数据库DSN

import threading
import traceback
import uuid
from datetime import date, datetime
from typing import Any

import psycopg
from psycopg.types.json import Json

from .数据更新器 import DataUpdater, get_watermark, refresh_watermark
from .更新注册表 import resolve

DSN = 数据库DSN()
DB_NAME = "金融市场数据"

_lock = threading.Lock()
_running_job: str | None = None


def list_console(
    *,
    owner: str | None = None,
    tag: str | None = None,
    freshness: str | None = None,
    schedulable_only: bool = False,
) -> list[dict]:
    clauses = ["TRUE"]
    params: list[Any] = []
    if owner:
        clauses.append("责任方 = %s")
        params.append(owner)
    if tag:
        clauses.append("批量标签 = %s")
        params.append(tag)
    if freshness:
        clauses.append("新鲜度 = %s")
        params.append(freshness)
    if schedulable_only:
        clauses.append("可调度 = TRUE")
    sql = f'''
        SELECT * FROM "行情增量"."更新控制台"
        WHERE {" AND ".join(clauses)}
        ORDER BY
          CASE 新鲜度
            WHEN '可更新' THEN 0 WHEN '无数据' THEN 1 WHEN '未知' THEN 2
            WHEN '已对齐' THEN 3 ELSE 4 END,
          落后天数 DESC NULLS LAST,
          数据源, 数据集
    '''
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for row in rows:
        for k, v in list(row.items()):
            if isinstance(v, (datetime, date)):
                row[k] = v.isoformat()
            elif isinstance(v, uuid.UUID):
                row[k] = str(v)
    return rows


def _serialize_row(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def get_job(job_id: str) -> dict | None:
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT * FROM "行情增量"."更新运行" WHERE "运行编号" = %s::uuid',
                (job_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d.name for d in cur.description]
            job = _serialize_row(dict(zip(cols, row)))
            cur.execute(
                '''
                SELECT * FROM "行情增量"."更新运行明细"
                WHERE "运行编号" = %s::uuid
                ORDER BY "开始时间" NULLS FIRST, "数据集"
                ''',
                (job_id,),
            )
            dcols = [d.name for d in cur.description]
            job["明细"] = [_serialize_row(dict(zip(dcols, r))) for r in cur.fetchall()]
            return job


def list_jobs(limit: int = 20) -> list[dict]:
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                '''
                SELECT "运行编号","触发方式","范围说明","状态","开始时间","结束时间",
                       "成功数","失败数","跳过数"
                FROM "行情增量"."更新运行"
                ORDER BY "开始时间" DESC
                LIMIT %s
                ''',
                (limit,),
            )
            cols = [d.name for d in cur.description]
            return [_serialize_row(dict(zip(cols, r))) for r in cur.fetchall()]


def create_job(
    *,
    targets: list[dict] | None = None,
    tags: list[str] | None = None,
    all_schedulable: bool = False,
    only_stale: bool = False,
    trigger: str = "API",
    created_by: str | None = None,
) -> dict:
    """创建作业并后台执行。同时只跑一个作业（避免源站限流互相踩）。"""
    global _running_job
    with _lock:
        if _running_job:
            raise RuntimeError(f"已有作业运行中: {_running_job}")

        items = _resolve_targets(targets, tags, all_schedulable, only_stale)
        if not items:
            raise ValueError("没有可调度的目标表")

        job_id = str(uuid.uuid4())
        scope = _scope_text(targets, tags, all_schedulable, only_stale, len(items))
        with psycopg.connect(DSN) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    '''
                    INSERT INTO "行情增量"."更新运行"
                      ("运行编号","触发方式","范围说明","状态","创建人")
                    VALUES (%s::uuid, %s, %s, '排队', %s)
                    ''',
                    (job_id, trigger, scope, created_by),
                )
                for it in items:
                    cur.execute(
                        '''
                        INSERT INTO "行情增量"."更新运行明细"
                          ("运行编号","数据源","数据集","拉取源","更新函数","状态")
                        VALUES (%s::uuid, %s, %s, %s, %s, '排队')
                        ''',
                        (
                            job_id,
                            it["数据源"],
                            it["数据集"],
                            it.get("拉取源"),
                            it.get("更新函数"),
                        ),
                    )
            conn.commit()

        _running_job = job_id
        t = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
        t.start()
        return {"运行编号": job_id, "目标数": len(items), "范围说明": scope, "状态": "排队"}


def _scope_text(targets, tags, all_schedulable, only_stale, n) -> str:
    parts = []
    if all_schedulable:
        parts.append("全部可调度")
    if tags:
        parts.append("标签=" + ",".join(tags))
    if targets:
        parts.append(f"指定{len(targets)}表")
    if only_stale:
        parts.append("仅可更新")
    parts.append(f"共{n}项")
    return " / ".join(parts)


def _resolve_targets(targets, tags, all_schedulable, only_stale) -> list[dict]:
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            if targets:
                out = []
                for t in targets:
                    cur.execute(
                        '''
                        SELECT 数据源, 数据集, 拉取源, 更新函数, 可调度, 水位列, 超时秒, 并发组
                        FROM "行情增量"."更新控制台"
                        WHERE 数据源 = %s AND 数据集 = %s
                        ''',
                        (t["数据源"], t["数据集"]),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise ValueError(f"未登记: {t}")
                    cols = [d.name for d in cur.description]
                    item = dict(zip(cols, row))
                    if not item["可调度"] or not item["更新函数"]:
                        raise ValueError(f"不可调度: {item['数据源']}.{item['数据集']}")
                    if not resolve(item["更新函数"]):
                        raise ValueError(f"未注册函数: {item['更新函数']}")
                    out.append(item)
                return out

            clauses = ["可调度 = TRUE", "更新函数 IS NOT NULL"]
            params: list[Any] = []
            if tags:
                clauses.append("批量标签 = ANY(%s)")
                params.append(tags)
            elif not all_schedulable:
                raise ValueError("请指定 targets / tags / all_schedulable")
            if only_stale:
                clauses.append("新鲜度 = '可更新'")
            cur.execute(
                f'''
                SELECT 数据源, 数据集, 拉取源, 更新函数, 可调度, 水位列, 超时秒, 并发组
                FROM "行情增量"."更新控制台"
                WHERE {" AND ".join(clauses)}
                ORDER BY 并发组 NULLS LAST, 数据集
                ''',
                params,
            )
            cols = [d.name for d in cur.description]
            items = [dict(zip(cols, r)) for r in cur.fetchall()]
            return [i for i in items if resolve(i["更新函数"])]


def _run_job(job_id: str) -> None:
    global _running_job
    updater = DataUpdater(DB_NAME, DSN)
    ok = fail = skip = 0
    try:
        with psycopg.connect(DSN) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    '''
                    UPDATE "行情增量"."更新运行"
                    SET "状态" = '运行中', "开始时间" = now()
                    WHERE "运行编号" = %s::uuid
                    ''',
                    (job_id,),
                )
                cur.execute(
                    '''
                    SELECT d."数据源", d."数据集", d."拉取源", d."更新函数", r."水位列"
                    FROM "行情增量"."更新运行明细" d
                    LEFT JOIN "行情增量"."数据集登记" r
                      ON r."数据源" = d."数据源" AND r."数据集" = d."数据集"
                    WHERE d."运行编号" = %s::uuid
                    ORDER BY d."数据集"
                    ''',
                    (job_id,),
                )
                cols = [d.name for d in cur.description]
                items = [dict(zip(cols, r)) for r in cur.fetchall()]
            conn.commit()

        for it in items:
            src, ds = it["数据源"], it["数据集"]
            fn_key = it["更新函数"]
            fn = resolve(fn_key)
            before = None
            with psycopg.connect(DSN) as conn:
                wm = get_watermark(conn, src, ds)
                before = wm.get("最后交易日") if wm else None
                with conn.cursor() as cur:
                    cur.execute(
                        '''
                        UPDATE "行情增量"."更新运行明细"
                        SET "状态"='运行中', "开始时间"=now(), "水位前最后日"=%s
                        WHERE "运行编号"=%s::uuid AND "数据源"=%s AND "数据集"=%s
                        ''',
                        (before, job_id, src, ds),
                    )
                conn.commit()

            t0 = datetime.now()
            try:
                if not fn:
                    raise RuntimeError(f"未注册: {fn_key}")
                msg = fn(updater) or "完成"
                # 仅当“看起来像失败且没有成功回退迹象”时抬升为失败
                soft_keys = ("登录失败", "黑名单用户", "获取失败", "Connection aborted", "接口不可用", "无法从水位")
                success_markers = ("新增", "已最新", "处理 ", "回退", "腾讯", "(EM)", "代码表", "刷新完成", "fhps", "即时")
                looks_bad = any(k in str(msg) for k in soft_keys)
                looks_ok = any(m in str(msg) for m in success_markers)
                if looks_bad and not looks_ok:
                    raise RuntimeError(msg)
                after = None
                advanced = None
                # 每次水位回写用独立连接，避免更新函数内失败事务污染
                with psycopg.connect(DSN, autocommit=True) as conn:
                    date_col = it.get("水位列")
                    if date_col and date_col not in ("updated_at", "更新时间", "trade_date"):
                        try:
                            refresh_watermark(
                                conn,
                                source=src,
                                dataset=ds,
                                schema=src,
                                table=ds,
                                date_col=date_col,
                                note=f"job:{job_id}",
                            )
                        except Exception as we:
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                            note_wm = f"（水位回写跳过: {type(we).__name__}）"
                        else:
                            note_wm = ""
                    else:
                        note_wm = ""
                    wm2 = get_watermark(conn, src, ds)
                    after = wm2.get("最后交易日") if wm2 else None
                    if before is not None and after is not None:
                        advanced = after > before
                    elif after is not None and before is None:
                        advanced = True
                    else:
                        advanced = False
                    status = "成功"
                    note = f"{msg}{note_wm}"
                    if advanced is False and before is not None:
                        note = f"{note}（水位未推进）"
                    elapsed = (datetime.now() - t0).total_seconds()
                    with conn.cursor() as cur:
                        cur.execute(
                            '''
                            UPDATE "行情增量"."更新运行明细"
                            SET "状态"=%s, "结束时间"=now(), "耗时秒"=%s,
                                "消息"=%s, "水位后最后日"=%s, "水位是否推进"=%s
                            WHERE "运行编号"=%s::uuid AND "数据源"=%s AND "数据集"=%s
                            ''',
                            (status, elapsed, note[:2000], after, advanced, job_id, src, ds),
                        )
                        cur.execute(
                            '''
                            UPDATE "行情增量"."同步水位"
                            SET "最近成功时间"=now(), "最近运行编号"=%s::uuid
                            WHERE "数据源"=%s AND "数据集"=%s
                            ''',
                            (job_id, src, ds),
                        )
                    conn.commit()
                ok += 1
            except Exception as e:
                fail += 1
                err = f"{type(e).__name__}: {e}"
                elapsed = (datetime.now() - t0).total_seconds()
                with psycopg.connect(DSN) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            '''
                            UPDATE "行情增量"."更新运行明细"
                            SET "状态"='失败', "结束时间"=now(), "耗时秒"=%s,
                                "错误"=%s, "消息"=%s
                            WHERE "运行编号"=%s::uuid AND "数据源"=%s AND "数据集"=%s
                            ''',
                            (elapsed, err[:2000], traceback.format_exc()[-1500:], job_id, src, ds),
                        )
                    conn.commit()

        final = "成功" if fail == 0 else ("失败" if ok == 0 else "部分失败")
        with psycopg.connect(DSN) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    '''
                    UPDATE "行情增量"."更新运行"
                    SET "状态"=%s, "结束时间"=now(),
                        "成功数"=%s, "失败数"=%s, "跳过数"=%s,
                        "摘要"=%s
                    WHERE "运行编号"=%s::uuid
                    ''',
                    (
                        final,
                        ok,
                        fail,
                        skip,
                        Json({"ok": ok, "fail": fail, "skip": skip}),
                        job_id,
                    ),
                )
            conn.commit()
    except Exception as e:
        with psycopg.connect(DSN) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    '''
                    UPDATE "行情增量"."更新运行"
                    SET "状态"='失败', "结束时间"=now(),
                        "摘要"=%s
                    WHERE "运行编号"=%s::uuid
                    ''',
                    (Json({"fatal": f"{type(e).__name__}: {e}"}), job_id),
                )
                cur.execute(
                    '''
                    UPDATE "行情增量"."更新运行明细"
                    SET "状态"='失败', "结束时间"=now(),
                        "错误"=COALESCE("错误", %s)
                    WHERE "运行编号"=%s::uuid AND "状态" IN ('排队','运行中')
                    ''',
                    (f"{type(e).__name__}: {e}"[:2000], job_id),
                )
            conn.commit()
    finally:
        with _lock:
            if _running_job == job_id:
                _running_job = None


def refresh_all_watermarks() -> dict:
    """调用现有全库水位脚本逻辑的轻量入口：重扫 database 责任方。"""
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "market_platform.data.脚本.刷新全库水位"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    return {
        "ok": r.returncode == 0,
        "exit": r.returncode,
        "stdout": (r.stdout or "")[-2000:],
        "stderr": (r.stderr or "")[-1000:],
    }
