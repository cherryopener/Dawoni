import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// 빌드 결과는 파이썬 패키지 안(dawoni/web)으로 → `dawoni ui`가 그대로 서빙한다.
// 개발 중에는 서버를 켜 두고 `pnpm dev` (API는 프록시). 시안은 서버 없이 `#/?mock` 자료로 그린다.
// 화면 구석에 작게 보이는 빌드 표시 — "v.0.1.4(26.11.19)": pyproject 버전(태그 빌드는 태그와 같음을
// build.yml이 확인한다) + 이 빌드의 커밋 날짜. git이 없으면 날짜만 빠진다.
function buildLabel(): string {
  const pyproject = readFileSync(new URL("../pyproject.toml", import.meta.url), "utf8");
  const version = /^version = "(.+)"/m.exec(pyproject)?.[1] ?? "?";
  let date = "";
  try {
    date = execFileSync("git", ["log", "-1", "--format=%cd", "--date=format:%y.%m.%d"], { encoding: "utf8" }).trim();
  } catch {
    /* git 없음 */
  }
  return date ? `v.${version}(${date})` : `v.${version}`;
}

export default defineConfig({
  define: { __BUILD_LABEL__: JSON.stringify(buildLabel()) },
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
