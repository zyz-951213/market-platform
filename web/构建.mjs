/**
 * 统一站点构建编排 —— 一次构建产出「站点壳 + 三域子应用」到 web/dist。
 *
 * 归属:
 *   web/                → 站点壳(顶栏导航 + 三域容器)      → dist/index.html
 *   web/模块/数据底座    → 数据底座子应用(原 database 前端)  → dist/数据底座/
 *   web/模块/行为分析    → 行为分析子应用(原 market-agents)  → dist/行为分析/
 *   web/模块/指数预测    → 指数预测子应用(原 market-predictor) → dist/指数预测/
 *
 * 关键点:三个客户端本来就读取 `import.meta.env.VITE_API_BASE`,
 * 因此「一个站点 + 三个接口前缀」不需要改动任何业务页面代码。
 *
 * 用法: node 构建.mjs              # 站点壳 + 三域
 *       node 构建.mjs 行为分析      # 只构建指定域
 *       node 构建.mjs --壳          # 只构建站点壳
 */

import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const 本站 = dirname(fileURLToPath(import.meta.url));
const 产物 = resolve(本站, "dist");
const 模块根 = resolve(本站, "模块");

/** 域定义:目录名 = 站点路径前缀 = 接口前缀 */
const 域列表 = [
  { 名: "数据底座", 说明: "公共/行情增量层:表结构、只读查询、水位与更新调度" },
  { 名: "行为分析", 说明: "NMF 行为分解 → 市场状态 → 仓位/回测/模拟盘" },
  { 名: "指数预测", 说明: "Transformer 概率 + LLM 研判 → 六袖统一夏普" },
];

const 参数 = process.argv.slice(2);
const 只壳 = 参数.includes("--壳");
const 指定域 = 参数.filter((a) => !a.startsWith("--"));

/** 找一个可用的 vite 入口(不依赖 npm/npx,避免联网安装) */
function 找Vite(起点) {
  const 候选 = [
    resolve(起点, "node_modules/vite/bin/vite.js"),
    ...readdirSync(模块根, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => resolve(模块根, d.name, "node_modules/vite/bin/vite.js")),
  ];
  for (const c of 候选) if (existsSync(c)) return c;
  throw new Error("找不到 vite:请先在 web/模块/<域>/ 内安装依赖");
}

function 运行(参数, 工作目录, 环境 = {}) {
  const r = spawnSync(process.execPath, 参数, {
    cwd: 工作目录,
    stdio: "inherit",
    env: { ...process.env, ...环境 },
  });
  if (r.status !== 0) {
    console.error(`\n✗ 构建失败(cwd=${工作目录}): node ${参数.join(" ")}`);
    process.exit(r.status ?? 1);
  }
}

function 构建壳() {
  const vite = 找Vite(本站);
  console.log("\n=== 构建站点壳 → dist/ ===");
  运行([vite, "build"], 本站, { NODE_ENV: "production" });
}

function 构建域(域) {
  const 目录 = resolve(模块根, 域.名);
  if (!existsSync(目录)) {
    console.error(`✗ 模块目录不存在: ${目录}`);
    process.exit(1);
  }
  const vite = 找Vite(目录);
  console.log(`\n=== 构建子应用:${域.名} → dist/${域.名}/ ===`);
  运行(
    [
      vite,
      "build",
      `--base=/${域.名}/`,
      `--outDir=${resolve(产物, 域.名)}`,
      "--emptyOutDir",
    ],
    目录,
    { VITE_API_BASE: `/${域.名}`, NODE_ENV: "production" },
  );
}

if (existsSync(产物) && 指定域.length === 0 && !只壳) {
  rmSync(产物, { recursive: true, force: true });
}

if (!指定域.length || 只壳) 构建壳();
for (const 域 of 域列表) {
  if (只壳) continue;
  if (指定域.length && !指定域.includes(域.名)) continue;
  构建域(域);
}

console.log("\n✓ 统一站点构建完成 →", 产物);
console.log("  站点入口: /");
for (const 域 of 域列表) console.log(`  子应用:   /${域.名}/   接口前缀 /${域.名}/api`);
