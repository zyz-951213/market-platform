#!/usr/bin/env bash
# 安装 Market Predictor 开机自启服务（需 root）
# 用法: sudo ./deploy/install-systemd.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "请使用 root 运行: sudo $0"
  exit 1
fi

echo "==> 项目目录: $REPO_ROOT"

# 自动定位 market-predictor（支持 conda / venv / PATH）
MARKET_PREDICTOR=""
if command -v market-predictor >/dev/null 2>&1; then
  MARKET_PREDICTOR="$(command -v market-predictor)"
elif [[ -x "$REPO_ROOT/.venv/bin/market-predictor" ]]; then
  MARKET_PREDICTOR="$REPO_ROOT/.venv/bin/market-predictor"
else
  echo "错误: 找不到 market-predictor，请先执行: pip install -e ."
  exit 1
fi
echo "==> CLI: $MARKET_PREDICTOR"

NPM_BIN=""
if command -v npm >/dev/null 2>&1; then
  NPM_BIN="$(command -v npm)"
else
  echo "错误: 未安装 npm，无法启动前端服务"
  exit 1
fi

if [[ ! -f "$REPO_ROOT/.env" ]]; then
  echo "警告: 未找到 .env，请复制 .env.example 并配置 DEEPSEEK_API_KEY"
fi

echo "==> 构建前端"
(cd "$REPO_ROOT/前端" && npm ci && npm run build)

install_unit() {
  local src="$1"
  local name="$2"
  sed -e "s|@REPO_ROOT@|$REPO_ROOT|g" \
      -e "s|@MARKET_PREDICTOR@|$MARKET_PREDICTOR|g" \
      -e "s|@NPM_BIN@|$NPM_BIN|g" \
      "$src" > "$SYSTEMD_DIR/$name"
  echo "  已安装 $name"
}

echo "==> 安装 systemd 单元"
install_unit "$SCRIPT_DIR/systemd/market-predictor-api.service" "market-predictor-api.service"
install_unit "$SCRIPT_DIR/systemd/market-predictor-frontend.service" "market-predictor-frontend.service"
install_unit "$SCRIPT_DIR/systemd/market-predictor-paper.service" "market-predictor-paper.service"
install_unit "$SCRIPT_DIR/systemd/market-predictor-paper.timer" "market-predictor-paper.timer"
install_unit "$SCRIPT_DIR/systemd/market-predictor-update.service" "market-predictor-update.service"
install_unit "$SCRIPT_DIR/systemd/market-predictor-update.timer" "market-predictor-update.timer"

systemctl daemon-reload

echo "==> 启用并启动服务"
systemctl enable --now market-predictor-api.service
systemctl enable --now market-predictor-frontend.service
systemctl enable --now market-predictor-update.timer
# paper 已并入 update，可选单独启用: systemctl enable --now market-predictor-paper.timer

echo ""
echo "完成！"
echo "  API:      http://localhost:18765/api/health"
echo "  前端:     http://localhost:19080"
echo "  模拟实盘: 工作日 16:30（market-predictor-paper.timer）"
echo "  自动更新: 工作日 16:45（market-predictor-update.timer）"
echo ""
echo "常用命令:"
echo "  systemctl status market-predictor-api"
echo "  systemctl status market-predictor-frontend"
echo "  systemctl list-timers market-predictor-paper.timer"
echo "  market-predictor update --once    # 手动触发自动更新"
