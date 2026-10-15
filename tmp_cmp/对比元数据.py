#!/usr/bin/env python3
"""本地 vs 云端 全库表级元数据对比：行数 + 各日期列最大值。

产出:
  tmp_cmp/local.tsv / cloud.tsv   schema|table|metric|value
用法: python3 tmp_cmp/对比元数据.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = "金融市场数据"
SSH_KEY = "/home/zhuyizhou/.ssh/id_ed25519"
SSH_HOST = "root@60.205.187.227"
SSH = [
    "ssh", "-F", "/dev/null", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
    "-o", "StrictHostKeyChecking=no", "-i", SSH_KEY, SSH_HOST,
]
DATE_TYPES = ("date", "timestamp without time zone", "timestamp with time zone")


def local_psql(sql: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["psql", "-d", DB, "-X", "-tA", "-F", "|", "-f", "-"],
        input=sql, capture_output=True, text=True,
    )


def remote_psql(sql: str) -> subprocess.CompletedProcess:
    cmd = f"sudo -u postgres psql -d '{DB}' -X -tA -F'|' -f -"
    return subprocess.run(SSH + [cmd], input=sql, capture_output=True, text=True)


def table_meta_sql(side: str) -> str:
    """取该侧所有物理表 + 日期列清单, 生成 count/max 查询 SQL。"""
    q = (
        "SELECT table_schema||'.'||table_name||'|'||column_name "
        "FROM information_schema.columns "
        "WHERE table_schema NOT IN ('pg_catalog','information_schema') "
        f"AND data_type IN ({','.join(chr(39) + t + chr(39) for t in DATE_TYPES)}) "
        "ORDER BY 1"
    )
    runner = local_psql if side == "local" else remote_psql
    p = runner(q)
    if p.returncode != 0:
        sys.exit(f"[{side}] 取列清单失败: {p.stderr[:500]}")

    dates: dict[str, list[str]] = {}
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        tbl, col = line.rsplit("|", 1)
        dates.setdefault(tbl, []).append(col)

    # 表清单（含无日期列的表）
    q2 = (
        "SELECT table_schema||'.'||table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('pg_catalog','information_schema') "
        "AND table_type='BASE TABLE' ORDER BY 1"
    )
    p2 = runner(q2)
    if p2.returncode != 0:
        sys.exit(f"[{side}] 取表清单失败: {p2.stderr[:500]}")
    tables = [t.strip() for t in p2.stdout.splitlines() if t.strip()]

    def ident(qualified: str) -> str:
        sch, tbl = qualified.split(".", 1)
        return f"\"{sch}\".\"{tbl}\""

    stmts = []
    for t in tables:
        key = f"'{t}'"
        stmts.append(
            f"SELECT {key},'rows',count(*)::text FROM {ident(t)};"
        )
        for col in dates.get(t, []):
            stmts.append(
                f"SELECT {key},'max:{col}',COALESCE(max(\"{col}\")::text,'NULL') "
                f"FROM {ident(t)};"
            )
    return "\n".join(stmts) + "\n"


def main() -> None:
    for side in ("local", "cloud"):
        sql = table_meta_sql(side)
        sql_file = HERE / f"{side}.sql"
        sql_file.write_text(sql, encoding="utf-8")
        print(f"[{side}] SQL {len(sql.splitlines())} 条 -> {sql_file.name}", flush=True)
        runner = local_psql if side == "local" else remote_psql
        p = runner(sql)
        out = HERE / f"{side}.tsv"
        out.write_text(p.stdout, encoding="utf-8")
        if p.stderr.strip():
            (HERE / f"{side}.err").write_text(p.stderr, encoding="utf-8")
        print(f"[{side}] 结果 {len(p.stdout.splitlines())} 行 (rc={p.returncode})", flush=True)


if __name__ == "__main__":
    main()
