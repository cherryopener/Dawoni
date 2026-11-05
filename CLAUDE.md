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

- **Windows가 주력**(학원 PC, 한글 2024). 맥 설치·호환 확인은 나중에 (README "나중에 할 일").
- 앱은 한글 없이 python-hwpx로 HWPX를 쓴다. 한글 COM(`pywin32`)은 `experiments/`의 검증에만 쓰며, 파일을
  열 때 '접근 허용' 창이 뜬다(보안 모듈을 등록한 PC는 안 뜸). PDF 내보내기는 하지 않는다 — 필요한
  강사가 한글에서 따로 저장한다 (2026-11-05 결정).
- Windows 개발 PC는 스마트 앱 컨트롤이 켜져 있어 서명 없는 exe(uv 진입점 등)가 막힌다 →
  `uv run python -m pytest`, `uv run python -m pyright`처럼 `python -m`으로 부른다.
- Windows Git Bash의 heredoc은 `\\`를 `\`로 줄인다 — 정규식·LaTeX가 든 편집은 파일 편집 도구로.

## 확인 명령

```bash
uv run python -m pytest -q
uv run python -m pyright dawoni tests
uv run python -m ruff check dawoni tests
uv run python -m ruff format --check dawoni tests
```

## AI 모델 (사용자 결정)

- 앱 기본 AI는 claude(sonnet) — `config.default_agent` (2026-11-05). sonnet은 채점이 가장 좋았지만
  표지의 한글 손글씨 이름을 자주 틀린다 (flash 5/6, opus 3/6, sonnet 1/6).
- agy(gemini)를 쓸 때: fast·standard = `gemini-3.8-flash-medium`, strong = `gemini-4-argon-low`(따로인
  한도). flash-low는 쓰지 않는다 (표지 회전·시험 읽기 실패).
- 개발 중 실측(시험 읽기·표지·판독·채점…)은 flash-medium과 `claude-sonnet-5-5`에 한 번씩 보내 나란히
  비교한다. `claude-opus-*`는 사용자에게 먼저 묻는다.

## 내보내기 배치 (2026-11-05)

`exports/<날짜_시험>/` 맨 위에 반별 묶음 `반_문항.hwpx`와 `(점수표)_시험.xlsx`, 학생 파일은 반 폴더 안
`반/반_이름_문항.hwpx`. 예전 배치(한 폴더, `(반별)_` 접두어)는 다음 내보내기 때 옮긴다 (`export/exam_files.py`).
