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

- **Windows가 주력**이다 (학원 PC). 개발·확인은 Windows에서 먼저 하고, macOS 설치·호환성은 나중에 한다
  (아래 "나중에 할 일").
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
uv run python experiments/hwpx_latex_coverage.py tests/fixtures D:/Downloads/Dawoni-windows/temp/exams
uv run --extra windows python experiments/hwpx_probe.py --hancom
```

| 확인한 것 | 결과 |
|---|---|
| 판독본: 머리 표식, 원본 스캔 그림, 소문항 표식(`■ 1-(1)`, 고정 문단 스타일), 불확실한 글자 노란 형광펜, 수식, 지운 줄(취소선) | 만들기·스키마 검사 통과 |
| 채점본: 점수표, 파란/빨간 코멘트, 맞은 근거 연두 형광펜, 틀린 곳 빨간 밑줄, 메모 | 만들기·스키마 검사 통과 |
| 판독본을 다시 읽기 (머리·소문항·줄·수식) | 쓴 것과 같음 |
| 강사가 판독문 한 줄을 고친 파일을 다시 읽기 | 고친 내용 반영 |
| LaTeX → 한글 수식 (실제 자료 식 81개: 다워니 해설지·픽스처) | 81개 모두 바뀜. 되돌렸다 다시 바꿔도 같은 스크립트. `\quad`·`\ `는 미리 공백으로 바꿔야 함 |
| 학생 × 문항 파일 1개 (`2반_김다원_1.hwpx`): 원본 그림 → 학생이 쓴 글 → 쪽 나눔 → 채점 (PLAN.md §15-4) | 한글에서 2쪽으로 열림. 다시 읽기는 채점 머리(`다워이 채점` 스타일)에서 멈춰 판독문만 같게 읽음. 한글이 다시 저장해도 같음 |
| 반별 묶음 (`(반별)_2반_1.hwpx`): 학생 파일을 이름순으로, 학생마다 새 쪽 | 한글에서 2명 → 4쪽. 탐색기 이름순에서 학생 파일보다 위 |
| 한글 2024에서 열기 (강사 확인) | 깨짐 없이 열림 |
| 한글 2024 COM으로 열기 → PDF·HWPX 다시 저장 → 다시 읽기 (`--hancom`) | 판독본·채점본 모두 저장됨. 한글이 다시 저장한 판독본도 쓴 것과 같음 |

- 한글 COM 자동화는 파일을 열 때 '접근 허용' 창을 띄운다. 한글 자동화 보안 모듈
  (`FilePathCheckerModule`)을 설치한 PC에서는 창 없이 돈다. 없으면 창을 숨겼을 때 아무도 답할 수 없어
  멈추므로 `--hancom`은 한글 창을 보이게 연다 — 창이 뜨면 [모두 허용]을 누른다.
- 한글은 **한 번 띄워 여러 파일에 쓴다.** 파일마다 띄우고 끄면 두 번째 연결이 아직 꺼지는 중인 한글에
  붙어 `The server threw an exception` → `The RPC server is unavailable`로 죽는다.
- python-hwpx의 `add_paragraph`는 기본으로 앞 문단 스타일을 물려받는다 (`inherit_style=True`). 그러면 머리 다음
  문단까지 '다워이 머리'가 되어 다시 읽기가 헷갈리므로 `inherit_style=False`로 쓴다.
- 한글 PDF에서 점수표 글자가 바탕 글꼴(명조)로 나온다 — 표 칸 문단에도 맑은 고딕을 줄 것 (M4).
- 다시 읽을 때 수식은 한글 수식 스크립트 → LaTeX로 되돌리므로 표기가 조금 바뀐다(`^2` → `^{2}`,
  `\ge` → `\geq`). 강사가 고치지 않은 식은 앱 데이터에 둔 원래 LaTeX를 쓰면 된다 (M3에서).

## 역할별 모델 실측 (M0, 2026-11-04, agy 1.4.12)

`experiments/measure_agy.py`로 다워이 엔진(실제 프롬프트·검증·수리 루프)을 그대로 쓰고 agy 모델만 바꿔
같은 실제 자료를 돌렸다: 다워니 실측 시험(`real_data/test.pdf`, 3문항·소문항 8개, 해설지 1쪽)과 그 시험의
학생 답안 스캔 6명분(표지 + 답안 2쪽). claude 모델은 이번에 재지 않았다. 호출 101건, 약 246만 토큰.

| 역할 | 모델 | 시간 중앙값(초) | 토큰 중앙값 | 재요청 | 정확도 |
|---|---|---|---|---|---|
| exam_reader (시험지 3쪽) | flash-low | 110 | 41k | 100% | 실패 — 2쪽에서 명령 실행을 시도해 차단됨 |
| | flash-medium | 656 | 71k | 0% | 소문항·배점 8/8 |
| | **argon-low** | **103** | **16k** | 0% | 8/8 |
| | argon-medium | 180 | 19k | 0% | 8/8 |
| key_reader (해설지 1쪽, 한 번 읽기) | flash-low | 27 | 40k | 0% | 6/7 (1(1) 답 빠뜨림) |
| | flash-medium | 127 | 53k | 0% | 6/7 (1(2) 해설 숫자 빠뜨림) |
| | **argon-low** | **51** | **18k** | 0% | 6/7 (2(1)② 답 칸에 해설을 넣음) |
| | argon-medium | 79 | 22k | 0% | 6/7 (같음) |
| cover_reader (표지 6장) | flash-low | 15 | 20k | 0% | 21/24 — 돌릴 각도 3장 틀림 |
| | flash-medium | 45 | 24k | 0% | 22/24 |
| | **argon-low** | **22** | **12k** | 0% | **23/24** |
| handwriting_reader (6명) | flash-high | 251 | 68k | 0% | 다른 모델과 유사도 93% |
| | flash-medium | 262 | 57k | 0% | 93% |
| | **argon-low** | **38** | **16k** | 0% | 93% |
| | argon-medium | 44 | 17k | 0% | 91% |
| grader (6명 × 1·2번, flash-high 판독문) | flash-low | 27 | 13k | 20% | 강사 점수와 같음 6/10 (차이 합 5점) |
| | flash-medium | 93 | 19k | 0% | 8/10 (3점) |
| | **argon-low** | **38** | **13k** | 0% | 8/10 (3점) |
| | argon-medium | 46 | 13k | 0% | 8/10 (3점) |

- 기준값: 시험지는 다워니 판독(강사 확인), 해설지는 강사가 확인한 다워니 해설지(인쇄본이 틀려 강사가 고친
  3(2)는 뺌 — PLAN.md §1-3), 표지는 사람이 그림을 보고 적은 값(읽기 애매한 이름·학교는 뺌), 채점은 표지의
  빨간 펜 문항 점수. 손글씨는 흐려 사람이 옮긴 기준을 만들지 못해 모델끼리의 일치도만 쟀다.
- 채점은 다워니의 **초안** 루브릭(보정 전)으로 했으므로 일치도는 보정 전 값이다.
- 작은 표본(시험 1개, 학생 6명)이다. 결론은 "argon-low가 어느 역할에서도 flash-medium보다 나쁘지 않고
  3~7배 빠르며 토큰은 약 1/3"까지다.

**기본값 (config.py)**: agy fast = standard = `gemini-3.8-flash-medium`, strong = `gemini-4-argon-low`.
flash와 argon은 구독 한도가 따로다. 정확도가 같으므로 대부분의 호출은 flash-medium에 두고, strong 역할만
argon-low로 보내 두 한도를 나눠 쓴다. flash-low는 쓰지 않는다. 앞으로의 실측도 flash-medium으로 한다
(`measure_agy.py`의 기본 모델).

## 나중에 할 일

- macOS 설치·호환성 확인 (M0에서 미룸): 모든 의존성이 소스 빌드 없이 설치되는지, pywebview 창, 한글 없는
  PC에서 HWPX 만들기·읽기. Windows를 먼저 끝낸 뒤 M8 패키징 전에 한다.
