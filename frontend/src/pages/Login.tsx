import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ErrorNote } from "@/components/ui/misc";
import { api, errorText } from "@/lib/api";
import type { AuthStatus } from "@/lib/types";

/** 배포 앱: 허가된 Google 계정으로 처음 한 번 로그인 (PLAN §11-3). 로그인은 기본 브라우저에서. */
export function LoginPage({ auth }: { auth: AuthStatus }) {
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const login = () => {
    setErr(null);
    api
      .post<{ url: string | null }>("/auth/login")
      .then((r) => setUrl(r.url))
      .catch((e: unknown) => setErr(errorText(e)));
  };
  return (
    <div className="grid min-h-full place-items-center px-6">
      <Card className="w-full max-w-sm p-8 text-center">
        <div className="mx-auto grid size-12 place-items-center rounded-xl bg-accent text-[18px] text-accent-fg">다</div>
        <h1 className="m-0 mt-4 text-[18px] font-medium">다워이</h1>
        {auth.state === "checking" ? (
          <p className="m-0 mt-2 text-text-2">로그인 확인 중…</p>
        ) : (
          <>
            <p className="m-0 mt-2 text-text-2">학원에서 허가한 Google 계정으로 로그인하세요. 기본 브라우저에서 로그인 창이 열립니다.</p>
            {auth.message && <p className={"m-0 mt-2 text-[13px] " + (auth.state === "blocked" ? "text-bad" : "text-text-2")}>{auth.message}</p>}
            <Button variant="primary" size="lg" className="mt-6 w-full" onClick={login}>
              Google로 로그인
            </Button>
            {url && (
              <p className="m-0 mt-3 break-all text-[12px] text-text-3">
                창이 안 열리면 이 주소를 브라우저에 붙여 넣으세요: {url}
              </p>
            )}
            {err && (
              <div className="mt-3 text-left">
                <ErrorNote message={err} />
              </div>
            )}
            <p className="m-0 mt-3 text-[12px] text-text-3">한 번 로그인하면 인터넷 없이 7일 동안 쓸 수 있습니다.</p>
          </>
        )}
      </Card>
    </div>
  );
}
