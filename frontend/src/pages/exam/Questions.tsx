import { useState } from "react";
import { ItemView } from "../../components/Item";
import { Badge, Button, Card, ErrorBox, Info, JobProgress, Notice, Row, Stack, TextArea } from "../../components/ui";
import { api, errorText, examFileUrl, useJob } from "../../lib/api";
import type { ExamQuestion, Figure } from "../../lib/types";
import type { TabProps } from "../Exam";

const TYPE: Record<string, string> = { multiple_choice: "선택형", short_answer: "단답형", essay: "서술형" };

type ExtractResult = { verdict: "spec" | "raster"; reason: string; matched: boolean; rounds: number; differences: string[] };

/** 원본 크롭 ↔ 스펙으로 다시 그린 그림을 나란히 보고, 어느 쪽을 출력에 쓸지 정한다 (PLAN §8-4). */
function FigurePanel({ examId, q, f, onChanged }: { examId: string; q: ExamQuestion; f: Figure; onChanged: () => void }) {
  const base = `/exams/${examId}/questions/${encodeURIComponent(q.number)}/figures/${encodeURIComponent(f.id)}`;
  const [result, setResult] = useState<ExtractResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [json, setJson] = useState("");
  const [nonce, setNonce] = useState(0);
  const job = useJob((j) => {
    setResult(j.result as ExtractResult);
    setNonce((n) => n + 1);
    onChanged();
  });
  const choiceFigure = q.item.choices_figure_id === f.id || q.item.choices.some((c) => c.figure_id === f.id);
  const put = (body: unknown) =>
    api
      .put(base, body)
      .then(() => {
        setNonce((n) => n + 1);
        setEditing(false);
        onChanged();
      })
      .catch((e: unknown) => setErr(errorText(e)));
  const saveJson = () => {
    try {
      void put({ spec: JSON.parse(json) });
    } catch {
      setErr("JSON 형식이 아닙니다.");
    }
  };
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 10 }}>
      <Stack gap={8}>
        <Row>
          <b>그림 {f.id}</b>
          {f.mode === "parametric" ? <Badge tone="accent">다시 그린 그림 사용</Badge> : <Badge>원본 사용</Badge>}
          {f.caption && <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>{f.caption}</span>}
          <span style={{ flex: 1 }} />
          {!choiceFigure && (
            <Button small onClick={() => void job.run(() => api.post(`${base}/extract`))} disabled={job.running || !f.orig}>
              {f.spec ? "AI로 다시 그리기" : "AI로 파라미터화"}
            </Button>
          )}
          {f.spec && f.mode === "raster" && (
            <Button small variant="primary" onClick={() => void put({ mode: "parametric" })}>
              다시 그린 그림 쓰기
            </Button>
          )}
          {f.mode === "parametric" && f.orig && (
            <Button small onClick={() => void put({ mode: "raster" })}>
              원본 쓰기
            </Button>
          )}
          {f.spec && (
            <Button small variant="ghost" onClick={() => { setJson(JSON.stringify(f.spec, null, 2)); setEditing(!editing); }}>
              스펙
            </Button>
          )}
        </Row>
        {choiceFigure && <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>선지 그림은 원본 그대로 씁니다 (동형문제에서도 고정).</span>}
        <JobProgress job={job.job} />
        <ErrorBox error={job.error ?? err} />
        {result && result.verdict === "raster" && <Notice>다시 그리기 어렵다는 판단: {result.reason} — 원본을 그대로 씁니다.</Notice>}
        {result && result.verdict === "spec" && (
          result.matched ? (
            <Info>원본과 비교해 차이 없음 ({result.rounds}회 비교). 나란히 보고 쓸지 정하세요.</Info>
          ) : (
            <Notice>
              {result.rounds}회 고친 뒤에도 남은 차이: {result.differences.join(" / ") || "없음"}
            </Notice>
          )
        )}
        <div style={{ display: "grid", gridTemplateColumns: f.spec ? "1fr 1fr" : "1fr", gap: 10, alignItems: "start" }}>
          {f.orig && (
            <Stack gap={4}>
              <span style={{ fontSize: 12, color: "var(--text-3)" }}>원본</span>
              <img src={examFileUrl(examId, f.orig)} alt="원본" style={{ maxWidth: "100%", maxHeight: 280, objectFit: "contain", border: "1px solid var(--line)" }} />
            </Stack>
          )}
          {f.spec && (
            <Stack gap={4}>
              <span style={{ fontSize: 12, color: "var(--text-3)" }}>스펙으로 다시 그림 (문항 변수 값 반영)</span>
              <img src={`/api${base}/render?n=${nonce}`} alt="재현" style={{ maxWidth: "100%", maxHeight: 280, objectFit: "contain", border: "1px solid var(--line)" }} />
            </Stack>
          )}
        </div>
        {editing && (
          <Stack gap={6}>
            <TextArea value={json} onChange={setJson} rows={12} mono />
            <Row>
              <Button small variant="primary" onClick={saveJson}>
                스펙 저장
              </Button>
              <span style={{ fontSize: 12, color: "var(--text-3)" }}>식에는 숫자나 문항 변수 이름을 씁니다 (예: "theta", "2*m").</span>
            </Row>
          </Stack>
        )}
      </Stack>
    </div>
  );
}

export function QuestionsTab({ detail, reload }: TabProps) {
  return (
    <Stack>
      {detail.exam.questions.map((q) => (
        <Card
          key={q.number}
          title={
            <Row>
              문항 {q.number}
              <Badge>{TYPE[q.item.item_type]}</Badge>
              <Badge>{q.points}점</Badge>
              {q.item.figures.length > 0 && <Badge>그림 {q.item.figures.length}</Badge>}
            </Row>
          }
        >
          <Stack>
            <ItemView
              number={q.number}
              points={q.points}
              item={q.item}
              examId={detail.exam.id}
              renderBase={`/api/exams/${detail.exam.id}/questions/${encodeURIComponent(q.number)}/figures`}
            />
            {q.item.figures
              .filter((f) => !(q.item.choices_figure_id === f.id || q.item.choices.some((c) => c.figure_id === f.id)))
              .map((f) => (
                <FigurePanel key={f.id} examId={detail.exam.id} q={q} f={f} onChanged={reload} />
              ))}
          </Stack>
        </Card>
      ))}
    </Stack>
  );
}
