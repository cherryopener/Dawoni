// 서버 모델(pydantic)과 같은 모양. 바뀌면 dawoni/models/*.py와 함께 고친다.

export type AgentName = "agy" | "claude" | "codex";

// ---------------------------------------------------------------- 문항 내용

export type TextRun = { t: "text"; v: string; bold?: boolean; underline?: boolean };
export type MathRun = { t: "math"; v: string };
export type Run = TextRun | MathRun;

export type LabeledItem = { label: string; runs: Run[] };

export type Block =
  | { type: "paragraph"; runs: Run[]; align?: "left" | "center" | "right" }
  | { type: "display_math"; latex: string }
  | { type: "figure"; figure_id: string; width?: number | null }
  | { type: "bogi"; title?: string; items: LabeledItem[] }
  | { type: "conditions"; boxed?: boolean; items: LabeledItem[] }
  | { type: "box"; title?: string | null; paragraphs: Run[][] }
  | { type: "table"; rows: Run[][][]; header_rows?: number };

export type Choice = { label: number; runs: Run[]; figure_id?: string | null; note?: string | null };

export type Figure = {
  id: string;
  mode: "parametric" | "raster";
  orig?: string | null;
  caption?: string | null;
  spec?: Record<string, unknown> | null;
};

export type ItemType = "multiple_choice" | "short_answer" | "essay";
export type ChoiceLayout = "auto" | "row" | "grid_3_2" | "grid_2" | "column";

export type Item = {
  item_type: ItemType;
  subject?: string | null;
  unit?: string | null;
  number_label?: string | null;
  points?: number | null;
  stem: Block[];
  choices: Choice[];
  choice_layout: ChoiceLayout;
  choices_figure_id?: string | null;
  answer?: unknown;
  solution: Block[];
  figures: Figure[];
};

export type ExamQuestion = { number: string; points: number; item: Item };
export type Exam = {
  id: string;
  title: string;
  scope_id: string;
  questions: ExamQuestion[];
  created_at: string;
};

// ---------------------------------------------------------------- 범위 프로필

export type Concept = { id: string; name: string; summary: string; source?: string | null };
export type Skill = { id: string; name: string; description: string; concept_ids: string[] };
export type Forbidden = { name: string; reason?: string | null };
export type Scope = {
  id: string;
  title: string;
  subject?: string | null;
  level: string;
  concepts: Concept[];
  skills: Skill[];
  forbidden: Forbidden[];
  conventions: string[];
  extensions: unknown[];
  version: number;
  updated_at: string;
};
export type ScopeSummary = {
  id: string;
  title: string;
  level: string;
  subject: string | null;
  version: number;
  concepts: number;
  skills: number;
  forbidden: string[];
};

// ---------------------------------------------------------------- 해설·루브릭

export type SolutionStep = { explanation: string; math?: string | null; uses: string[] };
export type Solution = {
  steps: SolutionStep[];
  final_answer: { latex: string; expr?: string | null };
  alternatives: { summary: string; steps: SolutionStep[] }[];
  out_of_scope_note?: string | null;
  question_number: string;
  verified: boolean;
  check_notes: string[];
};

export type RubricItem = {
  id: string;
  points: number;
  criterion: string;
  evidence_hint: string;
  solution_steps: number[];
};
export type RubricDraft = {
  items: RubricItem[];
  partial: { item_id: string; condition: string; points: number }[];
  accepted_alternatives: { description: string; item_ids: string[] }[];
  never_deduct: string[];
};
export type Rubric = RubricDraft & {
  question_number: string;
  version: number;
  status: "draft" | "approved";
  approved_by?: string | null;
  approved_at?: string | null;
};

// ---------------------------------------------------------------- 답안·채점

export type TranscriptLine = {
  text: string;
  math?: string | null;
  confidence: number;
  uncertain: string[];
  struck: boolean;
};
export type Transcript = {
  student_id: string;
  question_number: string;
  lines: TranscriptLine[];
  source: "typed" | "ocr";
  confirmed: boolean;
  confirmed_by?: string | null;
  pages: string[];
  cross_checked: boolean;
  agreement?: number | null;
  notes: string[];
};

export type Judgement = {
  rubric_item_id: string;
  awarded: number;
  evidence_quote?: string | null;
  reason: string;
  deduction_reason?: string | null;
  confidence: number;
};
export type GradeStatus = "auto" | "needs_review" | "teacher_confirmed";
export type Grade = {
  student_id: string;
  question_number: string;
  rubric_version: number;
  judgements: Judgement[];
  comment: string;
  flags: string[];
  total: number;
  max_points: number;
  status: GradeStatus;
  second_opinion?: Judgement[] | null;
  disagreements: string[];
  ai_judgements?: Judgement[] | null;
  teacher_note?: string | null;
  teacher_item_notes?: Record<string, string>;
  confirmed_by?: string | null;
  examples_used?: string[];
  agent?: string | null;
};

// ---------------------------------------------------------------- 요약 (API 전용)

export type QuestionState = {
  number: string;
  points: number;
  item_type: ItemType;
  solution: null | {
    steps: number;
    verified: boolean;
    check_notes: string[];
    out_of_scope_note: string | null;
    final_answer: string;
  };
  rubric: null | { status: "draft" | "approved"; version: number; total: number; items: number };
};

export type ExamSummary = {
  questions: number;
  solved: number;
  approved: number;
  students: number;
  pending_transcripts: number;
  graded: number;
  needs_review: number;
};

export type Cell = {
  transcript: null | {
    confirmed: boolean;
    source: "typed" | "ocr";
    attention: boolean;
    agreement: number | null;
    lines: number;
  };
  grade: null | {
    total: number;
    max: number;
    status: GradeStatus;
    disagreements: number;
    stale: boolean;
  };
};

export type ReviewRow = {
  student: string;
  question: string;
  total: number;
  max: number;
  status: GradeStatus;
  flags: string[];
  disagreements: string[];
  comment: string;
  stale: boolean;
  min_confidence: number;
};

export type JobStatus = "queued" | "running" | "paused" | "succeeded" | "failed" | "cancelled";
export type Job = {
  id: string;
  kind: string;
  status: JobStatus;
  progress: { done: number; total: number; message: string };
  params: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: { code: string; message: string; detail?: string | null } | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
};

export type Status = {
  version: string;
  workspace: string | null;
  workspace_ok: boolean;
  workspace_error: string | null;
  default_agent: AgentName;
  agents: Record<AgentName, { enabled: boolean; model: string | null; executable: string | null }>;
};

export type DetectedAgent = {
  name: AgentName;
  installed: boolean;
  path: string | null;
  version: string | null;
  logged_in: boolean | null;
  notes: string[];
  enabled: boolean;
  model: string | null;
};

export type RuleTarget =
  | "solve"
  | "rubric"
  | "transcribe"
  | "grade"
  | "feedback"
  | "ingest"
  | "variant"
  | "lecture";
export type RuleScope = RuleTarget | "all";
export type GlobalRule = {
  id: string;
  title: string;
  text: string;
  enabled: boolean;
  applies_to: RuleScope[];
  request?: string | null;
};
export type RefinedRule = {
  title: string;
  text: string;
  applies_to: RuleScope[];
  conflicts: { rule_id: string; reason: string }[];
  clarification_needed: string | null;
};

export type ExportResult = {
  path: string;
  name: string;
  ok: boolean;
  summary: string;
  errors: string[];
  warnings: string[];
  math_fallbacks: number;
};

// ---------------------------------------------------------------- 보정 (M8)

export type AgreementStats = {
  n: number;
  exact: number | null;
  within1: number | null;
  item_agreement: number | null;
  mean_abs_diff: number | null;
  bias: number | null;
  item_disagreements: Record<string, number>;
};

export type CalibrationSettings = {
  question_number: string;
  use_examples: boolean;
  max_examples: number;
  pinned: string[];
  excluded: string[];
};

export type ExemplarKind = "correction" | "manual" | "confirmed";

export type CalibrationView = {
  settings: CalibrationSettings;
  candidates: {
    student: string;
    kind: ExemplarKind;
    total: number;
    max: number;
    pinned: boolean;
    changed: Record<string, [number, number]>;
    teacher_note: string | null;
    order: number | null;
  }[];
  excluded: { student: string; total: number; max: number }[];
  agreement: AgreementStats;
  evals: { id: string; created_at: string; rubric_version: number; n: number; before: AgreementStats; after: AgreementStats }[];
  rubric: Rubric | null;
};

export type EvalCase = {
  student_id: string;
  teacher_total: number;
  before_total: number | null;
  after_total: number | null;
  examples_used: string[];
  error: string | null;
};
export type CalibrationEval = {
  id: string;
  question_number: string;
  cases: EvalCase[];
  before: AgreementStats;
  after: AgreementStats;
};

export type RubricSuggestion = {
  changes: { item_id: string | null; kind: string; summary: string; evidence: string[] }[];
  revised: RubricDraft | null;
  no_change_reason: string | null;
};

// ---------------------------------------------------------------- 문항 제작 (M9)

export type GenKind = "numeric" | "symbolic" | "condition" | "novel_a" | "novel_b";
export type Extension = {
  id: string;
  name: string;
  definition: string;
  assumptions: string[];
  expected_inference?: string | null;
};
export type GeneratedSet = {
  id: string;
  kind: GenKind;
  source: { exam_id: string; question_number: string };
  scope_id: string;
  points: number;
  item: Item;
  solution: Solution;
  rubric: RubricDraft | null;
  status: "draft" | "approved" | "rejected";
  checks: string[];
  warnings: string[];
  note: string | null;
  values: Record<string, number> | null;
  extension: Extension | null;
  skill_ids: string[];
  created_at: string;
};
