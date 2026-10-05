#!/usr/bin/env python3
"""按序应用 schema/迁移/*.sql，已应用版本跳过。"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg

DSN = "postgresql:///%E9%87%91%E8%9E%8D%E5%B8%82%E5%9C%BA%E6%95%B0%E6%8D%AE"
MIG_DIR = Path(__file__).resolve().parent / "迁移"


def main() -> int:
    files = sorted(MIG_DIR.glob("*.sql"))
    if not files:
        print("无迁移文件")
        return 0

    bootstrap = MIG_DIR / "000_迁移记录表.sql"
    with psycopg.connect(DSN) as conn:
        conn.execute(bootstrap.read_text(encoding="utf-8"))
        conn.commit()

        for path in files:
            ver = path.stem
            row = conn.execute(
                'SELECT 1 FROM "行情增量"."schema迁移" WHERE "版本" = %s', (ver,)
            ).fetchone()
            if row:
                print(f"[skip] {ver}")
                continue
            print(f"[apply] {ver} …", flush=True)
            try:
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute(
                        'INSERT INTO "行情增量"."schema迁移" ("版本", "说明") VALUES (%s, %s)',
                        (ver, path.name),
                    )
            except Exception as e:
                print(f"[FAIL] {ver}: {e}", file=sys.stderr)
                return 1
            print(f"[ok]   {ver}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
