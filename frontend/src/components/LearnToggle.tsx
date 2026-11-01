import { useState } from "react";
import { notifyJobs } from "../lib/api";
import { Check, TextInput } from "./ui";

/** 저장 요청에 붙이는 "이 수정에서 배우기". 켜면 저장 뒤 배우기 작업이 돌고, 제안은 '메모리 · 스킬'에 뜬다. */
export function useLearn() {
  const [learn, setLearn] = useState(false);
  const [note, setNote] = useState("");
  const fields = { learn, learn_note: learn ? note : "" };
  /** 저장이 끝난 뒤 부른다 — 작업 기록에 배우기 작업이 바로 보이게. */
  const saved = () => {
    if (learn) notifyJobs();
  };
  const view = (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <Check checked={learn} onChange={setLearn}>
        이 수정에서 배우기
      </Check>
      {learn && (
        <span style={{ width: 220 }}>
          <TextInput value={note} onChange={setNote} placeholder="무엇이 틀렸나요? (선택)" />
        </span>
      )}
    </span>
  );
  return { fields, saved, view };
}
