#!/usr/bin/env bash
# 安装/更新 market-platform 的 systemd 用户服务与定时器
#
#   ./安装服务.sh            安装并启用统一门户(以及保留旧独立端口服务的能力)
#   ./安装服务.sh --all      额外启用三域独立端口服务与定时器(数据更新/指数预测周更)
#   ./安装服务.sh --dry-run  只打印将执行的动作
#
# 设计:仓库内单元用 @仓库根@ / @PYTHON@ 占位,安装时替换为真实路径,
#       因此换机器/换 Python 环境不需要手改单元文件。
set -euo pipefail

仓库根="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
用户单元="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
PYTHON="${MARKET_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  for 候选 in "$HOME/miniconda3/bin/python" "$(command -v python3 || true)"; do
    if [[ -n "$候选" && -x "$候选" ]] && "$候选" -c "import market_platform" >/dev/null 2>&1; then
      PYTHON="$候选"; break
    fi
  done
fi
[[ -n "$PYTHON" ]] || { echo "未找到已安装 market_platform 的 Python(可设 MARKET_PYTHON)" >&2; exit 1; }

全部=0; 演练=0
for 参数 in "$@"; do
  case "$参数" in
    --all) 全部=1 ;;
    --dry-run) 演练=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数: $参数" >&2; exit 1 ;;
  esac
done

安装单元() {
  local 模板="$1" 目标
  目标="$用户单元/$(basename "$模板")"
  echo "  安装 $(basename "$模板") → $目标"
  if [[ $演练 -eq 1 ]]; then return 0; fi
  mkdir -p "$用户单元"
  sed -e "s|@仓库根@|$仓库根|g" -e "s|@PYTHON@|$PYTHON|g" "$模板" > "$目标"
}

echo "仓库根: $仓库根"
echo "Python: $PYTHON"
echo "用户单元目录: $用户单元"
echo

echo "[1/3] 安装统一门户(唯一站点 + 三域 API,默认 :18800)"
安装单元 "$仓库根/deploy/systemd/市场平台门户.service"

if [[ $全部 -eq 1 ]]; then
  echo
  echo "[2/3] 安装三域独立端口服务 + 定时器"
  for 模板 in \
    "$仓库根/deploy/数据底座/systemd/database-api.service" \
    "$仓库根/deploy/行为分析/systemd/market-agents-api.service" \
    "$仓库根/deploy/指数预测/systemd/market-predictor-api.service" \
    "$仓库根/deploy/指数预测/systemd/market-predictor-update.service" \
    "$仓库根/deploy/指数预测/systemd/market-predictor-update.timer" \
    "$仓库根/deploy/指数预测/systemd/market-predictor-paper.service" \
    "$仓库根/deploy/指数预测/systemd/market-predictor-paper.timer" \
    "$仓库根/deploy/数据底座/systemd/数据更新.service" \
    "$仓库根/deploy/数据底座/systemd/数据更新.timer" ; do
    安装单元 "$模板"
  done

  echo
  echo "[3/3] 启用服务"
  if [[ $演练 -eq 0 ]]; then
    systemctl --user daemon-reload
    systemctl --user enable --now 市场平台门户.service
    systemctl --user enable --now market-predictor-update.timer market-predictor-paper.timer
    echo "  已启用:市场平台门户 + 指数预测定时器"
    echo "  未自动启用:数据更新.timer(启用前请确认上游数据源;命令:systemctl --user enable --now 数据更新.timer)"
  fi
else
  echo
  echo "[2/3] 跳过三域独立端口服务(--all 可一并安装)"
  echo "[3/3] 如需立即启用门户:"
  echo "  systemctl --user daemon-reload && systemctl --user enable --now 市场平台门户.service"
fi

echo
echo "提示:旧的三套前端预览服务(database-frontend / market-agents-frontend /"
echo "      market-predictor-frontend)已被统一门户取代,可停用:"
echo "      systemctl --user disable --now database-frontend.service market-agents-frontend.service market-predictor-frontend.service"
