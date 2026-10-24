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
- [ ] M1 CLI 브리지
- [ ] M2 컨텍스트 엔진
- [ ] M3 수식·DOCX
- [ ] M4 파라미터 그림
- [ ] M5 PDF 자료 DB화
- [ ] M6 손글씨/스캔 OCR
- [ ] M7 문항 DB·UI
- [ ] M8 해설·동형문제
- [ ] M9 서술형 채점
- [ ] M10 패키징
- [ ] M11 확장 기능 (시험지 워크플로우, 유튜브 요약, Drive 팀 공유)

## 다음에 할 일 / 미뤄 둔 것

- 그림 스펙 안의 식(`angle_deg: "theta"`)이 정의된 변수만 쓰는지 검사 → M4에서 렌더러가 식을 평가할 때 함께 구현
- 채점 모델(`models/grading.py`) → M9

## 작업 로그

- 2026-10-25: M0 보완 — 그림(그래프) 선지 규칙(PLAN §5-5): 선지 그림은 원본 그대로, `note` 필수, 한 장짜리 선지 그림(`choices_figure_id`), `choice_layout`. 변수 자리표시를 `{{m}}`에서 `\var{m}`으로 변경(LaTeX 이중 중괄호 충돌). 테스트 75개.
- 2026-10-25: 계획 v1 확정. Claude Code CLI 2.1.310 설치(로그인 전). agy stdin(NDJSON) 호출 방식 실측. AIHelper 파일은 `legacy/aihelper/`로 이동. M0 완료.
