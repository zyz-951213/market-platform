import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

const allowedHosts = [".vip.cpolar.cn", "your-tunnel-3.vip.cpolar.cn", "your-tunnel-2.vip.cpolar.cn", "your-tunnel-1.vip.cpolar.cn"];

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./源码", import.meta.url)),
    },
  },
  server: {
    host: true,
    port: 8080,
    allowedHosts,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8766",
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: true,
    port: 8080,
    allowedHosts,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8766",
        changeOrigin: true,
      },
    },
  },
});
