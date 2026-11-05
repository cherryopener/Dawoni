import { useEffect, useState } from "react";

// 해시 주소(#/exams/…) — 웹뷰·서버 어디서든 새로고침해도 같은 화면 (다워니 router.ts에서 가져옴).
function read(): string[] {
  return window.location.hash
    .replace(/^#\/?/, "")
    .split("?")[0]!
    .split("/")
    .filter(Boolean)
    .map(decodeURIComponent);
}

export function useRoute(): string[] {
  const [parts, setParts] = useState(read);
  useEffect(() => {
    const f = () => setParts(read());
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return parts;
}

export function go(...parts: string[]): void {
  window.location.hash = "/" + parts.map(encodeURIComponent).join("/");
}

export function href(...parts: string[]): string {
  return "#/" + parts.map(encodeURIComponent).join("/");
}
