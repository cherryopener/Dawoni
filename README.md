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
