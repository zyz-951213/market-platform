#!/usr/bin/env bash
# 将独立研究库并入「金融市场数据」中文 schema
# 用法: ./迁入研究库.sh [factor_latent|micro_cap_study|st_legacy_study|all]
set -euo pipefail

TARGET="金融市场数据"
ONLY="${1:-all}"

rewrite_dump() {
  local SCHEMA="$1"
  # 把 public. 全部改写到目标 schema；修正 search_path；丢掉 restrict 元命令
  sed -E \
    -e "/^\\\\restrict /d" \
    -e "/^\\\\unrestrict/d" \
    -e "/^CREATE SCHEMA public/d" \
    -e "/^ALTER SCHEMA public/d" \
    -e "/^CREATE EXTENSION/d" \
    -e "/^COMMENT ON EXTENSION/d" \
    -e "s/public\\./\"${SCHEMA}\"./g" \
    -e "s/^SET search_path = .*/SET search_path = \"${SCHEMA}\";/" \
    -e "s/^SELECT pg_catalog.set_config\\('search_path'.*/SELECT pg_catalog.set_config('search_path', '\"${SCHEMA}\"', false);/"
}

migrate_one() {
  local SRC="$1"
  local SCHEMA="$2"
  echo "======== $SRC → $TARGET.$SCHEMA ========"
  psql -d "$TARGET" -v ON_ERROR_STOP=1 -c "
    CREATE SCHEMA IF NOT EXISTS public;
    CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
    DROP SCHEMA IF EXISTS \"$SCHEMA\" CASCADE;
    CREATE SCHEMA \"$SCHEMA\";
  "

  pg_dump -d "$SRC" --no-owner --no-privileges --format=plain \
    | rewrite_dump "$SCHEMA" \
    | psql -d "$TARGET" -v ON_ERROR_STOP=1

  local n
  n=$(psql -d "$TARGET" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema='${SCHEMA}' AND table_type='BASE TABLE'")
  echo "完成 $SRC → $SCHEMA （${n} 张表）"
}

case "$ONLY" in
  factor_latent) migrate_one factor_latent 隐因子 ;;
  micro_cap_study) migrate_one micro_cap_study 微盘股研究 ;;
  st_legacy_study) migrate_one st_legacy_study 特别处理研究 ;;
  all)
    migrate_one factor_latent 隐因子
    migrate_one micro_cap_study 微盘股研究
    migrate_one st_legacy_study 特别处理研究
    ;;
  *) echo "未知参数: $ONLY"; exit 1 ;;
esac

echo "全部完成"
