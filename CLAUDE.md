# Dawoni (다워니) — Claude용 안내

손글씨 서술형 채점 프로그램. 계획·결정·진행은 [PLAN.md](PLAN.md)(§13 마일스톤, §16 진행 기록), 실측은
[README.md](README.md). 이 파일은 PC를 옮겨도 Claude가 같은 맥락에서 시작하도록 둔다 — 대화 기록과 Claude
메모리는 PC마다 따로라 옮겨지지 않는다.

## 이름 (2026-11-05에 바뀜 — 헷갈리지 말 것)

| 지금 | 예전 | GitHub | 비고 |
|---|---|---|---|
| **Dawoni** (다워니, 이 저장소, 패키지 `dawoni`) | Dawoai (다워이, 패키지 `dawoai`) | `cherryopener/Dawoni` | 개발하는 것 |
| **Dawoni-old** (옛 다워니) | Dawoni | `cherryopener/Dawoni-old` | 참고만. 고치지 않는다 |

- GitHub 저장소 이름을 바꿔서, 예전 주소 `cherryopener/Dawoni.git`은 **이제 이 저장소**다. 옛 다워니를
  예전에 받아 둔 폴더는 원격을 `Dawoni-old.git`으로 바꿔야 한다.
- 문서·코드에서 "옛 다워니"는 Dawoni-old, "다워이"는 이 앱의 예전 이름이다.
- 앱 데이터: `%APPDATA%\Dawoni`(맥: `~/Library/Application Support/Dawoni`). 표식 `dawoni-app.json`이
  없는 `Dawoni` 폴더는 옛 다워니 것으로 보고 `Dawoni-old`로 비켜 두고, `Dawoai` 폴더를 옮겨 온다
  (`paths.settle_app_data`, 처음 켤 때 저절로).

## 플랫폼

- **강사가 쓰는 곳은 Windows**(학원 PC, 한글 2024). 배포 묶음은 GitHub Actions가 Windows zip과 맥 `Dawoni.app`
  zip을 함께 만든다 (`.github/workflows/build.yml`, 태그 `v*` 또는 수동 실행) — 맥에서 태그를 올리면 된다.
  맥 묶음은 `packaging/build_mac.py`(PyInstaller, Apple 실리콘만), 로컬에서도 `uv sync --group bundle-mac` 뒤 그대로 돈다.
- **개발은 2026-11-06부터 맥으로** (그 전까지 Windows 개발 PC). 맥에서 고치고 테스트하고 푸시한다.
  Windows에서만 확인할 수 있는 것은 학원 PC·Windows PC에서 새 exe로 본다:
  - 한글 COM: `.hwp` → HWPX 변환의 예비 경로(`ingest/convert.py` — 파일을 열 때 '접근 허용' 창이 뜰 수 있다,
    보안 모듈을 등록한 PC는 안 뜸). 2026-11-10부터 `.hwp`는 먼저 python-hwpx로 한글 없이 HWPX로 바꾼다(맥에서도).
    python-hwpx가 실패하거나 수식·표 등을 못 옮겼다고 할 때만 Windows에서 한글을 띄운다.
  - Edge `--app` 창, 창을 닫으면 서버가 끝나는지(`server/lifeline.py`), npm `.cmd` 실행 파일(`agents/detect.py`),
    스마트 앱 컨트롤, `%APPDATA%\Dawoni`, Google Drive for desktop.
- **앱은 HWPX를 쓰지 않는다** (2026-11-18 사용자 결정, PLAN §16 31번). 판독문은 앱 화면에서 원본과 나란히
  보고 고쳐 확인한다(답안 판독 → "차례로 확인", 채점 검토 → "판독문 고치기"). 판독문 표시: `[판독불가]`(확인을
  막음) · `[지움]` 학생이 지운 자리 · `[못 읽음]` 강사도 못 읽음 · `~~…~~` 줄 안에서 지운 글씨. 강사가 쓰는 칸에서는 식이 `$수식$`(저장은 `\( \)`) (PLAN §16 42번). 한글에서 고친 HWPX를 다시 읽는
  흐름(`confirm_from_files`, `exam confirm`·`exam export`)도 없앴다. 학생에게 돌려줄 것은 채점 답안지 PDF. 시험 읽기는 HWP·HWPX를 문서로 읽는다(python-hwpx, 그대로) — 한글로 PDF를 만들어 읽는
  것은 해 보고 되돌렸다 (2026-11-06, PLAN §16 18번).
- **macOS 묶음은 서명·공증하지 않는다** (2026-11-06, 학원 안 소수 강사용). 처음 열 때 시스템 설정 → 개인정보
  보호 및 보안 → [그래도 열기] (또는 `xattr -dr com.apple.quarantine`) — 묶음의 '읽어 주세요.txt'에 적었다.
- Windows 개발 PC는 스마트 앱 컨트롤이 켜져 있어 서명 없는 exe(uv 진입점 등)가 막힌다 →
  `uv run python -m pytest`, `uv run python -m pyright`처럼 `python -m`으로 부른다 (맥에서도 같은 명령이 된다).
- Windows Git Bash의 heredoc은 `\\`를 `\`로 줄인다 — 정규식·LaTeX가 든 편집은 파일 편집 도구로.

## 맥으로 옮길 때 (PC를 바꿀 때)

1. `git pull` → `uv sync` → `pnpm --dir frontend install` → `pnpm --dir frontend build`(→ `dawoni/web/`,
   git 제외라 PC마다 만든다).
2. **`real_data/`는 git에 없다** — 손으로 복사한다 (옛 다워니 실측 시험·답안, `real_data/docs/` 학원 시험지 원본:
   HWP·HWPX·PDF·DOCX, `경기형 2회 물리시험지.hwp`는 한글 자동 번호 시험지, `11월 월례평가 문제-물리.hwp`는
   미주 번호 앞에 앞 문항 해설이 든 시험지, `m1j·m1k·m2j.pdf`는 그 시험(`11월 월례평가 문제-물리 (1).hwp`)의
   실제 학생 답안지 스캔 — 반마다 한 파일, 표지 + 답안 쪽, 옆으로 돌아감). 없으면 실제 자료 테스트만 건너뛴다.
3. 확인 명령(아래)이 모두 통과해야 한다. Windows에만 있는 것을 시험하는 테스트는 맥에서 건너뛴다
   (`test_npm_cmd_shim_is_unwrapped_to_the_real_exe`).
4. Claude 메모리는 PC마다 따로다 — 남길 결정은 이 파일에 적는다.

## 확인 명령

```bash
uv run python -m pytest -q
uv run python -m pyright dawoni tests
uv run python -m ruff check dawoni tests
uv run python -m ruff format --check dawoni tests
```

## 0단계 해설 다듬기 (2026-11-18 사용자 결정 — PLAN §16 35번)

- 첫 화면은 **0단계**(해설·부분 점수 다듬기)와 **1단계**(한 번에 채점). 0단계는 건너뛸 수 있다 — 다른 강사는 다른 AI
  채팅으로 해설지를 다듬는다. 그래서 0단계의 결과는 앱 안의 루브릭이 아니라 **다듬은 해설지 HWPX**이고, 1단계에 넣는다.
  0단계 카드에 "부분 점수 기준이 필요하면 여기에 시험지와 해설지를 넣어 주세요" 문구.
- AI는 높은 등급(`key_refiner` = strong)으로, 해설지를 따라 풀고(처음부터 풀지 않음) 부분 점수를 촘촘하게. "부분점수 없음"
  소문항은 점수를 나누지 않는다. AI 추천은 바로 넣고 애매한 것은 질문 — 둘 다 강사가 고칠 수 있다. 해설이 없으면 AI가
  풀어 해설까지 쓴다. `dawoni/keyprep.py`, `export/key_hwpx.py`(원본 HWPX에서 부분 점수 문단만 바꿈), `#/prep/<ID>`.
- 답이 그림(그래프·작도)이면 기준을 글로("위로 볼록, y절편 3"). AI가 그림에서도 풀이로도 정할 수 없는 값은 지어내지
  않고 **강사가 적기** 줄(빈칸 □) — 채우거나 빼야 HWPX를 받는다.

## 채점 사례집 (2026-11-18 사용자 요청 — PLAN §16 36번)

- 채점 검토에서 강사가 바꾼 점수 → `casebook/case-….md` 한 건 (학생 표현·기준·AI → 강사 점수·"다음부터" 규칙·열쇠말).
  계속 쌓이는 판례집이고, 다음 시험의 0단계·1단계 루브릭에 그 문항과 맞는 것만 넣는다 (컨텍스트 상한). 승인 없이 쌓이고
  배우기 화면에서 끄고 고치고 지운다. `dawoni/casebook.py`. 메모리(600자 상한)는 강사의 말·판독 고침에서만 배운다.

## 한 번에 채점 (2026-11-18 사용자 결정 — PLAN §16 33번)

- 기본 흐름은 파일을 놓고 버튼 하나: 시험지(해설 포함) + 답안지 PDF + (명단) → [채점 시작] →
  결과 화면(채점 답안지 PDF·점수표·확인할 것). `dawoni/autopilot.py`, `POST /api/quick`, `exam auto`.
- 루브릭은 **먼저 모두 승인**: AI가 넓힌 표현 인정, 해설지와 문제가 다르다고 본 곳은 **강사의 해설지를
  따름**(강사가 부분 점수 규칙을 고친 해설지를 쓴다), 검산이 맞은 정답 후보만 인정. 강사에게는 정말 애매한
  것만 묻는다 — 이름, 판독불가, 검산이 엇갈린 정답 후보, 승인할 수 없는 루브릭.
- 답안지 PDF마다 강사가 **반**을 고른다 (2026-11-18 사용자 결정, PLAN §16 40번) → 그 반 명단만 표지 프롬프트에
  (7반 명단 86% → 그 반 명단 17~18/18). 명단은 엑셀·CSV·사진(jpg·png, AI가 읽음).
- **채팅에서처럼** (2026-11-19 사용자 결정, PLAN §16 43번): `[판독불가]`가 없는 판독문은 AI가 바로 확정('AI 확인',
  `config.ocr.auto_accept`), 빈 답안은 0점으로 끝, 채점 검토 필요는 정말 애매한 것만 (두 채점이 다름 · 확신 낮은데
  재채점 못 함 · 미확인 정답 후보 · 위치로만 맞음 · `[판독불가]`). AI의 다른 flags는 메모. 강사가 볼 것은
  `[판독불가]`와 그 검토 필요뿐.
- 이름 문제(못 읽음·같은 반 같은 이름·명부에 없음)와 `[판독불가]`는 멈추지 않는다 — 채점하고 "검토 필요",
  점수표·PDF에도 넣고("(이름 확인)"), 나중에 고치면 그 학생·문항만 다시 채점.
- 점수표의 "검토 필요" 칸은 비우지 않고 AI 점수를 노랑으로.
- 채점 답안지 PDF(`export/red_pen.py`): 판독 쪽 그림 위에 빨간 글씨. 칸은 AI 없이 표 줄로 찾고, 이유는
  채점의 `pen`(20자 안팎 메모). 식은 ziamath(LaTeX → SVG, 2026-11-18 사용자 허락으로 의존성 추가).

## AI 모델 (사용자 결정)

- **기본 등급별 모델** (2026-11-19 사용자 결정, PLAN §16 41번 — `config._default_tiers`):

  | | 빠름 | 보통 | 강함 |
  |---|---|---|---|
  | claude | sonnet-5-5 high | opus-5-5 medium | opus-5-5 high |
  | agy | gemini-3.8-flash-high | gemini-4-hydrogen-medium | gemini-4-hydrogen-high |

  설정 파일은 등급을 모두 적어 두므로, 예전 기본 그대로인 칸만 처음 읽을 때 옮긴다 (`config._migrate`,
  `schema_version` 2). 강사가 고친 칸은 그대로.
- 앱 기본 AI는 claude — `config.default_agent` (2026-11-05). sonnet은 채점이 가장 좋았지만
  표지의 한글 손글씨 이름은 gemini보다 못 읽는다 (2026-11-18 명단 없이 18장: hydrogen-medium 14, flash-medium 12,
  opus 9, sonnet 8, haiku 3 — 7반 명단이면 sonnet 86%, haiku 75%, 그 반 명단만이면 opus 18/18, sonnet 17~18/18).
  그래서 표지 판독(`cover_reader`)은 standard 등급.
- 등급(빠름·보통·강함) → 모델은 AI마다 하나이고 모든 역할이 함께 쓴다 — 설정 → 역할별 모델의 '등급별 모델' 표.
- agy: flash-low는 쓰지 않는다 (표지 회전·시험 읽기 실패). 2026-11-18까지는 fast·standard = flash-medium,
  strong = argon-low(flash와 따로인 한도)였다.
- 개발 중 실측(시험 읽기·표지·판독·채점…)은 flash-medium과 `claude-sonnet-5-5`에 한 번씩 보내 나란히
  비교한다. `claude-opus-*`는 사용자에게 먼저 묻는다.

## 내보내기 (2026-11-18)

`exports/<날짜_시험>/(점수표)_시험.xlsx` 하나 (`export/exam_files.py`). 2026-11-18 전에 쓴 HWPX(`반_문항.hwpx`,
`반/반_이름_문항.hwpx`)는 그 폴더에 남아 있고 앱은 건드리지 않는다.
점수표는 강사가 보낸 성적표 모양 (2026-11-18 사용자 요청, PLAN §9-2): 전체 요약 · 반마다 시트(소문항·문항 계·총점·
석차·비고, 배점·평균·최고·최저 — 계·총점은 엑셀 수식) · 채점 근거. 비고에 애매한 곳(원본 확인, 검토 이유, 강사 이유).
- HWP → HWPX(python-hwpx)는 쪽 배경 그림(워터마크)의 밝기·명암을 맞바꿔 읽는다 → `convert.fix_fill_images`로 바로잡음.
