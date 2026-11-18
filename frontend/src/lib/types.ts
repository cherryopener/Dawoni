// 서버 응답 꼴 (화면이 쓰는 필드만). 원본은 dawoni/models·dawoni/server.

export type StepKey = "result" | "read" | "rubric" | "scan" | "grade" | "export";

export type Job = {
  id: string;
  kind: string;
  status: "queued" | "running" | "paused" | "succeeded" | "failed" | "cancelled";
  progress: { done: number; total: number; message: string };
  params: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: { code: string; message: string; detail?: string | null; alternatives: string[] } | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type AuthStatus = {
  required: boolean;
  state: "off" | "checking" | "ok" | "needs_login" | "blocked";
  email: string | null;
  name: string | null;
  message: string | null;
  offline: boolean;
};

export type Status = {
  version: string;
  workspace: string | null;
  workspace_ok: boolean;
  workspace_error: string | null;
  workspace_mode: "personal" | "team" | null;
  machine: string;
  default_agent: string;
  teacher: string | null;
  /** 배포본(로그인 켜짐): 강사 이름은 허용 명단에서 온다 — 화면에서 고치지 않는다. */
  teacher_locked?: boolean;
  access_required: boolean;
};

export type Summary = {
  questions: number;
  approved: number;
  students: number;
  pending_transcripts: number;
  needs_input: number;
  graded: number;
  needs_review: number;
  needs_rename: number;
  not_in_roster: number;
  /** 한 번에 채점으로 만든(답안지를 넣은) 시험 — 결과 화면을 먼저 연다. */
  auto: boolean;
  step: StepKey;
};

export type ExamListItem = { id: string; title: string; scope_id: string; created_at: string; summary: Summary };

export type ExamPart = { label: string; points: number; kind: "essay" | "short_answer" | null };
export type ExamQuestion = {
  number: string;
  points: number;
  parts: ExamPart[];
  item: { item_type: string; figures?: { id: string; caption: string | null }[] };
  answer_pages?: number[];
  source_pages?: number[];
};
export type Exam = { id: string; title: string; scope_id: string; questions: ExamQuestion[]; created_at: string; scores_by_class: boolean };

export type QuestionState = {
  number: string;
  points: number;
  item_type: string;
  has_key: boolean;
  rubric: { status: "draft" | "approved"; version: number; total: number; items: number; based_on_key: boolean; attention: number } | null;
};

export type ExamDetail = {
  exam: Exam;
  scope: { id: string; title: string; level: string | null };
  questions: QuestionState[];
  summary: Summary;
};

export type KeyPart = { label: string; answer: string | null; solution: string; concepts: string[]; credit_text?: string | null };
export type AnswerKey = { question_number: string; parts: KeyPart[]; source?: string; pages?: string[]; confirmed: boolean; confirmed_by: string | null; doubtful: string[]; warnings: string[] };

export type RubricItem = { id: string; points: number; criterion: string; evidence_hint: string; part: string | null; concepts: string[] };
export type Decision = "proposed" | "accepted" | "rejected";
/** 해설지와 다른 기준을 어느 쪽으로: 문제 기준 · 해설지 기준 · 강사가 기준을 직접 고침 (null = 아직 안 정함) */
export type KeyChoice = "question" | "key" | "edited";
export type KeyConflict = {
  id: string;
  note: string;
  item_id: string | null;
  key_criterion: string | null;
  question_criterion: string | null;
  choice: KeyChoice | null;
};
export type Rubric = {
  question_number: string;
  items: RubricItem[];
  accepted_alternatives: { id: string | null; description: string; item_ids: string[]; part: string | null; status: Decision; concepts: string[] }[];
  candidates: { id: string; part: string; answer: string; argument: string; status: Decision }[];
  partial: { item_id: string; condition: string; points: number; from_key: boolean }[];
  never_deduct: string[];
  key_conflicts: KeyConflict[];
  review_notes: string[];
  version: number;
  status: "draft" | "approved";
  approved_by: string | null;
  finalized_by: string | null;
  finalized_at: string | null;
  answer_rules: { item_id: string; part: string; answers: string[]; all_correct: boolean }[];
  based_on_key: boolean;
};

export type QuestionDetail = {
  question: ExamQuestion;
  /** 본문을 고칠 수 있는 글 (식은 \( \), 그림 자리는 [그림 F1]) */
  stem_text: string;
  /** 강사가 더한 문항처럼 본문이 아직 없음 */
  stem_missing: boolean;
  key: AnswerKey | null;
  rubric: Rubric | null;
};

export type PartAgreement = { question_number: string | null; n: number; parts: number; same: number; ai_lower: number; ai_higher: number; rate: number | null };
export type Compare = { overall: PartAgreement; questions: Record<string, PartAgreement> };

export type StudentRecord = {
  student_id: string;
  name: string | null;
  class_name: string | null;
  school: string | null;
  cover_name: string | null;
  roster_id: string | null;
  needs_rename: boolean;
  roster_note: string | null;
};

export type Cell = {
  transcript: { confirmed: boolean; source: string; attention: boolean; unreadable: number; lines: number } | null;
  grade: { total: number; max: number; status: "auto" | "needs_review" | "teacher_confirmed"; disagreements: number; stale: boolean } | null;
};
export type ExamStudent = { student: string; record: StudentRecord | null; cells: Record<string, Cell> };

export type BoardRow = { question: string; teacher: string | null; students: number; graded: number; needs_review: number; waiting_input: number; ungraded: number };

export type Piece = { text: string; math: boolean; kind: "good" | "bad" | null };
export type ReviewSheet = {
  student: string;
  name: string;
  class_name: string | null;
  question: string;
  max: number;
  total: number | null;
  status: "auto" | "needs_review" | "teacher_confirmed" | null;
  transcript_confirmed: boolean;
  graded_before_confirm: boolean;
  flags: string[];
  disagreements: string[];
  comment: string;
  unreadable: number;
  page_images: string[];
  parts: {
    label: string;
    points: number;
    awarded: number | null;
    good: { quote: string; reason: string }[];
    bad: { quote: string; reason: string }[];
    lines: { pieces: Piece[]; struck: boolean; uncertain: string[]; unreadable: boolean }[];
  }[];
};

export type Outputs = {
  folder: string;
  folder_exists: boolean;
  scores: string;
  scores_exists: boolean;
  scores_by_class: boolean;
  scores_edited: boolean;
  marked_folder: string;
  marked: string[];
};

/** 판독문 한 줄 (`dawoni/models/grading.py` TranscriptLine). */
export type TranscriptLine = { text: string; part: string | null; math: string | null; confidence: number; uncertain: string[]; struck: boolean };
export type Transcript = {
  student_id: string;
  question_number: string;
  lines: TranscriptLine[];
  source: "typed" | "ocr";
  confirmed: boolean;
  confirmed_by: string | null;
  notes: string[];
};
/** 판독문 고치기 화면 (`GET …/students/{학생}/transcripts/{문항}`). */
export type TranscriptView = {
  student: string;
  name: string;
  class_name: string | null;
  transcript: Transcript;
  page_images: string[];
  needs_attention: boolean;
  unreadable: number;
  question: ExamQuestion;
};

export type InboxPdf = { path: string; name: string; pages: number | null; size: number; modified: number };

export type NoticeKind = "unreadable" | "rename" | "review" | "key" | "rubric" | "proposal" | "job_done" | "job_failed";
export type Notice = {
  id: string;
  kind: NoticeKind;
  exam_id?: string | null;
  exam?: string;
  title: string;
  detail: string;
  time: string;
  link: string[];
  count: number;
};

export type DetectedAgent = {
  name: string;
  installed: boolean;
  version: string | null;
  logged_in: boolean | null;
  notes: string[];
  enabled: boolean;
  model: string | null;
  base_url: string | null;
};

export type Role = { name: string; title: string; default_tier: Tier; tier: Tier };
export type Tier = "fast" | "standard" | "strong";

export type Config = {
  default_agent: string;
  teacher: string | null;
  coordinator_agent: string | null;
  role_tiers: Record<string, Tier>;
  ocr: { cross_check: boolean; clova_enabled: boolean; mathpix_enabled: boolean; clova_invoke_url: string | null; clova_secret: string | null; mathpix_app_id: string | null; mathpix_app_key: string | null };
  scanner: { smb_url: string | null; username: string | null };
};

export type Sharing = { mode: "personal" | "team"; provider: string | null; conflicts: { path: string }[] };

export type Scope = "all" | string;
export type MemoryNote = { id: string; text: string; applies_to: Scope[]; enabled: boolean; proposed: boolean; request: string | null; origin: string };
export type Skill = { id: string; name: string; description: string; applies_to: Scope[]; enabled: boolean; proposed: boolean; request: string | null; body: string };
export type GlobalRule = { id: string; title: string; text: string; enabled: boolean; applies_to: Scope[] };

/** 채점 사례집 (`dawoni/casebook.py`): 강사가 채점 검토에서 바꾼 점수 한 건. */
export type Case = {
  id: string;
  title: string;
  verdict: "인정" | "불인정";
  keywords: string[];
  reusable: boolean;
  enabled: boolean;
  exam: string;
  question: string;
  part: string;
  ai_points: number | null;
  teacher_points: number | null;
  max_points: number | null;
  created_by: string | null;
  created_at: string;
  body: string;
};

export type RosterStudent = { id: string; name: string; class_name: string | null; school: string | null };

export type Level = { id: string; label: string; scope_id: string };

/** 한 번에 채점 (`dawoni/autopilot.py` AutoState). */
export type AutoAsk = { kind: "name" | "unreadable" | "candidate" | "rubric" | "pages"; text: string; student: string | null; question: string | null };
export type AutoState = {
  state: "running" | "done" | "failed";
  stage: string;
  started_at: string;
  finished_at: string | null;
  approved: string[];
  decisions: string[];
  files: string[];
  students: number;
  graded: number;
  review: number;
  asks: AutoAsk[];
  warnings: string[];
  error: string | null;
};

export type HistoryExam = { exam_id: string; title: string; date: string; student_id: string; total: number | null; max: number; graded: number; questions: number; numbers: string[] };
export type StudentHistory = { key: string; name: string; class_name: string | null; school: string | null; roster_id: string | null; exams: HistoryExam[] };

/** 시험지 원본 (영역 지정해서 읽기). PDF가 아니면 그림 붙여 넣기만. */
export type ExamSource = { name: string | null; pdf: boolean; pages: { width: number; height: number }[]; key_pages: number[] };

// ---------------------------------------------------------------- 0단계 해설 다듬기 (dawoni/keyprep.py)

export type LineKind = "item" | "level" | "accept" | "deduct" | "note";
export type LineSource = "key" | "ai" | "ask" | "fill" | "teacher";

export type PrepLine = {
  id: string;
  kind: LineKind;
  text: string;
  points: number | null;
  source: LineSource;
  why: string | null;
  question: string | null;
  /** 강사가 정한 것. null이면 질문은 아직 안 정함(넣지 않음), 나머지는 넣음. */
  decision: "in" | "out" | null;
};

export type PrepPart = { label: string; points: number; answer: string | null; no_partial: boolean; lines: PrepLine[] };

export type SolveCheck = { label: string; answer: string; agrees: "same" | "different" | "unsure"; note: string | null };

export type PrepQuestion = {
  number: string;
  points: number | null;
  /** 시험지에 배점 표시가 없어 ⌊100 ÷ 문항 수⌋로 정함 (1단계와 같은 규칙). */
  points_guessed: boolean;
  text: string;
  figures: string[];
  key_paragraphs: string[];
  key_figures: string[];
  status: "waiting" | "done" | "failed";
  error: string | null;
  checks: SolveCheck[];
  explanation: string | null;
  explanation_in: boolean;
  replace: number[];
  parts: PrepPart[];
  agent: string | null;
  model: string | null;
  edited_by: string | null;
  /** AI에게 참고로 준 지난 채점 사례 (제목). */
  cases: string[];
  problems: string[];
  pending: number;
  /** 강사가 채울 빈칸(□)이 남은 줄 — 채우거나 빼야 HWPX를 받는다. */
  blanks: number;
  has_key: boolean;
};

export type KeyPrep = {
  id: string;
  title: string;
  source_name: string;
  level: string;
  created_at: string;
  state: "running" | "done" | "failed";
  error: string | null;
  questions: PrepQuestion[];
  warnings: string[];
  exported: string | null;
  exported_at: string | null;
};

export type KeyPrepSummary = {
  id: string;
  title: string;
  source_name: string;
  created_at: string;
  state: "running" | "done" | "failed";
  questions: number;
  done: number;
  failed: number;
  pending: number;
  blanks: number;
  exported: string | null;
};

export type PrepExport = { path: string; name: string; warnings: string[]; skipped: string[]; pending: number };
