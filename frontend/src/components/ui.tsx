import { useEffect, useState, type ReactNode } from "react";
import { api, errorText } from "../lib/api";
import type { Job } from "../lib/types";
import s from "./ui.module.css";

type BtnProps = {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger" | "ghost";
  small?: boolean;
  disabled?: boolean;
  title?: string;
  type?: "button" | "submit";
};

export function Button({ children, onClick, variant = "default", small, disabled, title, type }: BtnProps) {
  const cls = [s.btn, variant !== "default" ? s[variant] : "", small ? s.small : ""].join(" ");
  return (
    <button type={type ?? "button"} className={cls} onClick={onClick} disabled={disabled} title={title}>
      {children}
    </button>
  );
}

export type Tone = "neutral" | "ok" | "warn" | "bad" | "accent";
export function Badge({ children, tone = "neutral", title }: { children: ReactNode; tone?: Tone; title?: string }) {
  const cls = tone === "neutral" ? s.badge : `${s.badge} ${s[`tone-${tone}`]}`;
  return (
    <span className={cls} title={title}>
      {children}
    </span>
  );
}

export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className={s.card}>
      {(title || actions) && (
        <div className={s.cardHead}>
          {title && <h3>{title}</h3>}
          <div className={s.spacer} />
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className={s.field}>
      <span className={s.fieldLabel}>{label}</span>
      {children}
      {hint && <span className={s.hint}>{hint}</span>}
    </label>
  );
}

type InputProps = {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  mono?: boolean;
  type?: "text" | "number" | "password";
  disabled?: boolean;
};
export function TextInput({ value, onChange, placeholder, mono, type = "text", disabled }: InputProps) {
  return (
    <input
      className={`${s.input} ${mono ? s.mono : ""}`}
      value={value}
      type={type}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function NumberInput({ value, onChange, min, max, step }: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <input
      className={s.input}
      style={{ width: 76 }}
      type="number"
      value={Number.isFinite(value) ? value : ""}
      min={min}
      max={max}
      step={step ?? 0.5}
      onChange={(e) => onChange(parseFloat(e.target.value))}
    />
  );
}

export function TextArea({ value, onChange, rows = 3, placeholder, mono }: InputProps & { rows?: number }) {
  return (
    <textarea
      className={`${s.textarea} ${mono ? s.mono : ""}`}
      value={value}
      rows={rows}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function Select<T extends string>({ value, onChange, options }: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <select className={s.select} value={value} onChange={(e) => onChange(e.target.value as T)}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function Check({ checked, onChange, children, disabled }: {
  checked: boolean;
  onChange: (v: boolean) => void;
  children: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className={s.check}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      {children}
    </label>
  );
}

export function ErrorBox({ error }: { error: string | null | undefined }) {
  return error ? <div className={s.error}>{error}</div> : null;
}
export function Notice({ children }: { children: ReactNode }) {
  return <div className={s.notice}>{children}</div>;
}
export function Info({ children }: { children: ReactNode }) {
  return <div className={s.info}>{children}</div>;
}
export function Loading() {
  return <div className={s.empty}>불러오는 중…</div>;
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className={s.empty}>{children}</div>;
}
export function Row({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return (
    <div className={s.row} style={style}>
      {children}
    </div>
  );
}
export function Stack({ children, gap }: { children: ReactNode; gap?: number }) {
  return (
    <div className={s.stack} style={gap ? { gap } : undefined}>
      {children}
    </div>
  );
}
export const tableClass = s.table;

/** 작업 진행 표시 (진행률을 모르면 움직이는 막대). */
function useNow(ms: number): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

function elapsed(fromIso: string | null | undefined, now: number): string {
  if (!fromIso) return "";
  const sec = Math.max(0, Math.round((now - new Date(fromIso).getTime()) / 1000));
  return sec < 60 ? `${sec}초` : `${Math.floor(sec / 60)}분 ${sec % 60}초`;
}

export function JobProgress({ job, onCancel }: { job: Job | null; onCancel?: () => void }) {
  const now = useNow(1000);
  if (!job || ["succeeded", "failed", "cancelled"].includes(job.status)) return null;
  const { done, total, message } = job.progress;
  const pct = total ? Math.round((done / total) * 100) : null;
  return (
    <div className={s.progress}>
      <div className={s.progressRow}>
        <span style={{ flex: 1 }}>{message || "대기 중…"}</span>
        <span title="AI가 생각하는 동안에는 몇 분씩 아무 출력이 없을 수 있습니다">{elapsed(job.started_at ?? job.created_at, now)}</span>
        {total > 0 && (
          <span>
            {done}/{total}
          </span>
        )}
        <Button
          small
          variant="ghost"
          onClick={
            onCancel ??
            (() => {
              api.post(`/jobs/${job.id}/cancel`).catch((e: unknown) => alert(errorText(e)));
            })
          }
        >
          취소
        </Button>
      </div>
      <div className={s.bar}>
        {pct === null ? <div className={s.barIndeterminate} /> : <div className={s.barFill} style={{ width: `${pct}%` }} />}
      </div>
    </div>
  );
}

export function Modal({ title, onClose, children, wide, actions }: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  actions?: ReactNode;
}) {
  useEffect(() => {
    const f = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, [onClose]);
  return (
    <div className={s.backdrop} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`${s.modal} ${wide ? s.wide : ""}`} role="dialog">
        <div className={s.modalHead}>
          <h3>{title}</h3>
          <div className={s.spacer} />
          {actions}
          <Button variant="ghost" onClick={onClose}>
            닫기
          </Button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, tabs }: {
  value: T;
  onChange: (v: T) => void;
  tabs: { value: T; label: ReactNode }[];
}) {
  return (
    <div className={s.tabs} role="tablist">
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          className={`${s.tab} ${t.value === value ? s.tabActive : ""}`}
          onClick={() => onChange(t.value)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** 승인·확인할 때 남길 이름 (이 PC에만 저장). */
export function teacherName(): string {
  try {
    return localStorage.getItem("dawoni.teacher") ?? "";
  } catch {
    return "";
  }
}
export function setTeacherName(v: string): void {
  try {
    localStorage.setItem("dawoni.teacher", v);
  } catch {
    /* 저장 불가 환경 */
  }
}
