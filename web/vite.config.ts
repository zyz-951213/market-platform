/**
 * 统一站点壳的构建配置(仅壳;三域子应用由 `构建.mjs` 分别构建)。
 *
 * 开发模式:壳内嵌各域自己的 dev server(见 源码/域登记.ts 的 开发地址),
 *          故这里只需给壳自身配一个端口。
 * 生产模式:壳与三个子应用都在同一个网关下(/、/数据底座/、/行为分析/、/指数预测/),
 *          无需代理——网关直接转发 /<域>/api → 各域 FastAPI 应用。
 */
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

const allowedHosts = [
  ".vip.cpolar.cn",
  "your-tunnel-1.vip.cpolar.cn",
  "your-tunnel-2.vip.cpolar.cn",
  "your-tunnel-3.vip.cpolar.cn",
];

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./源码", import.meta.url)),
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    host: true,
    port: 18000,
    strictPort: true,
    allowedHosts,
  },
  preview: {
    host: true,
    port: 18000,
    strictPort: true,
    allowedHosts,
  },
});
