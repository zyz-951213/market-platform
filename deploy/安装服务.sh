#!/usr/bin/env bash
# 安装/更新 market-platform 的 systemd 用户服务与定时器
#
#   ./安装服务.sh            安装统一门户(推荐)
#   ./安装服务.sh --all      额外安装三域独立端口服务与定时器
#   ./安装服务.sh --dry-run  只打印将执行的动作
#
# 设计:仓库内单元用 @仓库根@ / @PYTHON@ 占位,安装时替换为真实路径,
#       换机器/换 Python 环境不需要手改单元文件。
#
# 注意:bash 变量名与环境变量名、systemd 单元名**必须是 ASCII**(中文名会报"未找到命令"),
#       故本脚本内部一律用英文变量名。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

PYTHON="${MARKET_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  for candidate in "$HOME/miniconda3/bin/python" "$(command -v python3 || true)"; do
    if [[ -n "$candidate" && -x "$candidate" ]] && "$candidate" -c "import market_platform" >/dev/null 2>&1; then
      PYTHON="$candidate"
      break
    fi
  done
fi
if [[ -z "$PYTHON" ]]; then
  echo "未找到已安装 market_platform 的 Python(可设 MARKET_PYTHON=/path/to/python)" >&2
  exit 1
fi

INSTALL_ALL=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --all) INSTALL_ALL=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "未知参数: $arg" >&2; exit 1 ;;
  esac
done

install_unit() {
  local template="$1" target
  target="$UNIT_DIR/$(basename "$template")"
  echo "  $(basename "$template") → $target"
  if [[ "$DRY_RUN" -eq 1 ]]; then return 0; fi
  mkdir -p "$UNIT_DIR"
  sed -e "s|@仓库根@|$REPO_ROOT|g" -e "s|@PYTHON@|$PYTHON|g" "$template" > "$target"
}

echo "仓库根      : $REPO_ROOT"
echo "Python      : $PYTHON"
echo "用户单元目录: $UNIT_DIR"
echo

echo "[1/3] 统一门户(唯一站点 + 三域 API,默认 :18800)"
install_unit "$REPO_ROOT/deploy/systemd/market-portal.service"

if [[ "$INSTALL_ALL" -eq 1 ]]; then
  echo
  echo "[2/3] 三域独立端口服务 + 定时器"
  for template in \
    "$REPO_ROOT/deploy/数据底座/systemd/database-api.service" \
    "$REPO_ROOT/deploy/数据底座/systemd/market-data-update.service" \
    "$REPO_ROOT/deploy/数据底座/systemd/market-data-update.timer" \
    "$REPO_ROOT/deploy/行为分析/systemd/market-agents-api.service" \
    "$REPO_ROOT/deploy/指数预测/systemd/market-predictor-api.service" \
    "$REPO_ROOT/deploy/指数预测/systemd/market-predictor-update.service" \
    "$REPO_ROOT/deploy/指数预测/systemd/market-predictor-update.timer" \
    "$REPO_ROOT/deploy/指数预测/systemd/market-predictor-paper.service" \
    "$REPO_ROOT/deploy/指数预测/systemd/market-predictor-paper.timer" ; do
    install_unit "$template"
  done

  echo
  echo "[3/3] 重载并启用"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    systemctl --user daemon-reload
    systemctl --user enable --now market-portal.service
    systemctl --user enable --now market-predictor-update.timer market-predictor-paper.timer
    echo "  已启用:市场平台门户 + 指数预测定时器"
    echo "  未启用:market-data-update.timer(启用前请确认上游数据源可用)"
    echo "          systemctl --user enable --now market-data-update.timer"
  fi
else
  echo
  echo "[2/3] 跳过三域独立端口服务(需要时加 --all)"
  echo "[3/3] 手工启用门户:"
  echo "  systemctl --user daemon-reload && systemctl --user enable --now market-portal.service"
fi

echo
echo "旧的三套前端预览服务已被统一门户取代,可停用:"
echo "  systemctl --user disable --now database-frontend.service market-agents-frontend.service market-predictor-frontend.service"
