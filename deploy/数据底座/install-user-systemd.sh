#!/usr/bin/env bash
# 安装数据库管理系统用户级 systemd 服务（API + 前端）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
USER_SYSTEMD="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$USER_SYSTEMD" "$REPO_ROOT/logs"

install -m 644 "$REPO_ROOT/deploy/systemd/database-api.service" "$USER_SYSTEMD/database-api.service"
install -m 644 "$REPO_ROOT/deploy/systemd/database-frontend.service" "$USER_SYSTEMD/database-frontend.service"

systemctl --user daemon-reload
systemctl --user enable --now database-api.service
systemctl --user enable --now database-frontend.service

echo "==> 已启动"
echo "  API:      http://127.0.0.1:18766"
echo "  Frontend: http://127.0.0.1:19081"
echo "  状态: systemctl --user status database-api database-frontend"
echo "  日志: $REPO_ROOT/logs/"
