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

export type ExamPart = { label: string; points: number; kind?: "essay" | "short_answer" | null };
export type ExamQuestion = { number: string; points: number; item: Item; parts?: ExamPart[]; answer_pages?: number[] };

export type KeyPart = {
  label: string;
  answer?: string | null;
  answer_expr?: string | null;
  solution: string;
  credit_policy: "listed" | "none" | "range" | "unspecified";
  credits: { what: string; points: number }[];
  credit_min?: number | null;
  credit_max?: number | null;
  zero_if: string[];
  credit_text?: string | null;
};
export type AnswerKey = {
  question_number: string;
  parts: KeyPart[];
  source: string;
  pages?: string[];
  confirmed?: boolean;
  confirmed_by?: string | null;
  doubtful?: string[];
  notes: string[];
  warnings: string[];
};
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
  unit_ids?: string[];
};

// ---------------------------------------------------------------- 해설·루브릭

export type SolutionStep = { explanation: string; math?: string | null; uses: string[] };
export type Solution = {
  steps: SolutionStep[];
  final_answer: { latex: string; expr?: string | null };
  part_answers?: { label: string; latex: string; expr?: string | null }[];
  alternatives: { summary: string; steps: SolutionStep[] }[];
  out_of_scope_note?: string | null;
  question_number: string;
  verified: boolean;
  check_notes: string[];
  created_by?: string | null;
  created_at?: string | null;
};

export type RubricItem = {
  id: string;
  points: number;
  criterion: string;
  evidence_hint: string;
  solution_steps: number[];
  part?: string | null;
  key_credit?: number | null;
};
export type PartCheck = {
  part: string;
  key_answer: string;
  ai_answer: string;
  verdict: "same" | "ai_wrong" | "other_valid" | "key_doubt";
  note: string;
  key_check?: "valid" | "invalid" | "unsure" | null;
  key_check_note?: string | null;
  ai_check?: "valid" | "invalid" | "unsure" | null;
  ai_check_note?: string | null;
};
export type AnswerCandidate = {
  id: string;
  part: string;
  answer: string;
  argument: string;
  item_ids: string[];
  status: "proposed" | "accepted" | "rejected";
  check?: "valid" | "invalid" | "unsure" | null;
  check_note?: string | null;
};
export type StudentAnswerSide = {
  student: string;
  side: "key" | "candidate" | "other";
  candidate_id?: string | null;
  quote: string;
};
export type PartAnswerSides = {
  part: string;
  key_answer: string;
  candidates: Record<string, string>;
  sides: StudentAnswerSide[];
  blank: string[];
};
export type CandidateMatches = {
  question_number: string;
  parts: PartAnswerSides[];
  unconfirmed: string[];
  created_at: string;
};
export type RubricDraft = {
  items: RubricItem[];
  partial: { item_id: string; condition: string; points: number; from_key?: boolean }[];
  accepted_alternatives: { description: string; item_ids: string[] }[];
  never_deduct: string[];
  checks?: PartCheck[];
  candidates?: AnswerCandidate[];
  key_conflicts?: string[];
};
export type Rubric = RubricDraft & {
  question_number: string;
  based_on_key?: boolean;
  review_notes?: string[];
  version: number;
  status: "draft" | "approved";
  approved_by?: string | null;
  approved_at?: string | null;
  created_by?: string | null;
  created_at?: string | null;
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
  teacher_part_scores?: Record<string, number>;
  teacher_part_notes?: Record<string, string>;
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
  has_key?: boolean;
  rubric: null | {
    status: "draft" | "approved";
    version: number;
    total: number;
    items: number;
    based_on_key?: boolean;
    attention?: number;
  };
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
  workspace_mode: WorkspaceMode | null;
  machine: string;
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
  transcript_doubts?: string[];
};

// ---------------------------------------------------------------- 문항 제작 (M9)

export type GenKind = "numeric" | "symbolic" | "condition" | "novel_a" | "novel_b" | "new";
export type AnswerFormat = "choice5" | "bogi" | "short" | "essay";
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
  source: { exam_id?: string | null; question_number: string; bank_item_id?: string | null };
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
  bank_item_id?: string | null;
  parts?: ExamPart[];
  unit_ids?: string[];
  idea?: string | null;
  answer_format?: AnswerFormat | null;
};

// ---------------------------------------------------------------- 강의 요약 (M12)

export type SummarySection = { heading: string; start_s: number; bullets: string[]; formulas: string[]; concept_ids: string[] };
export type LectureSummary = {
  id: string;
  title: string;
  overview: string;
  sections: SummarySection[];
  key_formulas: string[];
  tips: string[];
  review_questions: string[];
  video_id: string;
  url: string;
  video_title: string;
  channel: string | null;
  duration_s: number;
  scope_id: string | null;
  pages: 1 | 2;
  covered: string[];
  missing: string[];
  beyond: string[];
  caption_generated: boolean;
  agent: string | null;
  model: string | null;
  created_at: string;
};

// ---------------------------------------------------------------- 함께 쓰기 (M12)

export type WorkspaceMode = "personal" | "team";
export type Sharing = { mode: WorkspaceMode; provider: string | null; conflicts: { path: string; original: string }[] };
export type Presence = { machine: string; teacher: string | null; host: string; at: string };

// ---------------------------------------------------------------- 문제은행

export type Unit = { id: string; title: string; children: Unit[] };
export type UnitTree = {
  id: string;
  subject: string;
  group: string;
  curriculum?: string | null;
  units: Unit[];
  aliases: Record<string, string>;
  reviewed: boolean;
  version: number;
  updated_at: string;
  updated_by?: string | null;
};

export type BankSourceKind = "problem_bank" | "textbook" | "mock_exam";
export type OriginalLevel = "none" | "answer_only" | "brief" | "full";
export type BankSummary = {
  id: string;
  number: string;
  item_type: ItemType;
  points: number;
  subject: string | null;
  unit_ids: string[];
  source: string;
  source_id: string | null;
  source_kind: BankSourceKind | "dawoni" | "exam" | "other";
  origin: "ingested" | "variant" | "new";
  original_level: OriginalLevel | null;
  solution_by: "source" | "ai" | null;
  solved: boolean;
  needs_check: boolean;
  reviewed: boolean;
  difficulty: number | null;
  parent_id: string | null;
  preview: string;
  updated_by: string | null;
};
export type AnswerComparison = {
  verdict: "same" | "differs" | "unsure" | "no_original";
  original_answer?: string | null;
  ai_answer?: string | null;
  notes: string[];
  resolved?: "original" | "ai" | null;
  resolved_by?: string | null;
};
export type BankItem = {
  id: string;
  question: ExamQuestion;
  subject?: string | null;
  unit_ids: string[];
  origin: BankSummary["origin"];
  source: { source_id?: string | null; title: string; kind: string; pages: number[]; number_label?: string | null };
  parent_id?: string | null;
  gen_kind?: string | null;
  original?: { level: OriginalLevel; parts: KeyPart[]; pages: number[] } | null;
  solution?: Solution | null;
  solution_by?: "source" | "ai" | null;
  concept_units: Record<string, string>;
  answer_check?: AnswerComparison | null;
  rubric?: RubricDraft | null;
  difficulty?: number | null;
  tags: string[];
  note?: string | null;
  reviewed: boolean;
  reviewed_by?: string | null;
  revision: number;
  created_by?: string | null;
  updated_by?: string | null;
  updated_at: string;
};
export type ChunkStatus = "new" | "ingesting" | "ingested" | "failed";
export type SourceChunk = {
  index: number;
  pages: number[];
  key_pages: number[];
  concept_pages?: number[];
  unit_hint?: string | null;
  label?: string | null;
  status: ChunkStatus;
  concept_status?: ChunkStatus;
  concept_message?: string | null;
  scope_id?: string | null;
  item_ids: string[];
  message?: string | null;
  by?: string | null;
  machine?: string | null;
  updated_at: string;
};
export type BankSource = {
  source_id: string;
  filename: string;
  rel_path: string;
  kind: BankSourceKind;
  pages?: number | null;
  title?: string | null;
  publisher?: string | null;
  year?: number | null;
  level?: string | null;
  unit_trees: string[];
  chunks: SourceChunk[];
  ingest: { status: string; item_count: number; message?: string | null };
  added_at: string;
};
