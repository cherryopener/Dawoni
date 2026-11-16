"""그래프 답 판독·채점 확인: 가상 답안지 1장으로 판독 1번 + 채점 1번 (2026-11-17, README "그래프 답 판독").

    uv run python experiments/graph_answer.py              # 앱 기본 AI
    uv run python experiments/graph_answer.py --agent agy --model gemini-3.8-flash-medium

가상 답안지: 인쇄된 좌표축(눈금 숫자 없음) 위에 학생이 그린 h-t 그래프 (연직 위로 20 m/s, g = 10).
학생이 한 것과 기대 채점 (가상 해설지: 개형 1 · 지면 4 s 1 · 최고점 시각 2 s 1 · 최고점 높이 20 m 1):
- 개형: 원점에서 위로 볼록한 포물선, t축에서 끝남 → R1 1점
- t축 끝에 "4"를 적음 → R2 1점 (written)
- 최고점 시각은 적지 않음, 꼭짓점은 두 절편의 가운데 → R3 0점 + "위치로만 맞음" (position)
- 꼭짓점에서 h축으로 점선, "15"를 적음 → R4 0점 (정답 20 — 판독이 20으로 고쳐 읽으면 안 됨)
- 꼭짓점 위에 "v=0" (그래프와 따로 한 줄)
작업공간·전역 규칙은 쓰지 않는다. 결과는 `experiments/out/graph/[<label>/]`.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from dawoni.agents import AgentClient
from dawoni.config import AgentName, load_config
from dawoni.context import ContextEngine
from dawoni.context.calllog import CallLog
from dawoni.grading.pipeline import grade_answer
from dawoni.grading.render import transcript_text
from dawoni.levels import parse_level, preset_scope
from dawoni.models.grading import Exam, ExamQuestion, Rubric, RubricItem
from dawoni.ocr.pages import prepare_pages
from dawoni.ocr.transcribe import transcribe_student
from dawoni.paths import AppPaths

OUT = Path(__file__).resolve().parent / "out" / "graph"
GOTHIC = "/System/Library/Fonts/Supplemental/AppleGothic.ttf"  # 맥 글꼴 — 다른 PC는 바꿀 것
HAND = "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf"

STEM = (
    "지면에서 공을 연직 위로 20 m/s의 속력으로 던졌다 (중력 가속도 10 m/s², 공기 저항 무시). "
    "공이 다시 지면에 닿을 때까지 공의 높이 h를 시간 t에 대한 그래프로 그리고, 최고점의 시각과 "
    "높이, 지면에 닿는 시각을 그래프에 나타내시오."
)


def make_sheet(path: Path) -> Path:
    random.seed(7)
    w, h = 1240, 1754
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    f_print, f_small = ImageFont.truetype(GOTHIC, 30), ImageFont.truetype(GOTHIC, 26)
    f_hand = ImageFont.truetype(HAND, 46)
    for y, s in [
        (90, "1. 지면에서 공을 연직 위로 20 m/s의 속력으로 던졌다."),
        (140, "   (중력 가속도는 10 m/s², 공기 저항은 무시한다.)"),
        (200, "   공이 다시 지면에 닿을 때까지 공의 높이 h를 시간 t에 대한 그래프로 그리고,"),
        (245, "   최고점의 시각과 높이, 지면에 닿는 시각을 그래프에 나타내시오. [4점]"),
    ]:
        d.text((90, y), s, font=f_print, fill="black")
    d.rectangle((80, 320, w - 80, 1500), outline="black", width=2)
    ox, oy = 300, 1300  # 인쇄된 축 (눈금 숫자 없음)
    d.line((ox, oy, 1100, oy), fill="black", width=3)
    d.polygon([(1100, oy - 10), (1125, oy), (1100, oy + 10)], fill="black")
    d.line((ox, oy, ox, 400), fill="black", width=3)
    d.polygon([(ox - 10, 400), (ox, 375), (ox + 10, 400)], fill="black")
    d.text((1080, oy + 20), "t(s)", font=f_small, fill="black")
    d.text((ox - 75, 370), "h(m)", font=f_small, fill="black")
    d.text((ox - 35, oy + 10), "O", font=f_small, fill="black")
    pen, sx, top = (25, 35, 90), 170, 560  # 1초 = 170px, 꼭짓점 높이 560px
    pts: list[tuple[float, float]] = []
    for i in range(161):
        t = 4 * i / 160
        jitter = math.sin(i / 7) * 2.5 + random.uniform(-1.2, 1.2)
        pts.append((ox + t * sx + jitter * 0.4, oy - top * (1 - ((t - 2) / 2) ** 2) + jitter))
    d.line(pts, fill=pen, width=5, joint="curve")
    vx, vy = ox + 2 * sx, oy - top
    x = vx
    while x > ox + 6:  # 꼭짓점에서 h축으로 점선
        d.line(
            (x, vy + random.uniform(-1, 1), x - 14, vy + random.uniform(-1, 1)), fill=pen, width=3
        )
        x -= 26
    d.text((ox + 4 * sx - 12, oy + 12), "4", font=f_hand, fill=pen)
    d.text((ox - 70, vy - 30), "15", font=f_hand, fill=pen)
    d.text((vx + 20, vy - 75), "v=0", font=f_hand, fill=pen)
    img.save(path)
    return path


def rubric() -> Rubric:
    def item(n: int, criterion: str, hint: str) -> RubricItem:
        return RubricItem(
            id=f"R{n}", points=1, criterion=criterion, evidence_hint=hint, solution_steps=[]
        )

    return Rubric(
        question_number="1",
        items=[
            item(1, "개형: 원점에서 시작해 위로 볼록한 곡선(포물선)으로 올라갔다가 t축에서 끝남",
                 "개형: 원점에서 시작해 위로 볼록한 곡선, 가로축에서 끝남"),
            item(2, "지면에 닿는 시각 4 s를 그래프에 표시함", "가로축 절편 (가로 4)"),
            item(3, "최고점의 시각 2 s를 그래프에 표시함", "꼭짓점 (2, …)"),
            item(4, "최고점의 높이 20 m를 그래프에 표시함", "꼭짓점 (…, 20)"),
        ],
        based_on_key=True,
        status="approved",
    )  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--label", default="")
    args = ap.parse_args()
    agent: AgentName | None = args.agent
    out = OUT / args.label if args.label else OUT
    out.mkdir(parents=True, exist_ok=True)
    paths = AppPaths.default()
    engine = ContextEngine(
        AgentClient(load_config(paths), paths), calllog=CallLog(out / "calls.jsonl")
    )
    scope = preset_scope(parse_level("고1 물리"))
    item = {"item_type": "essay", "subject": "물리", "unit": "등가속도 운동",
            "stem": [{"type": "paragraph", "runs": [{"t": "text", "v": STEM}]}]}  # fmt: skip
    q = ExamQuestion.model_validate({"number": "1", "points": 4, "item": item})
    exam = Exam(title="가상 그래프 시험", scope_id=scope.id, questions=[q])

    page = prepare_pages([make_sheet(out / "sheet.png")], out / "pages")[0]
    got = transcribe_student(
        engine, exam, scope.level, "가상학생", [page.image], page_refs=[], agent=agent,
        model=args.model,
    )  # fmt: skip
    t = got.transcripts["1"]
    print(f"== 판독 ({got.agent} {got.model})\n{transcript_text(t)}")
    print("notes:", got.notes)
    g = grade_answer(engine, q, rubric(), t, scope, agent=agent, model=args.model)
    print(f"\n== 채점 ({g.agent} {g.model})")
    for j in g.judgements:
        print(f"{j.rubric_item_id}: {j.awarded:g} (확신 {j.confidence}) 인용 {j.evidence_quote!r}"
              f" 감점 {j.deduction_reason!r}")  # fmt: skip
    print(f"총점 {g.total:g}/{g.max_points:g} {g.status} flags {g.flags}")
    (out / "result.json").write_text(
        json.dumps({"transcript": t.model_dump(mode="json"), "grade": g.model_dump(mode="json")},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )  # fmt: skip


if __name__ == "__main__":
    main()
