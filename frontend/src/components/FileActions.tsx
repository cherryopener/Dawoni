import { useState } from "react";
import { api, errorText } from "../lib/api";
import { Button, ErrorBox, Info, Row } from "./ui";

/** 웹뷰는 다운로드를 처리하지 않으므로(AIHelper) 파일은 서버가 열고·보여 주고·복사한다. */
export function FileActions({ path }: { path: string }) {
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const call = (what: "open" | "reveal" | "save-as") => {
    setErr(null);
    setMsg(null);
    api
      .post<{ saved_to?: string | null }>(`/files/${what}`, { path })
      .then((r) => {
        if (what === "save-as") setMsg(r.saved_to ? `저장했습니다: ${r.saved_to}` : null);
      })
      .catch((e: unknown) => setErr(errorText(e)));
  };
  return (
    <>
      <Row>
        <Button small variant="primary" onClick={() => call("open")}>
          Word로 열기
        </Button>
        <Button small onClick={() => call("reveal")}>
          폴더에서 보기
        </Button>
        <Button small onClick={() => call("save-as")}>
          다른 이름으로 저장…
        </Button>
      </Row>
      <ErrorBox error={err} />
      {msg && <Info>{msg}</Info>}
    </>
  );
}
