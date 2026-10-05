#!/usr/bin/env bash
# 安装 cpolar 用户级 systemd 服务（与 market-predictor 前端联动）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CPOLAR_BIN="${CPOLAR_BIN:-$HOME/.local/bin/cpolar}"
CPOLAR_CONFIG="${CPOLAR_CONFIG:-$HOME/.cpolar/cpolar.yml}"
USER_SYSTEMD="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [[ ! -x "$CPOLAR_BIN" ]]; then
  echo "错误: 未找到 cpolar，请先安装: https://www.cpolar.com/download"
  exit 1
fi

mkdir -p "$(dirname "$CPOLAR_CONFIG")" "$USER_SYSTEMD"

if [[ ! -f "$CPOLAR_CONFIG" ]]; then
  cp "$SCRIPT_DIR/cpolar.yml.example" "$CPOLAR_CONFIG"
  echo "已创建 $CPOLAR_CONFIG，请编辑 authtoken 后重新运行本脚本"
  exit 1
fi

if grep -q 'YOUR_CPOLAR_AUTHTOKEN' "$CPOLAR_CONFIG" 2>/dev/null; then
  echo "错误: 请先在 $CPOLAR_CONFIG 中配置 authtoken"
  exit 1
fi

if ! grep -q 'subdomain: your-tunnel-1' "$CPOLAR_CONFIG"; then
  echo "警告: $CPOLAR_CONFIG 中未找到 subdomain: your-tunnel-1，请手动添加 market-predictor 隧道"
fi

cat > "$USER_SYSTEMD/cpolar.service" <<EOF
[Unit]
Description=Cpolar Tunnel Service (market-predictor your-tunnel-1)
After=network-online.target market-predictor-frontend.service
Wants=network-online.target
Requires=market-predictor-frontend.service

[Service]
Type=simple
ExecStart=$CPOLAR_BIN start-all -config=$CPOLAR_CONFIG -log=stdout
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now cpolar.service

echo ""
echo "完成！"
echo "  本地前端: http://localhost:19080"
echo "  公网地址: https://your-tunnel-1.vip.cpolar.cn （以 cpolar 后台状态为准）"
echo "  隧道状态: http://127.0.0.1:4040"
echo ""
echo "常用命令:"
echo "  systemctl --user status cpolar"
echo "  systemctl --user restart cpolar"
echo "  journalctl --user -u cpolar -f"
