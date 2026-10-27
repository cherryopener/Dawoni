import { useEffect, useState } from "react";

// 해시 주소(#/exams/…) — 웹뷰·서버 어디서든 새로고침해도 같은 화면.
export function useRoute(): string[] {
  const read = () =>
    window.location.hash
      .replace(/^#\/?/, "")
      .split("/")
      .filter(Boolean)
      .map(decodeURIComponent);
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
