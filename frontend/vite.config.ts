import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 빌드 결과는 파이썬 패키지 안(dawoni/web)으로 → `dawoni ui`가 그대로 서빙한다.
// 개발 중에는 `dawoni ui --no-window`로 서버를 켜 두고 `npm run dev` (API는 프록시).
export default defineConfig({
  plugins: [react()],
  // 로컬 앱이라 한 덩어리(약 600KB)여도 괜찮다
  build: { outDir: "../dawoni/web", emptyOutDir: true, chunkSizeWarningLimit: 1500 },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/login": "http://127.0.0.1:8765",
    },
  },
  test: { environment: "node" },
});
