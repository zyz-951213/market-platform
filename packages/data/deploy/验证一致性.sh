#!/usr/bin/env bash
# 本地 vs 云端 全表一致性验证（三层结构：公共 / 项目 / 行情增量）
# 用法: ./验证一致性.sh
# 增量表比 max(增量键), 全量表比 count(*)
set -uo pipefail

SERVER="root@60.205.187.227"
SSH_OPTS="-o ConnectTimeout=15 -o StrictHostKeyChecking=no"
OUT_LOCAL="/tmp/verify_local.txt"
OUT_CLOUD="/tmp/verify_cloud.txt"

if [[ -z "${SSHPASS:-}" ]]; then
  echo "需要 SSHPASS 环境变量" >&2
  exit 1
fi

# ---------- 表清单: 库名|schema|表名|增量键(空=全量count) ----------
# 与 推送增量.sh 对齐（三层结构：公共 + 项目 + 行情增量）
TABLES=(
  # 公共主数据 / 行为分析
  "金融市场数据|公共|证券档案|"
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
  "金融市场数据|行为分析|用户|"
  "金融市场数据|行为分析|组合|"
  # 指数预测
  "金融市场数据|指数预测|时序数据|交易日期"
  "金融市场数据|指数预测|回测信号|交易日期"
  "金融市场数据|指数预测|指数特征|交易日期"
  "金融市场数据|指数预测|大模型缓存|行情日期"
  "金融市场数据|指数预测|数据集快照|更新时间"
  "金融市场数据|指数预测|拉取元数据|更新时间"
  "金融市场数据|指数预测|应用状态|更新时间"
  "金融市场数据|指数预测|回测记录|保存时间"
  # 公共: 日频/事件
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
  # 公共: 宏观/财务 (全量)
  "金融市场数据|公共|人民币存款准备金率表|"
  "金融市场数据|公共|各层次货币供应量月度文件|"
  "金融市场数据|公共|固定资产投资表(月)|"
  "金融市场数据|公共|国内生产总值表(季)|"
  "金融市场数据|公共|央行公开市场业务|"
  "金融市场数据|公共|居民消费价格涨幅表(月)|"
  "金融市场数据|公共|工业生产者出厂价格涨幅表(月)|"
  "金融市场数据|公共|社会消费品零售总额表(月)|"
  "金融市场数据|公共|规模以上工业增加值增速表(月)|"
  "金融市场数据|公共|采购经理人指数表|"
  "金融市场数据|公共|利润表|"
  "金融市场数据|公共|现金流量表(合并)|"
  "金融市场数据|公共|资产负债表|"
  "金融市场数据|公共|年、中、季报基本情况文件|"
  "金融市场数据|公共|预披露日期表|"
  # 行情增量元数据
  "金融市场数据|行情增量|数据集登记|"
  "金融市场数据|行情增量|同步水位|"
)

rm -f "$OUT_LOCAL" "$OUT_CLOUD"

# 生成按库分组的查询脚本
gen_sql() {
  # $1 = 输出文件; $2 = "local" 或 "remote"
  local out="$1"
  local mode="$2"
  local cur_db=""
  : > "${out}.sql"
  for entry in "${TABLES[@]}"; do
    IFS='|' read -r db schema tbl key <<< "$entry"
    quoted="\"$schema\".\"$tbl\""
    if [[ "$db" != "$cur_db" ]]; then
      if [[ -n "$cur_db" ]]; then echo "" >> "${out}.sql"; fi
      echo "-- DB: $db" >> "${out}.sql"
      cur_db="$db"
    fi
    if [[ -n "$key" ]]; then
      echo "SELECT '${db}|${schema}.${tbl}|' || COALESCE(max(\"$key\")::text, 'NULL') FROM $quoted;" >> "${out}.sql"
    else
      echo "SELECT '${db}|${schema}.${tbl}|rows:' || count(*)::text FROM $quoted;" >> "${out}.sql"
    fi
  done
}

echo "=== 生成查询脚本 ==="
gen_sql "$OUT_LOCAL" "local"
echo "本地查询: $(wc -l < "${OUT_LOCAL}.sql") 行 SQL"

echo "=== 本地执行 ==="
cur_db=""
while IFS= read -r line; do
  if [[ "$line" == "-- DB: "* ]]; then
    cur_db="${line#-- DB: }"
  elif [[ -n "$line" && -n "$cur_db" ]]; then
    psql -d "$cur_db" -X -q -tA -c "$line" >> "$OUT_LOCAL" 2>/dev/null || echo "${cur_db}|ERROR|查询失败" >> "$OUT_LOCAL"
  fi
done < "${OUT_LOCAL}.sql"
echo "本地完成: $(wc -l < "$OUT_LOCAL") 条结果"

echo "=== 云端执行 ==="
# 上传 SQL, 云端按库执行
sshpass -e scp $SSH_OPTS "${OUT_LOCAL}.sql" "$SERVER:/tmp/verify.sql"
sshpass -e ssh $SSH_OPTS "$SERVER" 'bash -s' << 'REMOTE' > "$OUT_CLOUD" 2>/dev/null
cur_db=""
while IFS= read -r line; do
  if [[ "$line" == "-- DB: "* ]]; then
    cur_db="${line#-- DB: }"
  elif [[ -n "$line" && -n "$cur_db" ]]; then
    sudo -u postgres psql -d "$cur_db" -X -q -tA -c "$line" 2>/dev/null || echo "${cur_db}|ERROR|查询失败"
  fi
done < /tmp/verify.sql
REMOTE
echo "云端完成: $(wc -l < "$OUT_CLOUD") 条结果"

echo ""
echo "════════ 对比结果 ════════"
if diff <(sort "$OUT_LOCAL") <(sort "$OUT_CLOUD") > /tmp/verify_diff.txt; then
  echo "✅ 全部一致 ($(wc -l < "$OUT_LOCAL") 张表)"
else
  echo "❌ 存在差异, 如下:"
  cat /tmp/verify_diff.txt
fi
