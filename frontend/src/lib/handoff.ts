// 0단계(해설 다듬기)에서 다듬은 HWPX를 1단계(새 채점)에 넘긴다 — 파일을 다시 끌어다 놓지 않게.
// 1단계 카드(QuickStart)는 첫 화면에 늘 떠 있으므로(App의 Keep) 알림을 받아 시험지 자리에 넣는다.

type Listener = (f: File) => void;
const listeners = new Set<Listener>();
let waiting: File | null = null;

export function handExam(file: File): void {
  if (listeners.size === 0) {
    waiting = file;
    return;
  }
  listeners.forEach((f) => f(file));
}

/** 받을 곳을 건다. 걸기 전에 넘긴 파일이 있으면 바로 받는다. */
export function onHandedExam(f: Listener): () => void {
  listeners.add(f);
  if (waiting) {
    const w = waiting;
    waiting = null;
    f(w);
  }
  return () => listeners.delete(f);
}
