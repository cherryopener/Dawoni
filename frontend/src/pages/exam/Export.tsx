import { useState } from "react";
import { FileActions } from "../../components/FileActions";
import {
  Badge,
  Button,
  Card,
  Check,
  ErrorBox,
  Field,
  Notice,
  NumberInput,
  Row,
  Stack,
  TextInput,
} from "../../components/ui";
import { api, errorText } from "../../lib/api";
import type { ExportResult } from "../../lib/types";
import type { TabProps } from "../Exam";

type Kind = "paper" | "key" | "report" | "scores";

function ExportCard({ examId, kind, title, desc, options }: {
  examId: string;
  kind: Kind;
  title: string;
  desc: string;
  options: Record<string, unknown>;
  children?: never;
}) {
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ExportResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const make = () => {
    setBusy(true);
    setErr(null);
    api
      .post<ExportResult>(`/exams/${examId}/export`, { kind, ...options })
      .then(setRes)
      .catch((e: unknown) => setErr(errorText(e)))
      .finally(() => setBusy(false));
  };
  return (
    <Stack gap={8}>
      <Row>
        <b>{title}</b>
        <span style={{ color: "var(--text-2)", fontSize: 13 }}>{desc}</span>
        <span style={{ flex: 1 }} />
        <Button variant="primary" small onClick={make} disabled={busy}>
          {busy ? "만드는 중…" : "Word 파일 만들기"}
        </Button>
      </Row>
      <ErrorBox error={err} />
      {res && (
        <Stack gap={6}>
          <Row>
            {res.ok ? <Badge tone="ok">자동 검사 통과</Badge> : <Badge tone="bad">자동 검사 실패</Badge>}
            <span style={{ fontSize: 13, color: "var(--text-2)" }}>
              {res.name} · {res.summary}
            </span>
          </Row>
          {res.errors.map((e, i) => (
            <ErrorBox key={i} error={e} />
          ))}
          {res.warnings.map((w, i) => (
            <Notice key={i}>{w}</Notice>
          ))}
          {res.math_fallbacks > 0 && <Notice>Word 수식으로 바꿀 수 없는 학생 답안 식 {res.math_fallbacks}개는 원문으로 표시했습니다.</Notice>}
          <FileActions path={res.path} />
        </Stack>
      )}
    </Stack>
  );
}

export function ExportTab({ detail }: TabProps) {
  const id = detail.exam.id;
  const [columns, setColumns] = useState<1 | 2>(2);
  const [points, setPoints] = useState(true);
  const [lines, setLines] = useState(8);
  const [subtitle, setSubtitle] = useState("");
  const [withQ, setWithQ] = useState(false);
  const [withRubric, setWithRubric] = useState(false);
  return (
    <Stack>
      <Card title="시험지">
        <Stack>
          <Row>
            <Check checked={columns === 2} onChange={(v) => setColumns(v ? 2 : 1)}>
              2단
            </Check>
            <Check checked={points} onChange={setPoints}>
              배점 표시
            </Check>
            <span>서술형 풀이 칸</span>
            <NumberInput value={lines} min={0} max={40} step={1} onChange={(v) => setLines(Math.round(v || 0))} />
            <span>줄</span>
            <div style={{ flex: 1, minWidth: 220 }}>
              <Field label="제목 아래 줄 (선택)">
                <TextInput value={subtitle} onChange={setSubtitle} placeholder="고등학교 1학년 · 대수" />
              </Field>
            </div>
          </Row>
          <ExportCard
            examId={id}
            kind="paper"
            title=""
            desc="문항·그림·선지 배치·수식(Word 수식)"
            options={{ columns, show_points: points, answer_lines: lines, subtitle: subtitle || null }}
          />
        </Stack>
      </Card>
      <Card title="정답 및 해설">
        <Stack>
          <Row>
            <Check checked={withQ} onChange={setWithQ}>
              문항 함께
            </Check>
            <Check checked={withRubric} onChange={setWithRubric}>
              채점 기준 함께 (강사용)
            </Check>
          </Row>
          <ExportCard examId={id} kind="key" title="" desc="정답표 · 범위 해설 · 다른 풀이" options={{ with_questions: withQ, with_rubric: withRubric }} />
        </Stack>
      </Card>
      <Card title="채점 결과">
        <Stack gap={14}>
          <ExportCard examId={id} kind="report" title="채점 리포트" desc="강사용 — 판독문·근거 인용·검토 표시" options={{}} />
          <ExportCard examId={id} kind="scores" title="성적표" desc="학생용 — 점수·첨삭·감점 내용 (학생마다 한 쪽)" options={{}} />
        </Stack>
      </Card>
    </Stack>
  );
}
