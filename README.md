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
