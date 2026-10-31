// 구간에서 DB화에 실패한 문항·정답이 없는 문항 → 직접 입력 (AI 없음).
import { useState } from "react";
import { api, errorText, useLoad } from "../lib/api";
import type { ChunkManual as Data, ItemManual, ManualQuestion, SourceChunk } from "../lib/types";
import { ManualEditor, type ManualTarget } from "./ManualEditor";
import { Button, Empty, ErrorBox, Info, Loading, Modal, Row, Stack } from "./ui";

/** 문항 ID 끝의 번호 ("bk-…-c11-12" → 12). */
const num = (id: string) => Number(id.split("-").pop()) || 0;

export function ChunkManualButton({ sourceId, c, onChanged }: { sourceId: string; c: SourceChunk; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  if (c.status !== "failed" || c.pages.length === 0) return null;
  return (
    <>
      <Button small onClick={() => setOpen(true)} title="실패한 문항을 원본을 보며 직접 넣습니다 (AI 없음)">
        직접 입력
      </Button>
      {open && <ChunkManualModal sourceId={sourceId} c={c} onChanged={onChanged} onClose={() => setOpen(false)} />}
    </>
  );
}

function ChunkManualModal({ sourceId, c, onChanged, onClose }: {
  sourceId: string; c: SourceChunk; onChanged: () => void; onClose: () => void;
}) {
  const url = `/bank/sources/${sourceId}/chunks/${c.index}/manual`;
  const { data, error, reload } = useLoad<Data>(url);
  const [target, setTarget] = useState<ManualTarget | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const openNew = (q: ManualQuestion) => setTarget({ kind: "new", sourceId, index: c.index, q });
  const openItem = (itemId: string) => {
    setErr(null);
    Promise.all([
      api.get<ItemManual>(`/bank/items/${encodeURIComponent(itemId)}/manual`),
      api.get<{ item: { revision: number } }>(`/bank/items/${encodeURIComponent(itemId)}`),
    ])
      .then(([m, it]) => setTarget({ kind: "item", itemId, m, revision: it.item.revision }))
      .catch((e: unknown) => setErr(errorText(e)));
  };
  const saved = () => { setTarget(null); reload(); onChanged(); };
  return (
    <Modal title={`직접 입력 · ${c.label ?? `구간 ${c.index}`}`} onClose={onClose}>
      <ErrorBox error={error ?? err} />
      {!data ? <Loading /> : (
        <Stack gap={10}>
          <Info>
            AI 없이 원본 쪽을 보며 넣습니다. 글은 PDF 글자로 미리 채워 두었고(깨졌거나 짧으면 고쳐 쓰세요),
            그림은 레이아웃이 잘라 둔 것을 확인만 하면 됩니다. 저장한 문항은 ‘검토함’이 됩니다.
          </Info>
          {data.questions.length === 0 && data.need_answer.length === 0 && <Empty>채울 문항이 없습니다.</Empty>}
          {data.questions.length > 0 && (
            <Stack gap={4}>
              <b style={{ fontSize: 13 }}>DB화에 실패한 문항 ({data.questions.length})</b>
              {data.questions.map((q) => (
                <Row key={q.number}>
                  <span><b>{q.number}번</b> · {q.pages.join(", ")}쪽 · 그림 {q.figures.length}개</span>
                  <span style={{ flex: 1 }} />
                  <Button small variant="primary" onClick={() => openNew(q)}>입력</Button>
                </Row>
              ))}
            </Stack>
          )}
          {data.need_answer.length > 0 && (
            <Stack gap={4}>
              <b style={{ fontSize: 13 }}>
                해설 {data.key_failed_pages.join(", ")}쪽을 못 읽어 정답이 없는 문항 ({data.need_answer.length})
              </b>
              <span style={{ fontSize: 12.5, color: "var(--text-2)" }}>
                다시 DB화하면 그 쪽만 다시 읽습니다. 직접 넣으려면 문항마다 정답을 적으세요.
              </span>
              <Row style={{ gap: 6 }}>
                {[...data.need_answer].sort((a, b) => num(a) - num(b) || a.localeCompare(b)).map((id) => (
                  <Button key={id} small onClick={() => openItem(id)}>{id.split("-").pop()}번</Button>
                ))}
              </Row>
            </Stack>
          )}
        </Stack>
      )}
      {target && <ManualEditor target={target} onSaved={saved} onClose={() => setTarget(null)} />}
    </Modal>
  );
}
