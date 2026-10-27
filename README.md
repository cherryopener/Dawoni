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
