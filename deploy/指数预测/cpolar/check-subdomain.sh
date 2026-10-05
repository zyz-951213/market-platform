#!/usr/bin/env bash
# 检查 cpolar 二级子域名 DNS 是否与隧道注册节点一致
set -euo pipefail

check_one() {
  local name="$1"
  local cname
  cname="$(dig +short "${name}.vip.cpolar.cn" CNAME | sed 's/\.$//')"
  if [[ -z "$cname" ]]; then
    echo "❌ ${name}: 无 CNAME（可能未预留或 DNS 未生效）"
    return 1
  fi
  if [[ "$cname" == "cpolard.vip.cpolar.cn" ]]; then
    echo "✅ ${name} → ${cname}（与 your-tunnel-1/your-tunnel-3 同集群，可配合 region: cn_vip）"
  elif [[ "$cname" == "cpolard-b.vip.cpolar.cn" ]]; then
    echo "⚠️  ${name} → ${cname}（B 集群；若隧道注册在 cpolard 主节点会 404，需重新预留）"
    return 2
  else
    echo "ℹ️  ${name} → ${cname}"
  fi
}

check_http() {
  local name="$1"
  local vip_code cn_code
  vip_code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "https://${name}.vip.cpolar.cn/" || echo "000")"
  cn_code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "https://${name}.cpolar.cn/" || echo "000")"
  if [[ "$vip_code" == "200" ]]; then
    echo "✅ https://${name}.vip.cpolar.cn → ${vip_code}"
  else
    echo "❌ https://${name}.vip.cpolar.cn → ${vip_code}"
  fi
  if [[ "$cn_code" != "404" && "$cn_code" != "000" ]]; then
    echo "ℹ️  https://${name}.cpolar.cn → ${cn_code}"
  else
    echo "⚠️  https://${name}.cpolar.cn → ${cn_code}（China VIP 请用 *.vip.cpolar.cn）"
  fi
}

echo "cpolar 二级域名 DNS 检查（*.vip.cpolar.cn）"
echo ""
subs=("$@")
if [[ ${#subs[@]} -eq 0 ]]; then
  subs=(your-tunnel-1 your-tunnel-2 your-tunnel-3)
fi
for sub in "${subs[@]}"; do
  check_one "$sub" || true
  check_http "$sub"
  echo ""
done
echo "正确公网地址（China VIP 预留）："
for sub in "${subs[@]}"; do
  echo "  https://${sub}.vip.cpolar.cn"
done
echo ""
echo "修复 your-tunnel-2 指向 cpolard-b 的步骤："
echo "  1. cpolar 后台 → 预留 → 删除 your-tunnel-2"
echo "  2. 重新预留 your-tunnel-2，地区选 China VIP（与 your-tunnel-1 相同）"
echo "  3. 运行本脚本确认 CNAME 变为 cpolard.vip.cpolar.cn"
echo "  4. systemctl --user restart cpolar"
