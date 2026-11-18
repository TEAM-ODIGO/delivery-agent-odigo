import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 개발 서버가 /orchestrator 요청을 백엔드로 넘긴다 → 브라우저에서 CORS 설정이 필요 없다.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.VITE_API_TARGET || "http://localhost:8000";
  return {
    plugins: [react()],
    server: { port: 5173, proxy: { "/orchestrator": { target, changeOrigin: true } } },
    preview: { port: 4173, proxy: { "/orchestrator": { target, changeOrigin: true } } },
  };
});
