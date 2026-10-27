# Dawoni (다워니)

학원 강사가 이미 구독 중인 AI의 공식 CLI(Antigravity `agy`, Claude Code, Codex CLI)를 일꾼으로 부려
수학·과학 문항 DB화, 파라미터 그림, 해설·동형문제, 서술형 채점, DOCX 출력을 돕는 도구.

설계와 진행 순서는 [PLAN.md](PLAN.md), 진행 기록은 [PROGRESS.md](PROGRESS.md)를 본다.

## 개발 환경

```bash
uv sync
uv run pytest
uv run ruff check .
uv run pyright
```

- Python 3.15 (`.python-version`), Node 26 (프론트엔드, M7부터)
- 수식 → Word 변환용 pandoc은 `pypandoc-binary`로 함께 설치됨
- 실제 AI CLI를 호출하는 테스트는 기본 제외: `uv run pytest -m agent`

## 화면 (M7)

```bash
cd frontend && npm install && npm run build   # 결과는 dawoni/web/ (커밋하지 않음)
uv run dawoni ui                               # 앱 창 (macOS: pywebview, Windows: Edge 앱 창)
```

- 서버는 `127.0.0.1`에서만 열리고, 실행할 때마다 새 토큰을 만든다 (다른 웹사이트가 부르지 못함).
- 화면 개발: `uv run dawoni ui --no-window`로 서버를 켠 채 `cd frontend && npm run dev`,
  터미널에 찍힌 주소의 `/login?t=…` 부분을 `http://localhost:5173/login?t=…`로 열기.
- 프론트 검사: `cd frontend && npm run typecheck && npm test`

## 서술형 채점: 해설지 기준 루브릭

```bash
uv run dawoni ingest exam 시험지.pdf --title 중간고사 --scope <범위ID>   # 해설 쪽은 자동으로 찾음 (--key-pages 6 | none)
uv run dawoni exam key <시험ID> 해설지.pdf [--pages 6]                   # 해설지가 따로 있을 때
uv run dawoni exam key-confirm <시험ID> --by 김선생                        # 해설지 판독을 원본과 비교한 뒤
uv run dawoni exam solve <시험ID> && uv run dawoni exam rubric <시험ID>
uv run dawoni exam show <시험ID> -q 3                                    # 해설지·AI 풀이·비교·정답 후보
uv run dawoni exam approve <시험ID> -q 3 --by 김선생 [--accept A1] [--reject A2]
```

- 시험지 PDF 뒤의 해설 쪽("부분 점수", "정답 및 해설" 표시)은 문항 영역에서 빼고 **해설지**로 읽는다:
  소문항마다 답·해설·부분 점수 표기(항목별 점수 / 없음 / 범위)·0점 조건.
- 해설지는 새 세션에서 **두 번** 읽어 숫자를 비교하고, 다른 소문항은 '원본 확인'으로 표시한다
  (실측: 한글은 그대로 옮기면서 수식의 숫자를 바꿔 읽음). 강사가 원본과 비교해 확인·수정한다.
- 문항의 소문항((1), (2), (1)① …)마다 배점과 유형(서술형/단답형)을 함께 저장한다.
- AI는 해설지를 **보지 않고** 먼저 푼다 → 루브릭은 해설지의 정답·배점·부분 점수를 기준으로 만들고,
  소문항마다 AI 답과 비교한다: 같음 / AI 오답(해설지 따름) / 다른 정답 후보 / 해설지·문제 의문.
  두 답이 다르면 **둘 다** 문제에 대입해 독립 검산한다 — 해설지 답이 검산을 통과하지 못하면 강사에게
  알린다 (실측: 해설지가 그림과 다른 기호로 답을 적은 경우).
- 소문항 배점 합, "부분 점수 없음"이면 항목 하나, 항목별 점수의 합은 코드가 검사한다.
  다른 AI 세션이 루브릭이 해설지를 지키는지 한 번 더 검토한다.
- 해설지와 **다른 답**은 '정답 후보'로만 둔다 (문제에 대입해 독립 검산). 강사가 인정하기 전에는
  점수를 주지 않고, 학생 답이 후보와 같으면 그 채점을 '검토 필요'로 둔다. 같은 답에 이르는 다른
  풀이·설명은 'AI 제안'으로 표시해 인정한다.

## 유튜브 강의 요약 (M12)

```bash
uv run dawoni lecture summarize "https://youtu.be/…" --scope <범위ID> [--pages 2] --docx
uv run dawoni lecture list | show <요약ID> | export <요약ID>
```

- 영상·음성은 받지 않고 **자막(자동 자막 포함)만** 읽는다. 자막이 없는 영상은 아직 안 됨.
- 약 20분 구간마다 노트 → 합쳐 A4 1장(또는 양면 1장, 2단) 요약. 구간 노트는 저장해 두므로
  긴 강의가 중간에 실패해도 다시 하면 끝난 구간은 건너뛴다.
- 범위 프로필과 비교(다룬 개념·강의에 없는 교재 개념·범위 밖 내용)는 코드가 노트에서 계산.
- Word: 부분마다 `▶ 시각` 링크(영상의 그 시각), 머리에 영상 QR 코드.

## 혼자 쓰기 / 함께 쓰기 (M12)

```bash
uv run dawoni workspace init "<Google Drive 폴더>/다워니" --shared
uv run dawoni workspace status <경로>          # 모드·동기화 폴더·충돌 사본
uv run dawoni workspace mode personal|team <경로>
```

- 함께 쓰기: 여러 강사가 Google Drive·OneDrive·Dropbox 등 동기화 폴더 하나를 작업공간으로 연결.
  설정·기록·캐시·다시 그린 그림은 PC마다 앱 데이터 폴더에 두고, 작업공간에는 시험·답안·채점 결과만.
- 같은 시험을 다른 PC에서 열어 두면 시험 화면에 알림(시험 폴더의 `.inuse/`, 3분 지나면 무시).
- 동기화 프로그램이 만든 충돌 사본(`파일 (1).json`, `… conflicted copy …` 등)을 연결·설정 화면에서 알림.
