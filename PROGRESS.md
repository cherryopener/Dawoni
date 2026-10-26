# Dawoni 진행 기록

> 이어서 작업할 때 이 파일부터 읽는다. 설계는 [PLAN.md](PLAN.md).
> 각 마일스톤은 PLAN.md §8의 **완료 기준**을 통과해야 완료로 표시한다.

## 검사 명령 (모든 커밋 전에 통과해야 함)

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
```

## 마일스톤

- [x] **M0 기반** (2026-10-25)
  - uv 프로젝트(Python 3.16.0a1), ruff·pyright(strict)·pytest
  - `paths.py` 앱 데이터/작업공간 폴더, `config.py` 설정(원자적 저장, 손상 시 백업, 알 수 없는 키 무시, 0600 권한)
  - `logs.py` dawoni.log + error.log, `fsio.py` 원자적 쓰기·SHA-256, `workspace.py` 작업공간 만들기/열기
  - `models/` 핵심 계약: 런/블록(수식 분리, 보기·조건·상자·표), 문항(`ItemContent`=AI 출력 / `Item`=저장), 변수·제약, 정답 3종, 그림(원본+스펙, 빗면·함수그래프 2종), 작업 진행률, 원본 자료
  - 문항 검증: 선지 번호, 정답-선지 일치, 그림 참조, 정의되지 않은 변수, 예약어 변수명, 변수 범위
  - `dawoni version | schema <모델> | workspace init <경로>`
  - 완료 기준: pyright 0 오류, 테스트 58개 통과, 모든 스키마 생성·`additionalProperties:false`·튜플 스키마 미사용 확인
- [x] **M1 CLI 브리지** (2026-10-25)
  - `agents/base.py` 요청·결과·오류 코드(한국어 안내 포함), `detect.py` 설치·버전·로그인·전역 지침 파일 경고(맥 GUI PATH 보강)
  - `sandbox.py` 호출마다 격리 폴더, 첨부는 `inputs/input_NN.ext`로 복사, stdin·stdout·stderr 기록 보존(실패 시)
  - `runner.py` 프롬프트는 항상 stdin + 즉시 EOF, 무출력 감시(idle) + 전체 제한 시간 + 취소, 프로세스 그룹째 종료
  - 어댑터: agy(실측), claude(옵션 조합 실행 확인), codex(미설치 — 문서 기준, 미검증)
  - `client.py` 미설치·로그인 필요·한도 초과일 때만 다음 AI로 전환
  - `dawoni agent detect`, `dawoni agent test [--cli] [--model] [--keep]`
  - 완료 기준: 실제 agy로 이미지 판독 + 스키마 강제 JSON 통과(권한 승인 없이), 가짜 CLI로 멈춤·시간 초과·취소·한도·인증·권한 거부·비정상 종료·200KB 한글 프롬프트 시나리오 통과. 테스트 108개 + 실연결 1개(`-m agent`)
- [x] **M2 메타프롬프트·컨텍스트 엔진** (2026-10-25)
  - `context/templates/` 계약·규칙 정제·수리 템플릿 (첫 줄 version, 빠진 변수는 오류)
  - `assembler.py` 레이어 순서 고정(계약→과업→규칙→범위→자료→예시→대상→출력 형식→수리), 토큰 예산: 예시→범위→자료 순으로 줄이고 그래도 넘치면 오류(조용히 자르지 않음)
  - `extract.py` 응답에서 JSON 꺼내기(울타리·앞뒤 설명·여러 조각), pydantic 오류 → 고칠 수 있는 문장
  - `schema.py` CLI용 스키마: `$ref` 펼치기·`discriminator`/`title` 제거 (agy가 `$ref`를 못 읽음 — 실측)
  - `engine.py` 조립 → 호출 → 추출 → 스키마 검증 → 작업별 검증 → 수리(최대 2회, 같은 AI), `calls.jsonl` 기록(프롬프트 본문은 남기지 않음)
  - `rules.py` 전역 규칙 1개 = 파일 1개, ON/OFF, 적용 대상(8개 작업 또는 전체), 원문 요청 보관
  - `refine.py` 자연어 요구 → 규칙 초안(모호하면 확인 질문, 기존 규칙과 충돌 표시)
  - `dawoni rules list|add [--yes]|on|off|rm|preview <작업>`
  - 완료 기준: 깨진 JSON·스키마 위반·작업별 검증 실패를 주는 가짜 CLI → 2회 안에 복구, 포기 시 3회 기록. ON·해당 작업 규칙만 프롬프트에 들어감. agy 실호출: 규칙 정제 3건(모호함 질문, 충돌 감지 포함) 정상. 테스트 140개 + 실연결 2개
- [ ] M3 채점 파이프라인 1차 (범위 프로필 → 범위 해설 → 루브릭 → 채점·이중 채점)
- [ ] M4 손글씨 판독
- [ ] M5 자료 DB화 (교재 개념·기법 → 범위 프로필, 시험지 PDF)
- [ ] M6 수식·DOCX
- [ ] M7 UI
- [ ] M8 보정 고도화
- [ ] M9 문항 제작 (동형 미지수형·수치형, 신규 A: 같은 범위·다른 기법, 신규 B: 확장 개념+제시문)
- [ ] M10 파라미터 그림
- [ ] M11 패키징
- [ ] M12 확장 (유튜브 요약, Drive 팀 공유)

## 다음에 할 일 / 미뤄 둔 것

- 그림 스펙 안의 식(`angle_deg: "theta"`)이 정의된 변수만 쓰는지 검사 → M4에서 렌더러가 식을 평가할 때 함께 구현
- 채점 모델(`models/grading.py`) → M9
- codex 어댑터 실측 (설치 후 `dawoni agent test --cli codex`)
- `selftest._extract_json`을 `context.extract.extract_json_object`로 교체
- 규칙 정제 응답이 27~54초(gemini-3.8-flash-medium) — 짧은 내부 작업은 더 가벼운 모델(flash-low) 지정 검토
- claude의 `rate_limit_event` 사용률을 대기열 속도 조절에 활용 → M7 이후
- agy가 `~/.gemini/GEMINI.md`를 실제로 읽는지 확인 (현재 빈 파일이라 영향 없음, detect가 비어 있지 않으면 경고)

## 작업 로그

- 2026-10-27: PLAN §9 문항 제작 추가(동형 미지수형·수치형, 신규 A·B). 실제 시험 세트는 나중에 제공 → M3는 합성 세트로 기능 먼저.

- 2026-10-25: M2 완료. 템플릿 `render(name=…)` 인자 충돌 버그를 테스트로 발견·수정. agy가 pydantic 스키마의 `$ref`를 처리하지 못해 스키마 펼치기 추가.

- 2026-10-25: PLAN v2 — 목적(강사 기준 서술형 채점)을 척추로 재배치. 채점 파이프라인을 M3로 앞당기고 PDF·DOCX·그림은 뒤로.

- 2026-10-25: M1 완료. agy 권한 동작 실측(읽기 허용, 쓰기·변경 명령 자동 거부, plan 모드 토큰 4배). claude 격리 옵션 조합 확인 중 1회 실제 호출(약 $0.03)이 일어남 — 이 개발 세션 환경의 인증이 상속됨. 이후 테스트는 agy만 사용.
- 2026-10-25: M0 보완 — 그림(그래프) 선지 규칙(PLAN §5-5): 선지 그림은 원본 그대로, `note` 필수, 한 장짜리 선지 그림(`choices_figure_id`), `choice_layout`. 변수 자리표시를 `{{m}}`에서 `\var{m}`으로 변경(LaTeX 이중 중괄호 충돌). 테스트 75개.
- 2026-10-25: 계획 v1 확정. Claude Code CLI 2.1.310 설치(로그인 전). agy stdin(NDJSON) 호출 방식 실측. AIHelper 파일은 `legacy/aihelper/`로 이동. M0 완료.
