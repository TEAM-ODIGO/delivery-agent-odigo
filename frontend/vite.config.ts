import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 개발 서버가 API 요청을 백엔드로 넘긴다 → 브라우저에서 CORS 설정이 필요 없다.
// /owner 는 화면 주소라서 점주 API(/owner/restaurants)만 넘긴다 (이슈 #50).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.VITE_API_TARGET || "http://localhost:8000";
  return {
    plugins: [react()],
    server: { port: 5173, proxy: proxy(target) },
    preview: { port: 4173, proxy: proxy(target) },
  };
});

function proxy(target: string) {
  return {
    "/orchestrator": { target, changeOrigin: true },
    "/owner/restaurants": { target, changeOrigin: true },
  };
}
