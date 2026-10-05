import { defineConfig, loadEnv } from "vite";
import path from "node:path";
import { fileURLToPath, URL } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL(".", import.meta.url)), "..");

export default defineConfig(({ mode }) => {
  const rootEnv = loadEnv(mode, repoRoot, "");
  const localEnv = loadEnv(mode, process.cwd(), "");
  const env = { ...rootEnv, ...localEnv };
  const frontendPort = Number(env.FRONTEND_PORT || 19080);
  const apiPort = Number(env.API_PORT || 18765);
  const apiTarget = env.VITE_API_BASE || `http://127.0.0.1:${apiPort}`;
  const allowedHosts = (env.VITE_PREVIEW_ALLOWED_HOSTS || ".vip.cpolar.cn")
    .split(",")
    .map((host) => host.trim())
    .filter(Boolean);

  const proxy = {
    "/api": {
      target: apiTarget,
      changeOrigin: true,
    },
  };

  return {
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./源码", import.meta.url)),
      },
    },
    server: {
      host: true,
      port: frontendPort,
      strictPort: true,
      allowedHosts,
      proxy,
    },
    preview: {
      host: true,
      port: frontendPort,
      strictPort: true,
      allowedHosts,
      proxy,
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
    },
  };
});
