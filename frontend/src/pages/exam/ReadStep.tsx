import { AlertTriangle, ArrowRight, Check, Pencil, Plus, ScanSearch, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { RichText } from "@/components/Math";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Help } from "@/components/ui/help";
import { Advanced, ErrorNote, Input } from "@/components/ui/misc";
import { Segmented } from "@/components/ui/segmented";
import { api, enc, useAction, waitJob } from "@/lib/api";
import { go } from "@/lib/router";
import { useTeacher } from "@/lib/session";
import { pts } from "@/lib/steps";
import type { ExamQuestion, Job, KeyPart, QuestionDetail } from "@/lib/types";
import { useExam } from "../Exam";
import { RegionDialog, type RegionWhat } from "./RegionDialog";
import { useQuestionDetails } from "./shared";

const KIND: Record<string, string> = { essay: "서술형", short_answer: "단답형" };
type PartKind = "essay" | "short_answer";
const KIND_OPTIONS: { value: PartKind | ""; label: string }[] = [
  { value: "short_answer", label: "단답형" },
  { value: "essay", label: "서술형" },
];

const textarea =
  "w-full rounded-lg border border-line bg-card px-3 py-2 text-text outline-none placeholder:text-text-3 focus:border-accent";

/** 본문 미리 보기: 줄마다 (식은 KaTeX, 그림 자리는 흐리게). */
function StemPreview({ text }: { text: string }) {
  return (
    <div className="flex flex-col gap-1 leading-relaxed">
      {text.split("\n").map((line, i) =>
        /^\[그림 F\d+\]$/.test(line.trim()) ? (
          <span key={i} className="text-text-3">
            {line}
          </span>
        ) : (
          <RichText key={i} text={line} />
        ),
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 고치기

type Row = {
  id: number;
  /** 고치기 전 표시 — 해설이 따라온다. 새 소문항이면 null, 소문항이 없던 문항의 해설이면 "" */
  was: string | null;
  label: string;
  points: string;
  kind: PartKind | null;
  answer: string;
  solution: string;
  credit: string;
};

type KeyFields = Pick<Row, "answer" | "solution" | "credit">;

function keyFields(kp: KeyPart | undefined): KeyFields {
  return { answer: kp?.answer ?? "", solution: kp?.solution ?? "", credit: kp?.credit_text ?? "" };
}

function sameKey(a: KeyFields, b: KeyFields): boolean {
  return a.answer.trim() === b.answer.trim() && a.solution.trim() === b.solution.trim() && a.credit.trim() === b.credit.trim();
}

let nextId = 1;

/** 소문항(더하기·빼기·표시·유형·배점), 문항 유형, 본문, 해설을 한 번에 고친다.
 * `reset`: 루브릭이 있는 문항 — 강사가 확인했으니 해설 말고 다른 것을 고치면 루브릭을 비켜 둔다. */
function QuestionEditor({
  q,
  d,
  reset,
  onDone,
  onCancel,
}: {
  q: ExamQuestion;
  d: QuestionDetail;
  reset: string;
  onDone: (notes: string[]) => void;
  onCancel: () => void;
}) {
  const { base } = useExam();
  const by = useTeacher();
  const act = useAction();
  const key = d.key;
  const kp = (label: string) => key?.parts.find((p) => p.label === label);
  const [itemType, setItemType] = useState(q.item.item_type);
  const [points, setPoints] = useState(String(q.points));
  const [whole, setWhole] = useState<KeyFields>(keyFields(kp("")));
  const [stem, setStem] = useState(d.stem_missing ? "" : d.stem_text);
  const [rows, setRows] = useState<Row[]>(() =>
    q.parts.map((p) => ({ id: nextId++, was: p.label, label: p.label, points: String(p.points), kind: p.kind, ...keyFields(kp(p.label)) })),
  );
  const [confirmDelete, setConfirmDelete] = useState(false);
  const editableType = itemType === "essay" || itemType === "short_answer";
  const total = rows.reduce((a, r) => a + (Number(r.points) || 0), 0);

  const setRow = (id: number, patch: Partial<Row>) => setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  const addRow = () =>
    setRows((rs) => {
      const first = rs.length === 0 && q.parts.length === 0; // 소문항 없던 문항의 해설은 첫 소문항으로
      const kind: PartKind | null = itemType === "essay" ? "essay" : itemType === "short_answer" ? "short_answer" : null;
      return [
        ...rs,
        { id: nextId++, was: first ? "" : null, label: `(${rs.length + 1})`, points: "", kind, ...(first ? whole : { answer: "", solution: "", credit: "" }) },
      ];
    });

  const structureChanged =
    itemType !== q.item.item_type ||
    rows.length !== q.parts.length ||
    rows.some((r, i) => r.label.trim() !== q.parts[i]?.label || Number(r.points) !== q.parts[i]?.points || r.kind !== q.parts[i]?.kind) ||
    (rows.length === 0 && Number(points) !== q.points);
  const keyChanged = rows.length
    ? rows.some((r) => !sameKey(r, keyFields(r.was === null ? undefined : kp(r.was))))
    : !sameKey(whole, keyFields(kp("")));
  const stemChanged = stem.trim() !== "" && stem !== (d.stem_missing ? "" : d.stem_text);

  const save = () =>
    act.run(async () => {
      const notes: string[] = [];
      const path = `${base}/questions/${enc(q.number)}`;
      if (structureChanged) {
        const body = {
          item_type: editableType ? itemType : null,
          points: rows.length ? null : Number(points),
          parts: rows.map((r) => ({ label: r.label.trim(), points: Number(r.points), kind: r.kind, was: r.was })),
        };
        const res = await api.put<{ notes: string[] }>(`${path}/structure${reset}`, body);
        notes.push(...res.notes);
      }
      if (stemChanged) {
        const res = await api.put<{ notes: string[] }>(`${path}/stem${reset}`, { text: stem });
        notes.push(...res.notes.filter((n) => !notes.includes(n)));
      }
      if (keyChanged) {
        const parts = rows.length
          ? rows.map((r) => ({ label: r.label.trim(), answer: r.answer, solution: r.solution, credit_text: r.credit }))
          : [{ label: "", answer: whole.answer, solution: whole.solution, credit_text: whole.credit }];
        await api.put(`${path}/key-text`, { parts, by });
      }
      onDone(notes);
    });
  const remove = () =>
    act.run(async () => {
      await api.del(`${base}/questions/${enc(q.number)}${reset}`);
      setConfirmDelete(false);
      onDone([]);
    });

  const keyInputs = (f: KeyFields, set: (patch: Partial<KeyFields>) => void) => (
    <div className="grid grid-cols-[4.5rem_1fr] items-start gap-x-2 gap-y-1.5">
      <span className="pt-2 text-[13px] text-text-3">답</span>
      <Input value={f.answer} onChange={(e) => set({ answer: e.target.value })} placeholder="최종 답 (식은 \( \)로)" />
      <span className="pt-2 text-[13px] text-text-3">해설</span>
      <textarea rows={2} value={f.solution} onChange={(e) => set({ solution: e.target.value })} placeholder="해설지의 풀이를 붙여 넣기" className={textarea} />
      <span className="pt-2 text-[13px] text-text-3">부분 점수</span>
      <Input value={f.credit} onChange={(e) => set({ credit: e.target.value })} placeholder="예: [부분 점수: 과정 2점 / 답 2점]" />
    </div>
  );

  return (
    <div className="flex flex-col gap-4 px-5 py-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-[13px] text-text-2">유형</span>
        {editableType ? (
          <Segmented
            value={itemType as PartKind}
            onChange={setItemType}
            options={[
              { value: "short_answer", label: "단답형" },
              { value: "essay", label: "서술형" },
            ]}
          />
        ) : (
          <span>객관식</span>
        )}
        {rows.length === 0 && (
          <>
            <span className="ml-4 text-[13px] text-text-2">배점</span>
            <Input type="number" min={0} step={0.5} value={points} onChange={(e) => setPoints(e.target.value)} className="h-8 w-20 text-right" />
            <span className="text-text-3">점</span>
          </>
        )}
        {rows.length > 0 && <span className="ml-4 text-text-2">합 {pts(total)}점</span>}
      </div>

      {rows.length === 0 && (
        <div className="rounded-lg border border-line p-3">
          <div className="mb-2 text-[13px] font-medium">문항 전체 해설</div>
          {keyInputs(whole, (p) => setWhole({ ...whole, ...p }))}
        </div>
      )}
      {rows.map((r) => (
        <div key={r.id} className="rounded-lg border border-line p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Input value={r.label} onChange={(e) => setRow(r.id, { label: e.target.value })} aria-label="소문항 표시" className="h-8 w-24" />
            <Segmented value={r.kind ?? ""} onChange={(v) => setRow(r.id, { kind: v || null })} options={KIND_OPTIONS} />
            <Input
              type="number"
              min={0}
              step={0.5}
              value={r.points}
              onChange={(e) => setRow(r.id, { points: e.target.value })}
              aria-label={`${r.label} 배점`}
              className="h-8 w-20 text-right"
            />
            <span className="text-text-3">점</span>
            <div className="flex-1" />
            <Button size="sm" variant="ghost" aria-label={`${r.label} 빼기`} onClick={() => setRows((rs) => rs.filter((x) => x.id !== r.id))}>
              <Trash2 />
            </Button>
          </div>
          {keyInputs(r, (p) => setRow(r.id, p))}
        </div>
      ))}
      <div>
        <Button size="sm" variant="ghost" onClick={addRow}>
          <Plus /> 소문항
        </Button>
      </div>

      <div>
        <div className="mb-1.5 flex items-center gap-2 text-[13px] text-text-2">
          본문
          <Help>식은 \( \)로 감싸고, 그림 자리는 [그림 F1]처럼 둡니다. 식이 많으면 '원본에서 읽기'가 편합니다.</Help>
        </div>
        <textarea rows={6} value={stem} onChange={(e) => setStem(e.target.value)} placeholder="문항 본문을 붙여 넣기" className={textarea} />
      </div>

      {act.error && <ErrorNote message={act.error} />}
      <div className="flex items-center gap-2">
        <Button variant="danger" size="sm" disabled={act.busy} onClick={() => setConfirmDelete(true)}>
          <Trash2 /> 문항 지우기
        </Button>
        <div className="flex-1" />
        <Button variant="ghost" disabled={act.busy} onClick={onCancel}>
          취소
        </Button>
        <Button disabled={act.busy || !(structureChanged || keyChanged || stemChanged)} onClick={() => void save()}>
          <Check /> 저장
        </Button>
      </div>
      <Dialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`${q.number}번을 지울까요?`}
        desc="해설지·그림도 함께 지웁니다. 잘못 생긴 문항일 때만 지우세요."
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              취소
            </Button>
            <Button variant="danger" disabled={act.busy} onClick={() => void remove()}>
              지우기
            </Button>
          </>
        }
      />
    </div>
  );
}

// ---------------------------------------------------------------- 문항 카드

function QuestionCard({
  q,
  d,
  pages,
  locked,
  onSaved,
}: {
  q: ExamQuestion;
  d?: QuestionDetail;
  pages: number[];
  locked: boolean;
  onSaved: () => void;
}) {
  const { base } = useExam();
  const by = useTeacher();
  const [points, setPoints] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState(false);
  const [region, setRegion] = useState<RegionWhat | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  /** 루브릭이 있는 문항을 고치려 할 때 먼저 묻는다: 확인하면 이 카드에서는 고칠 수 있다 */
  const [ask, setAsk] = useState<(() => void) | null>(null);
  const [unlocked, setUnlocked] = useState(false);
  const act = useAction();
  const key = d?.key;
  const open = locked && !unlocked;
  const reset = locked ? "?reset_rubric=true" : "";
  const guard = (then: () => void) => (open ? setAsk(() => then) : then());
  useEffect(() => {
    if (!locked) setUnlocked(false); // 루브릭을 다시 만들면 다시 묻는다
  }, [locked]);
  const rows = q.parts.length ? q.parts : [{ label: "", points: q.points, kind: null }];
  const kindOf = (k: string | null) => (k ? KIND[k] : q.item.item_type === "essay" ? "서술형" : q.item.item_type === "multiple_choice" ? "객관식" : "단답형");
  const save = () =>
    act.run(async () => {
      const value = (label: string, old: number) => Number(points[label] ?? old);
      const body = q.parts.length
        ? { parts: Object.fromEntries(q.parts.map((p) => [p.label, value(p.label, p.points)])) }
        : { points: value("", q.points) };
      await api.put(`${base}/questions/${enc(q.number)}/points${reset}`, body);
      setPoints({});
      onSaved();
    });
  const confirmKey = () =>
    act.run(async () => {
      await api.put(`${base}/questions/${enc(q.number)}/key`, { confirm: true, by });
      onSaved();
    });
  const dirty = Object.keys(points).length > 0;
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3">
        <span className="text-[15px] font-medium">{q.number}번</span>
        <span className="text-text-3">{pts(q.points)}점</span>
        {d?.stem_missing && (
          <Badge tone="warn">
            <AlertTriangle /> 본문 없음
          </Badge>
        )}
        <div className="flex-1" />
        {key?.confirmed && (
          <span className="inline-flex items-center gap-1 text-[12px] text-ok">
            <Check className="size-3.5" /> 해설 확인 {key.confirmed_by ?? ""}
          </span>
        )}
        {!editing && key && !key.confirmed && key.doubtful.length > 0 && (
          <Button size="sm" variant="outline" disabled={act.busy} onClick={() => void confirmKey()}>
            원본과 맞음
          </Button>
        )}
        {!editing && dirty && (
          <Button size="sm" variant="outline" disabled={act.busy} onClick={() => void save()}>
            배점 저장
          </Button>
        )}
        {!editing && (
          <>
            <Button size="sm" variant="outline" onClick={() => guard(() => setRegion(d?.stem_missing || key ? "question" : "key"))}>
              <ScanSearch /> 원본에서 읽기
            </Button>
            <Button size="sm" variant="outline" disabled={!d} onClick={() => guard(() => setEditing(true))}>
              <Pencil /> 고치기
            </Button>
          </>
        )}
      </div>
      {notes.length > 0 && (
        <div className="flex flex-col gap-1 border-b border-line bg-warn-soft px-5 py-2 text-[13px]">
          {notes.map((n) => (
            <span key={n}>{n}</span>
          ))}
        </div>
      )}
      {editing && d ? (
        <QuestionEditor
          q={q}
          d={d}
          reset={reset}
          onCancel={() => setEditing(false)}
          onDone={(n) => {
            setEditing(false);
            setNotes(n);
            onSaved();
          }}
        />
      ) : (
        <>
          <table className="w-full border-collapse text-left">
            <thead className="text-[12px] text-text-3">
              <tr>
                <th className="w-24 px-5 py-2 font-normal">소문항</th>
                <th className="w-20 px-2 py-2 font-normal">유형</th>
                <th className="w-32 px-2 py-2 font-normal">배점</th>
                <th className="px-5 py-2 font-normal">해설지 답</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => {
                const kp = key?.parts.find((x) => x.label === p.label);
                const doubt = key?.doubtful.includes(p.label) && !key.confirmed;
                return (
                  <tr key={p.label} className="border-t border-line align-top">
                    <td className="px-5 py-2.5">{p.label ? `${q.number}-${p.label}` : q.number}</td>
                    <td className="px-2 py-2.5 text-text-2">{kindOf(p.kind)}</td>
                    <td className="px-2 py-2.5">
                      <input
                        type="number"
                        min={0}
                        step={0.5}
                        readOnly={open}
                        title={open ? "루브릭을 만든 문항 — 눌러서 고치기" : undefined}
                        onClick={() => open && guard(() => undefined)}
                        value={points[p.label] ?? String(p.points)}
                        onChange={(e) => setPoints({ ...points, [p.label]: e.target.value })}
                        className="h-8 w-16 rounded-md border border-line bg-card px-2 text-right outline-none focus:border-accent read-only:cursor-pointer read-only:opacity-60"
                      />
                      <span className="ml-1 text-text-3">점</span>
                    </td>
                    <td className="px-5 py-2.5">
                      {!key ? (
                        <span className="text-text-3">해설 없음</span>
                      ) : (
                        <div className="flex flex-col gap-1">
                          <RichText text={kp?.answer ?? (kp?.solution.slice(0, 120) || "—")} />
                          {doubt && (
                            <span>
                              <Badge tone="warn">
                                <AlertTriangle /> 두 번 읽은 숫자가 다름 · 원본과 비교
                              </Badge>
                            </span>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {d && !d.stem_missing && (
            <div className="border-t border-line px-5 py-3">
              <Advanced label="본문 보기">
                <StemPreview text={d.stem_text} />
              </Advanced>
            </div>
          )}
        </>
      )}
      {act.error && (
        <div className="px-5 pb-3">
          <ErrorNote message={act.error} />
        </div>
      )}
      <RegionDialog
        open={region !== null}
        onOpenChange={(v) => !v && setRegion(null)}
        base={base}
        number={q.number}
        parts={q.parts}
        sourcePages={pages}
        initial={region ?? "question"}
        resetRubric={locked}
        onDone={(n) => {
          setNotes(n);
          onSaved();
        }}
      />
      <Dialog
        open={ask !== null}
        onOpenChange={(v) => !v && setAsk(null)}
        title={`${q.number}번은 루브릭을 이미 만들었습니다`}
        desc={
          <>
            소문항·배점·본문을 고쳐 저장하면 이 문항의 루브릭은 맞지 않게 되어 비켜 둡니다 (지우지 않고 따로 보관). 고친 뒤 루브릭을 다시 만들고 승인해
            주세요 — 이미 채점한 답안은 다시 채점해야 합니다. 해설만 고치면 루브릭은 그대로입니다.
          </>
        }
        footer={
          <>
            <Button variant="ghost" onClick={() => setAsk(null)}>
              취소
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setUnlocked(true);
                ask?.();
                setAsk(null);
              }}
            >
              고치기
            </Button>
          </>
        }
      />
    </Card>
  );
}

/** 원본에서 읽기를 열 때 보여 줄 쪽: 이 문항을 읽은 쪽. 강사가 더한 문항이면 앞 문항 다음 쪽. */
function guessPages(questions: ExamQuestion[], i: number): number[] {
  const own = questions[i]!.source_pages ?? [];
  if (own.length) return own;
  for (let k = i - 1; k >= 0; k--) {
    const before = questions[k]!.source_pages ?? [];
    if (before.length) return [Math.max(...before) + 1];
  }
  return [];
}

/** 빠진 문항 더하기: 번호·배점·유형만 — 본문·해설은 '원본에서 읽기'나 '고치기'로. */
function AddQuestion({ onAdded }: { onAdded: () => void }) {
  const { base } = useExam();
  const [open, setOpen] = useState(false);
  const [number, setNumber] = useState("");
  const [points, setPoints] = useState("");
  const [itemType, setItemType] = useState<PartKind>("essay");
  const act = useAction();
  const add = () =>
    act.run(async () => {
      await api.post(`${base}/questions`, { number: number.trim(), points: Number(points), item_type: itemType });
      setOpen(false);
      setNumber("");
      setPoints("");
      onAdded();
    });
  if (!open)
    return (
      <div>
        <Button variant="ghost" onClick={() => setOpen(true)}>
          <Plus /> 문항 더하기
        </Button>
      </div>
    );
  return (
    <Card className="flex flex-wrap items-center gap-3 px-5 py-3">
      <span className="text-[13px] text-text-2">번호</span>
      <Input value={number} onChange={(e) => setNumber(e.target.value)} placeholder="예: 4" className="h-8 w-24" autoFocus />
      <span className="text-[13px] text-text-2">배점</span>
      <Input type="number" min={0} step={0.5} value={points} onChange={(e) => setPoints(e.target.value)} className="h-8 w-20 text-right" />
      <Segmented
        value={itemType}
        onChange={setItemType}
        options={[
          { value: "short_answer", label: "단답형" },
          { value: "essay", label: "서술형" },
        ]}
      />
      <div className="flex-1" />
      {act.error && <ErrorNote message={act.error} />}
      <Button variant="ghost" onClick={() => setOpen(false)}>
        취소
      </Button>
      <Button disabled={act.busy || !number.trim() || !(Number(points) > 0)} onClick={() => void add()}>
        더하기
      </Button>
    </Card>
  );
}

/** ① 시험 읽기: 읽은 문항·소문항·배점·해설지 답을 확인하고 고친다. 배점 표시가 없던 문서는 자리값(2점). */
export function ReadStep() {
  const { id, base, detail, reload } = useExam();
  const by = useTeacher();
  const numbers = detail.exam.questions.map((q) => q.number);
  const { details, error, reload: reloadDetails } = useQuestionDetails(base, numbers);
  const act = useAction();
  const [msg, setMsg] = useState("");
  const total = detail.exam.questions.reduce((a, q) => a + q.points, 0);
  const parts = detail.exam.questions.reduce((a, q) => a + Math.max(1, q.parts.length), 0);
  const doubtful = Object.values(details).reduce((a, d) => a + (d.key && !d.key.confirmed ? d.key.doubtful.length : 0), 0);
  const missing = Object.values(details).filter((d) => d.stem_missing).length;
  const noRubric = detail.questions.every((q) => q.rubric === null);
  const refresh = () => {
    reload();
    reloadDetails();
  };
  const makeRubrics = () =>
    act.run(async () => {
      await waitJob(await api.post<Job>(`${base}/rubrics`, { by }), (j) => setMsg(j.progress.message));
      reload();
      go("exams", id, "rubric");
    });
  return (
    <div className="mx-auto max-w-4xl px-6 py-6">
      <div className="mb-4 flex items-center gap-3">
        <span className="text-text-2">
          문항 {numbers.length}개 · 소문항 {parts}개 · {pts(total)}점
        </span>
        {doubtful > 0 && (
          <Badge tone="warn">
            <AlertTriangle /> 해설 원본 확인 {doubtful}곳
          </Badge>
        )}
        {missing > 0 && (
          <Badge tone="warn">
            <AlertTriangle /> 본문 없는 문항 {missing}개
          </Badge>
        )}
        <Help>
          잘못 읽혔으면 문항마다 '고치기'로 소문항·배점·해설을 직접 고치거나, '원본에서 읽기'로 시험지에서 그 부분을 지정해 다시 읽힙니다. 배점 표시가
          없는 시험지는 소문항마다 2점으로 두었습니다. 루브릭을 만든 뒤에 소문항·배점·본문을 고치면 그 문항의 루브릭은 다시 만듭니다.
        </Help>
        <div className="flex-1" />
        {noRubric ? (
          <Button variant="primary" disabled={act.busy} onClick={() => void makeRubrics()}>
            {act.busy ? msg || "루브릭 만드는 중…" : "확인하고 루브릭 만들기"} <ArrowRight />
          </Button>
        ) : (
          <Button variant="primary" onClick={() => go("exams", id, "rubric")}>
            루브릭으로 <ArrowRight />
          </Button>
        )}
      </div>
      {(error || act.error) && (
        <div className="mb-3">
          <ErrorNote message={(act.error ?? error)!} />
        </div>
      )}
      <div className="flex flex-col gap-3">
        {detail.exam.questions.map((q, i) => (
          <QuestionCard
            key={q.number}
            q={q}
            pages={guessPages(detail.exam.questions, i)}
            d={details[q.number]}
            locked={detail.questions.find((x) => x.number === q.number)?.rubric != null}
            onSaved={refresh}
          />
        ))}
        <AddQuestion onAdded={refresh} />
      </div>
    </div>
  );
}
