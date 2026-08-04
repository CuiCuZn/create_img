import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    // dev 模式下把 /api 请求代理到后端,避免 CORS
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  // 构建产物输出到 dist,后端生产模式会托管此目录
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
