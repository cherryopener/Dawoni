# Dawoai (다워이)

학원 강사가 이미 구독 중인 AI의 공식 CLI(Antigravity `agy`, Claude Code, Codex CLI, 로컬 LLM)를 일꾼으로 부려
**손글씨 서술형 답안을 해설지 기준으로, 토큰을 적게 쓰면서 일관되게 채점**하는 도구.
다워니(`~/Dev/Dawoni`, 커밋 `b3902b4`)에서 채점 기능만 떼어 왔다.

설계와 진행 순서는 [PLAN.md](PLAN.md)를 본다.

## 개발 환경

```bash
uv sync
uv run pytest
uv run ruff check .
uv run pyright
```

- Python 3.13 (`.python-version`). 다워니(3.15)에서 낮췄다 — PLAN.md §12
- 실제 AI CLI를 호출하는 테스트는 기본 제외: `uv run pytest -m agent`
- 실제 자료 회귀 테스트는 `real_data/`(git 제외, 다워니 실측 시험·답안)가 있을 때만 돈다.
- Windows에서 한글·Word COM을 쓰는 기능(선택): `uv sync --extra windows`
  (그 뒤 `uv run`은 `uv run --extra windows`로 — 그냥 `uv run`은 pywin32를 지운다)
- 가짜 AI CLI(`tests/fake_agy.py`)는 macOS·Linux에서 셸 스크립트, Windows에서 `.cmd`로 만든다.
  그래서 Windows에서도 테스트를 건너뛰지 않는다.

## HWPX (M0 실험, 2026-11-04)

판독본·채점본 HWPX는 [python-hwpx](https://github.com/airmang/python-hwpx)(Apache-2.0, lxml만 필요,
한글 없이 동작)로 만든다. OWPML을 직접 쓰지 않는다 — PLAN.md §5-3의 "라이브러리가 있으면 쓴다".

```bash
uv run python experiments/hwpx_probe.py --scan real_data/20261028172150.pdf --page 2
uv run python experiments/hwpx_latex_coverage.py tests/fixtures <다워니 작업공간>/exams
uv run --extra windows python experiments/hwpx_probe.py --hancom   # 한글로 열어 PDF·HWPX 다시 저장
```

| 확인한 것 | 결과 |
|---|---|
| 판독본: 머리 표식, 원본 스캔 그림, 소문항 표식(`■ 1-(1)`, 고정 문단 스타일), 불확실한 글자 노란 형광펜, 수식, 지운 줄(취소선) | 만들기·스키마 검사 통과 |
| 채점본: 점수표, 파란/빨간 코멘트, 맞은 근거 연두 형광펜, 틀린 곳 빨간 밑줄, 메모 | 만들기·스키마 검사 통과 |
| 판독본을 다시 읽기 (머리·소문항·줄·수식) | 쓴 것과 같음 |
| 강사가 판독문 한 줄을 고친 파일을 다시 읽기 | 고친 내용 반영 |
| LaTeX → 한글 수식 (실제 자료 식 81개: 다워니 해설지·픽스처) | 81개 모두 바뀜. 되돌렸다 다시 바꿔도 같은 스크립트. `\quad`·`\ `는 미리 공백으로 바꿔야 함 |
| 한글 2024에서 열기·PDF 저장·다시 저장 (`--hancom`) | **아직 확인 못 함** — 아래 |

- 한글 COM 자동화는 파일을 열 때 '접근 허용' 창을 띄운다. 한글 자동화 보안 모듈
  (`FilePathCheckerModule`)이 등록된 PC에서만 창 없이 돈다. 창을 숨기면 아무도 답할 수 없어
  멈추므로 `--hancom`은 한글 창을 보이게 연다 — 창이 뜨면 [모두 허용]을 누른다.
- 다시 읽을 때 수식은 한글 수식 스크립트 → LaTeX로 되돌리므로 표기가 조금 바뀐다(`^2` → `^{2}`,
  `\ge` → `\geq`). 강사가 고치지 않은 식은 앱 데이터에 둔 원래 LaTeX를 쓰면 된다 (M3에서).
