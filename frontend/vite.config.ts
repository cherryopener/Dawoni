import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// 빌드 결과는 파이썬 패키지 안(dawoni/web)으로 → `dawoni ui`가 그대로 서빙한다.
// 개발 중에는 서버를 켜 두고 `npm run dev` (API는 프록시). 시안은 서버 없이 `#/?mock` 자료로 그린다.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
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
