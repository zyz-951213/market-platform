#!/usr/bin/env bash
# ============================================================
# 本地 → 云端 增量数据推送脚本 v2 (文件传输版, 无 shell 引号问题)
# 用法: ./推送增量.sh [--days N] [--only 库名] [--dry-run]
#   --days N    增量窗口天数(默认 10,覆盖周末+容错)
#   --only xxx  只推指定库(market_agents|market_predictor|金融市场数据)
#   --dry-run   只打印要执行的命令,不真正传输
#   --yes       确认执行(远端含 TRUNCATE 时必须显式给出,见下方预检)
# 原理: 大表按 date 键导出窗口内数据 → scp 上传 → 云端 DELETE 窗口内 + COPY 导入(幂等)
#        小表/配置表全量 TRUNCATE + COPY
# ============================================================
set -uo pipefail

SERVER="root@60.205.187.227"
SSH_OPTS="-o ConnectTimeout=15 -o StrictHostKeyChecking=no"
WINDOW_DAYS=10
ONLY=""
DRY_RUN=0
CONFIRMED=0
TMPDIR_LOCAL="/tmp/推送增量"
TMPDIR_REMOTE="/tmp/推送增量"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --days) WINDOW_DAYS="$2"; shift 2 ;;
    --only) ONLY="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes) CONFIRMED=1; shift ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

if [[ -z "${SSHPASS:-}" ]]; then
  echo "需要 SSHPASS 环境变量(云端 root 密码)" >&2
  exit 1
fi

# ---------- 表清单: 库名|schema|表名|增量键(空=全量)|upsert主键(父表用, 避免TRUNCATE炸外键) ----------
# 三层结构后原始仓均在「公共」；行为分析原 JSON 标的时序已拆为参与者活跃度+分钟日特征
TABLES=(
  # ===== 公共主数据 / 行为分析 =====
  "金融市场数据|公共|证券档案||证券代码"
  "金融市场数据|行为分析|参与者活跃度|交易日期"
  "金融市场数据|行为分析|分钟日特征|交易日期"
  "金融市场数据|行为分析|日行情|交易日期"
  "金融市场数据|行为分析|资金流向|交易日期"
  "金融市场数据|行为分析|策略净值|交易日期"
  "金融市场数据|行为分析|市场时序|交易日期"
  "金融市场数据|行为分析|组合净值|交易日期"
  "金融市场数据|行为分析|标的摘要|分析时间"
  "金融市场数据|行为分析|标的产物|更新时间"
  "金融市场数据|行为分析|行业摘要|更新时间"
  "金融市场数据|行为分析|应用状态|更新时间"
  "金融市场数据|行为分析|用户||"
  "金融市场数据|行为分析|组合||组合编号"
  # ===== 指数预测 =====
  "金融市场数据|指数预测|时序数据|交易日期"
  "金融市场数据|指数预测|回测记录|保存时间|运行编号"
  "金融市场数据|指数预测|回测信号|交易日期|运行编号,交易日期"
  "金融市场数据|指数预测|指数特征|交易日期"
  "金融市场数据|指数预测|大模型缓存|行情日期"
  "金融市场数据|指数预测|数据集快照|更新时间"
  "金融市场数据|指数预测|拉取元数据|更新时间"
  "金融市场数据|指数预测|应用状态|更新时间"
  # ===== 公共: 日频/事件表按日期增量 =====
  "金融市场数据|公共|个股日行情文件|交易日期"
  "金融市场数据|公共|国内指数日行情文件|交易日期"
  "金融市场数据|公共|日市场回报率文件|交易日期"
  "金融市场数据|公共|公司文件|上市日期"
  "金融市场数据|公共|上市状态变更表|执行日期"
  "金融市场数据|公共|日历文件|日历日期"
  "金融市场数据|公共|分配文件|除权(息)日期"
  "金融市场数据|公共|上市公司基本信息年度表|统计截止日期"
  "金融市场数据|公共|指数基本信息文件|开始日期"
  "金融市场数据|公共|个股资金流向|交易日期"
  "金融市场数据|公共|融资融券|交易日期"
  "金融市场数据|公共|龙虎榜|交易日期"
  "金融市场数据|公共|龙虎榜营业部|交易日期"
  "金融市场数据|公共|国内期货日交易数据|交易日期"
  "金融市场数据|公共|ETF融资融券交易明细表|交易日期"
  "金融市场数据|公共|ETF基金申购赎回清单|交易日期"
  "金融市场数据|公共|汇率文件|汇率日期"
  "金融市场数据|公共|无风险利率文件|统计日期"
  "金融市场数据|公共|国内商品期货基本信息表|首日上市日期"
  # ===== 公共: 宏观/财务低频表全量 =====
  "金融市场数据|公共|人民币存款准备金率表||"
  "金融市场数据|公共|各层次货币供应量月度文件||"
  "金融市场数据|公共|固定资产投资表(月)||"
  "金融市场数据|公共|国内生产总值表(季)||"
  "金融市场数据|公共|央行公开市场业务||"
  "金融市场数据|公共|居民消费价格涨幅表(月)||"
  "金融市场数据|公共|工业生产者出厂价格涨幅表(月)||"
  "金融市场数据|公共|社会消费品零售总额表(月)||"
  "金融市场数据|公共|规模以上工业增加值增速表(月)||"
  "金融市场数据|公共|采购经理人指数表||"
  "金融市场数据|公共|利润表||"
  "金融市场数据|公共|现金流量表(合并)||"
  "金融市场数据|公共|资产负债表||"
  "金融市场数据|公共|年、中、季报基本情况文件||"
  "金融市场数据|公共|预披露日期表||"
  # ===== 行情增量元数据（全量）=====
  "金融市场数据|行情增量|数据集登记||数据源,数据集"
  "金融市场数据|行情增量|同步水位||数据源,数据集"
)

# ---------- 执行前预检(TASK-003 D8)----------
# 远端镜像库上的破坏性动作必须先看得见:TRUNCATE(小表/配置表全量)会清空整表,
# DELETE 窗口只清增量窗。含 TRUNCATE 时要求显式 --yes,避免"以为只是推送增量"。
TRUNCATE_TABLES=(); DELETE_TABLES=(); UPSERT_TABLES=()
for entry in "${TABLES[@]}"; do
  IFS='|' read -r _db _schema _tbl _key _upsert <<< "$entry"
  if [[ -n "$ONLY" && "$_db" != "$ONLY" ]]; then continue; fi
  if [[ -n "$_key" ]]; then DELETE_TABLES+=("$_schema.$_tbl")
  elif [[ -n "$_upsert" ]]; then UPSERT_TABLES+=("$_schema.$_tbl")
  else TRUNCATE_TABLES+=("$_schema.$_tbl"); fi
done

  echo "════════ 执行前预检(远端镜像库)════════"
  echo "库: $SERVER"
  echo "窗口: 最近 ${WINDOW_DAYS} 天"
  echo "· TRUNCATE(整表清空): ${#TRUNCATE_TABLES[@]} 张"
  printf '    %s\n' "${TRUNCATE_TABLES[@]:-无}"
  echo "· DELETE 窗口      : ${#DELETE_TABLES[@]} 张"
  echo "· 全量 upsert      : ${#UPSERT_TABLES[@]} 张"
  echo "说明:本地生产库不做任何 DELETE/TRUNCATE;上述动作只发生在云端镜像库。"
  if [[ $DRY_RUN -eq 0 && ${#TRUNCATE_TABLES[@]} -gt 0 && $CONFIRMED -eq 0 ]]; then
    echo "" >&2
    echo "拒绝执行:本次包含 ${#TRUNCATE_TABLES[@]} 张表的远端 TRUNCATE。" >&2
    echo "请先 --dry-run 预览,确认后用 --yes 执行。" >&2
    exit 3
  fi
  echo "═══════════════════════════════════════"



mkdir -p "$TMPDIR_LOCAL"
if [[ $DRY_RUN -eq 1 ]]; then
  # dry-run 必须完全离线可预览(TASK-003 D8):此前仍会 SSH 建远端临时目录
  echo "(dry-run) 跳过远端临时目录创建"
else
  sshpass -e ssh $SSH_OPTS "$SERVER" "mkdir -p $TMPDIR_REMOTE && chown postgres:postgres $TMPDIR_REMOTE" || { echo "云端临时目录创建失败" >&2; exit 1; }
fi


ok=0; fail=0; skipped=0
FAILED_TABLES=()

for entry in "${TABLES[@]}"; do
  IFS='|' read -r db schema tbl key upsert_pk <<< "$entry"
  if [[ -n "$ONLY" && "$db" != "$ONLY" ]]; then skipped=$((skipped+1)); continue; fi

  quoted="\"$schema\".\"$tbl\""
  cut_ts="CURRENT_DATE - INTERVAL '${WINDOW_DAYS} days'"
  # 中文表名经 tr 会全部变成 '_' 互相冲突（如 利润表↔龙虎榜）；用 md5+pid 保证唯一
  safe_name="$(printf '%s' "${schema}.${tbl}" | md5sum | awk '{print $1}')_$$"
  data_file_local="$TMPDIR_LOCAL/${safe_name}.tsv"
  data_file_remote="$TMPDIR_REMOTE/${safe_name}.tsv"

  if [[ -n "$key" ]]; then
    where="WHERE \"$key\" >= $cut_ts"
    mode="增量(${WINDOW_DAYS}天,$key)"
    del_sql="DELETE FROM $quoted WHERE \"$key\" >= $cut_ts;"
  elif [[ -n "$upsert_pk" ]]; then
    where=""
    mode="全量-upsert($upsert_pk)"
    del_sql=""  # upsert 不需要先删, 见下方 SQL
  else
    where=""
    mode="全量"
    del_sql="TRUNCATE TABLE $quoted;"
  fi

  echo "──────────────────────────────────────────────"
  echo "[$db] $quoted  ← $mode"

  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  (dry-run) 跳过传输"
    continue
  fi

  # 1. 本地导出到临时文件
  if ! psql -d "$db" -X -q -A -t -c "COPY (SELECT * FROM $quoted $where) TO STDOUT" > "$data_file_local" 2>"${data_file_local}.err"; then
    fail=$((fail+1)); FAILED_TABLES+=("$db.$quoted(本地导出)")
    echo "  ❌ 本地导出失败"; tail -3 "${data_file_local}.err"
    rm -f "$data_file_local" "${data_file_local}.err"
    continue
  fi
  rows=$(wc -l < "$data_file_local")

  # 2. 生成 SQL 脚本(数据文件用绝对路径, psql -f 执行, 无 shell 引号传递)
  sql_file_local="$TMPDIR_LOCAL/${safe_name}.sql"
  sql_file_remote="$TMPDIR_REMOTE/${safe_name}.sql"
  if [[ -n "$upsert_pk" ]]; then
    # upsert 模式: 动态取列名 → INSERT ... ON CONFLICT (pk) DO UPDATE
    # upsert_pk 支持逗号分隔复合主键(如 "run_id,date"); 导出行按增量键窗口过滤
    cols=$(psql -d "$db" -X -q -A -t -c "SELECT string_agg(quote_ident(column_name), ',') FROM information_schema.columns WHERE table_schema='$schema' AND table_name='$tbl'")
    # 排除所有主键列(复合键用 IN)
    pk_list=$(echo "$upsert_pk" | tr ',' '\n' | sed 's/^/'"'"'/;s/$/'"'"'/' | paste -sd, -)
    upd_cols=$(psql -d "$db" -X -q -A -t -c "SELECT string_agg(quote_ident(column_name)||'=EXCLUDED.'||quote_ident(column_name), ',') FROM information_schema.columns WHERE table_schema='$schema' AND table_name='$tbl' AND column_name NOT IN ($pk_list)")
    conflict_cols=$(echo "$upsert_pk" | tr ',' '\n' | sed 's/^/"/;s/$/"/' | paste -sd, -)
    {
      echo "BEGIN;"
      echo "CREATE TEMP TABLE _push_$safe_name (LIKE $quoted) ON COMMIT DROP;"
      echo "COPY _push_$safe_name FROM '$data_file_remote';"
      echo "INSERT INTO $quoted ($cols) SELECT $cols FROM _push_$safe_name ON CONFLICT ($conflict_cols) DO UPDATE SET $upd_cols;"
      echo "COMMIT;"
    } > "$sql_file_local"
  else
    {
      echo "$del_sql"
      echo "COPY $quoted FROM '$data_file_remote';"
    } > "$sql_file_local"
  fi

  # 3. scp 上传数据 + SQL
  if ! sshpass -e scp $SSH_OPTS "$data_file_local" "$sql_file_local" "$SERVER:$TMPDIR_REMOTE/" 2>"${data_file_local}.scperr"; then
    fail=$((fail+1)); FAILED_TABLES+=("$db.$quoted(上传)")
    echo "  ❌ scp 上传失败"; tail -3 "${data_file_local}.scperr"
    rm -f "$data_file_local" "$sql_file_local" "${data_file_local}.err" "${data_file_local}.scperr"
    continue
  fi

  # 4. 云端: chown + psql -f (单引号包裹的固定模板, 表名/库名都在 SQL 文件里)
  remote_cmd="chown postgres:postgres '$data_file_remote' '$sql_file_remote' && sudo -u postgres psql -d '$db' -X -q -v ON_ERROR_STOP=1 -f '$sql_file_remote' && sudo -u postgres psql -d '$db' -X -tA -c 'SELECT count(*) FROM $quoted' | head -1"
  if sshpass -e ssh $SSH_OPTS "$SERVER" "$remote_cmd" >"${data_file_local}.sshout" 2>&1; then
    cp "${data_file_local}.sshout" "/tmp/sshout_${safe_name}.log" 2>/dev/null
    echo "RC=0" >> "/tmp/sshout_${safe_name}.log"
    ok=$((ok+1))
    echo "  ✅ 成功 ($rows 行)"
  else
    fail=$((fail+1)); FAILED_TABLES+=("$db.$quoted")
    echo "  ❌ 云端导入失败 ($rows 行导出, 但导入报错)"
    tail -4 "${data_file_local}.sshout"
  fi

  # 清理本地临时文件
  rm -f "$data_file_local" "${data_file_local}.err" "${data_file_local}.scperr" "${data_file_local}.sshout"
done

echo ""
echo "══════════════════════════════════════════════"
echo "完成: 成功 $ok | 失败 $fail | 跳过 $skipped"
if [[ $fail -gt 0 ]]; then
  printf '失败表: %s\n' "${FAILED_TABLES[@]}"
  exit 1
fi
