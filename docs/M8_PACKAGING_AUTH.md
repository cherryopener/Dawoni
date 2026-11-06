# M8 작업 계획 — exe 빌드(GitHub Actions) + Gist 명단 로그인 + 강사 이름은 명단에서

작성 2026-11-05. 토큰이 모자라 구현은 다음 세션으로 넘긴다. 이 문서만 보고 이어서 할 수 있게 적는다.

## 진행 (2026-11-05, 같은 날 다음 세션)

**만든 것** — §2 강사 이름 잠금, §5 빌드·워크플로. 남은 것은 사용자가 할 §3·§4와 실제 릴리스.

- **§2 그대로**: `AppState.who(by)`가 로그인이 켜져 있으면 화면이 보낸 `by`를 버리고 `Access.teacher`(명단 이름,
  비었으면 이메일)를 쓴다. 서버의 `body.by`·`body.teacher` 전부(35곳)를 거친다 — 새로 생기면
  `test_every_recorded_name_goes_through_who`가 잡는다. 상태 응답 `teacher_locked`, 잠긴 동안
  `PATCH /config`의 `teacher`는 버림. 로그인 전이면 401 `needs_login`(다른 이름으로 기록하지 않음).
  화면: 마법사는 "○○○ 선생님으로 로그인됨"(AI 연결 단계는 그대로 보임), 설정은 읽기 전용 + "관리자 명단에서
  바뀝니다", 로그인 화면의 거절 안내.
- **§5 바뀜 — PyInstaller를 버렸다.** 개발 PC(Windows 11, 스마트 앱 컨트롤 켜짐)에서 PyInstaller로 만든
  서명 없는 `Dawoni.exe`가 실행부터 막혔다 ("An Application Control policy has blocked this file").
  인터넷에서 받은 표시(MOTW)를 붙인 `.cmd`도 막혔다 ("Dangerous file extension from the web").
  통과한 방법: python.org 파이썬(PSF 서명)을 폴더째 담고, **서명된 `pythonw.exe`를 `Dawoni.exe`로 복사**
  (이름을 바꿔도 서명은 유지). 인자 없이 켜지면 `_dawoni_boot.pth`가 `Dawoni.exe -m dawoni`를 띄운다.
  모든 파일에 MOTW를 붙여도 열리고, PyPI 패키지의 .pyd도 불러온다 (README "배포 묶음").
  - 만드는 것: `packaging/build_bundle.py` (`spec`·PyInstaller 의존성은 지움). 로그인 설정은 `--access 파일`
    또는 비밀값 `DAWONI_ACCESS_JSON`을 묶음 안 `dawoni/assets/access.json`으로 (BOM 없이, 내용은 찍지 않음).
    묶음 표식 `dawoni/assets/bundled.json`(버전·커밋·때)이 있으면 `DAWONI_ACCESS_FILE` 환경 변수를 무시한다.
  - 창 없는 실행에는 stdout이 없어 uvicorn이 죽었다 → `python -m dawoni`가 stdout·stderr를
    `%APPDATA%\Dawoni\logs\app.log`로 돌리고, 인자가 없으면 `ui`. AI CLI·taskkill은 `CREATE_NO_WINDOW`
    (안 그러면 호출마다 검은 창).
  - 워크플로 `.github/workflows/build.yml`(처음 이름 build-windows.yml, 2026-11-07 맥 작업을 더하며 바꿈) Windows 작업: 태그 = pyproject 버전 확인 → `pytest` → 화면 시험·빌드 →
    묶음(태그면 로그인 설정 필수) → 묶음의 python.exe로 import·화면·토큰 없는 API 거부·Dawoni.exe 서명 확인 →
    artifact, 태그면 Release. 아직 GitHub에서 돌려 보지 않음.
  - 버전 0.8.0. Dawoni.exe 아이콘은 파이썬 아이콘이다 (아이콘을 바꾸면 서명이 깨진다).
- 서명 인증서를 사면(§8) PyInstaller exe로 돌아갈 수 있지만, 지금 방식은 돈이 들지 않고 이미 통과한다.

## 0. 결정 사항 (사용자 지시)

1. **exe는 git(GitHub Actions)으로 만든다** — 개발 PC에서 손으로 빌드하지 않는다. 태그를 올리면 빌드되고 Release에 exe가 붙는다.
2. **로그인은 Gist 명단에 있는 사람만** — 명단 한 줄 = `이메일, 강사 이름`.
3. **강사 이름은 스스로 정하지 않는다** — 로그인한 이메일에 맞는 Gist 명단의 이름을 그대로 쓴다. 처음 실행 마법사·설정 화면의 "강사 이름" 입력은 없앤다(배포본 기준).

## 1. 이미 있는 것 (다시 만들지 말 것)

- `dawoni/access.py` — Google 로그인(바깥 브라우저 → `http://127.0.0.1:<port>/auth/callback`) + 허용 명단.
  - `AccessConfig`: `google_client_id`, `google_client_secret`, `allowlist_url`(Gist raw URL), `allowed_emails`, `offline_days`.
  - `parse_allowlist()`: 한 줄 `이메일, 이름`(# 주석) 또는 JSON 목록 → `{email: name}`.
  - `Access._allow()`가 `self.name = allowed[email]`로 **명단의 이름을 이미 들고 있다**. `Access.status()`가 `"name"`을 돌려준다.
  - 켤 때마다 refresh token으로 이메일 재확인 + 명단 새로 받기. 오프라인이면 `offline_days`까지 받아 둔 명단으로 허용.
- 설정 파일 `dawoni/assets/access.json`은 `.gitignore`에 있다(공개 저장소). 예시: `packaging/access.example.json`.
  docstring에 "배포 빌드는 GitHub 비밀값 `DAWONI_ACCESS_JSON`으로 만든다"고 적혀 있다 → 워크플로에서 이 비밀값을 파일로 쓴다.
- `dawoni/server/app.py:60` — `LocalOnly` 미들웨어가 `gate.ok`가 아니면 API를 막는다.
- `dawoni/server/api_auth.py` — `/api/auth/status`, `/api/auth/login`, `/api/auth/logout`, `/auth/callback`.
- `pyproject.toml` dev 의존성에 `pyinstaller>=6.22.3`. macOS용 `pywebview`.
- `.gitignore`에 `/dawoni/web/`(프런트 빌드 결과를 패키지 안에 넣는 자리로 보임 — 확인할 것).

## 2. 강사 이름을 명단에서 가져오기 (코드 변경)

현재: 강사 이름 = `config.teacher`(`dawoni/config.py:148`). 처음 실행 마법사(`frontend/src/pages/Setup.tsx:25`)와 설정(`frontend/src/pages/Settings.tsx:45, 239`)에서 사람이 적고, `api_setup.py:60`이 `status.teacher`로 내려주고, 프런트 `useTeacher()`(`frontend/src/lib/session.tsx`)가 모든 요청의 `by`에 넣는다.

### 2-1. 서버
- `api_setup.py`의 상태 응답(60행 근처): `access`가 켜져 있고(`state.access is not None and state.access.required`) 로그인되어 있으면
  `"teacher": state.access.name or state.access.email`, 아니면 지금처럼 `cfg.teacher`(개발·직접 실행용).
  응답에 `"teacher_locked": True`도 넣어 프런트가 입력칸을 숨기게 한다.
- `api_setup.py:394~412`(설정 저장): 잠긴 상태면 `body.teacher`를 무시(저장 안 함).
- **`by`를 서버에서 덮어쓰기** — 프런트가 보낸 `by`를 믿지 않는다. 가장 간단한 방법:
  `AppState`에 `def who(self, by: str | None) -> str | None`을 두고 잠긴 상태면 `self.access.name or self.access.email`, 아니면 `by`.
  `by=body.by`를 쓰는 곳 전부(아래 grep) `by=st.who(body.by)`로 바꾼다:
  ```
  grep -rn "body.by" dawoni/server
  ```
  현재 위치: `api_calibration.py` 196·277·312, `api_content.py` 80·152·197, `api_exams.py` 300·313·373·421·432·496·593·599·664·672·849·949 등.
  `api_exams.py:994` `sharing.mark_in_use(..., body.teacher)`도 같은 식으로.
- 명단에 이름이 비어 있으면(`이메일`만 적힌 줄) 이메일을 그대로 쓴다. 로그 메시지로 "명단에 이름을 적어 주세요" 안내.

### 2-2. 프런트
- `types.ts`의 `Status`에 `teacher_locked?: boolean`.
- `Setup.tsx`: `teacher_locked`면 강사 이름 단계를 건너뛰고 "○○○ 선생님으로 로그인됨"만 보여 준다(입력 검사 25행 제거 조건부).
- `Settings.tsx` 239행 근처: 잠겼으면 읽기 전용 글자 + "이름은 관리자 명단에서 바뀝니다". 45행 검사도 조건부.
- 로그인 화면(로그인 안 됐을 때) 문구: "허용 명단에 없는 계정입니다 — 관리자에게 이메일을 알려 주세요"(서버 `AccessDenied` 메시지 확인).

### 2-3. 테스트
- `tests/`에 access 관련 테스트가 있으면 이어서: 잠긴 상태에서 `by="가짜"`로 루브릭 승인 → 기록에 명단 이름이 남는지.
- 잠기지 않은 상태(access.json 없음)는 지금 동작 그대로인지.

## 3. Gist 명단 운영

1. gist.github.com에서 **secret gist** 생성, 파일 이름 `allowlist.txt`:
   ```
   # 이메일, 강사 이름
   cherryopener@gmail.com, 개발자
   teacher1@gmail.com, 김다원
   ```
2. **Raw** 버튼 URL에서 커밋 해시 부분을 뺀 형태를 쓴다 — 고칠 때마다 최신을 받도록:
   `https://gist.githubusercontent.com/cherryopener/<gist-id>/raw/allowlist.txt`
   (해시가 들어간 URL은 그 버전에 고정되니 쓰지 말 것.) raw는 몇 분 캐시될 수 있다.
3. secret gist는 URL을 아는 사람은 볼 수 있다 → 명단에는 이메일·이름만. 비밀값 금지.
4. 사람 추가·삭제 = gist만 고치면 됨(다시 빌드 불필요). 빼면 그 사람은 다음 실행부터(또는 오프라인 유예 `offline_days` 뒤) 못 쓴다.

## 4. Google OAuth 클라이언트

1. Google Cloud Console → API 및 서비스 → OAuth 동의 화면(외부, 테스트 사용자 또는 게시) → 사용자 인증 정보 → **OAuth 클라이언트 ID, 유형 "데스크톱 앱"**.
   데스크톱 앱 유형은 `http://127.0.0.1:<임의 포트>` 리디렉트를 허용한다.
2. 범위: 지금 코드가 요청하는 scope를 `access.py`의 `start_login`에서 확인(이메일 + 시트용 drive 범위가 있음 — `_drive_granted`). 민감 범위면 동의 화면 검증 필요할 수 있음.
3. 클라이언트 ID·비밀번호로 `access.json` 작성(형식은 `packaging/access.example.json`).
   데스크톱 앱의 client secret은 사실상 공개 취급이지만 그래도 저장소에는 올리지 않는다.

## 5. GitHub Actions로 exe 만들기

### 5-1. 비밀값
저장소 Settings → Secrets and variables → Actions → New repository secret:
- `DAWONI_ACCESS_JSON` = access.json 내용 전체(JSON 그대로).

### 5-2. 워크플로 `.github/workflows/build-windows.yml` (초안 — 경로·명령은 확인 후 맞출 것)
```yaml
name: build-windows
on:
  push:
    tags: ["v*"]
  workflow_dispatch:
permissions:
  contents: write
jobs:
  build:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv python install 3.13
      - run: uv sync --frozen --all-groups
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: npm, cache-dependency-path: frontend/package-lock.json }
      - run: npm ci
        working-directory: frontend
      - run: npm run build          # 결과가 dawoni/web/ 로 가는지 vite.config 확인 (.gitignore에 /dawoni/web/)
        working-directory: frontend
      - name: write access.json
        shell: pwsh
        env: { ACCESS: "${{ secrets.DAWONI_ACCESS_JSON }}" }
        run: |
          if (-not $env:ACCESS) { throw "DAWONI_ACCESS_JSON 비밀값이 없습니다" }
          [IO.File]::WriteAllText("dawoni/assets/access.json", $env:ACCESS, (New-Object Text.UTF8Encoding $false))
      - run: uv run pyinstaller packaging/dawoni.spec --noconfirm
      - uses: actions/upload-artifact@v4
        with: { name: Dawoni-windows, path: dist/Dawoni* }
      - if: startsWith(github.ref, 'refs/tags/')
        uses: softprops/action-gh-release@v2
        with: { files: dist/Dawoni*.zip }
```
- 액션 버전은 그때 최신 안정판으로(위 숫자는 초안).
- `access.json`은 BOM 없이 써야 한다(`read_json`이 BOM에 걸릴 수 있음).
- 로그에 비밀값이 찍히지 않게 `echo` 금지.

### 5-3. ~~PyInstaller spec~~ (쓰지 않음 — 위 "진행" 참고, 아래는 처음 계획)
- 진입점: `dawoni/__main__.py`(앱 서버 + 창 띄우기 흐름 확인). Windows 창은 Edge 앱 모드인지 pywebview인지 `__main__`/`cli.py`에서 확인.
- `datas`: `dawoni/web`(프런트 빌드), `dawoni/assets`(selftest.png, access.json), 그 밖에 패키지 데이터(`grading`·`ocr` 등 안의 json/프롬프트 파일 — `git ls-files dawoni | grep -v '\.py$'`로 목록 확인).
- `--onedir` 권장(시작 빠름, 백신 오탐 적음) → `dist/Dawoni/` 를 zip. 단일 exe가 꼭 필요하면 `--onefile`.
- `console=False`, 아이콘 `packaging/dawoni.ico`(없으면 생략).
- 숨은 import: uvicorn(`uvicorn.logging`, `uvicorn.loops.auto`, `uvicorn.protocols.http.auto`, `uvicorn.lifespan.on`), pydantic 등. 첫 빌드 후 실행해 보고 빠진 모듈을 추가.
- `access.py`의 `ACCESS_FILE = Path(__file__).resolve().parent / "assets" / "access.json"` — onedir에서는 `_internal/dawoni/assets/`로 들어가므로 `datas`에 `("dawoni/assets", "dawoni/assets")`.
- 외부 도구(Claude/Codex/agy CLI, 한글)는 exe에 넣지 않는다 — 사용자 PC에 설치된 것을 연결 마법사가 찾는다.

### 5-4. 릴리스 절차
```bash
git tag v0.8.0
git push origin v0.8.0
```
→ Actions가 빌드 → Release에 zip. 수동 실행은 Actions 탭 → build → Run workflow(artifact로만 받음).

## 6. 완료 기준 (PLAN M8)
- [ ] 깨끗한 Windows PC(파이썬·node 없음)에서 zip 풀고 더블클릭 → 바깥 브라우저 Google 로그인 → 명단에 있으면 앱이 열림.
- [ ] 명단에 없는 계정 → 거절 문구, API 막힘.
- [ ] 화면 어디에도 강사 이름 입력칸 없음, 승인·확정 기록의 `by` = 명단 이름.
- [ ] gist에서 이름 바꾸고 앱 다시 켜면 새 이름.
- [ ] 인터넷 끊고 켜도 `offline_days` 안이면 열림.
- [ ] macOS .app: 서명·공증 없이 GitHub Actions에서 만든다 (2026-11-06 결정, 학원 안 소수 강사용) — `packaging/build_mac.py`, 워크플로 `macos` 작업. 받은 zip을 다른 맥에서 풀고 [그래도 열기]로 켜 보기.

## 8. 코드 서명 (하지 않음 — 기록만)

서명 없는 새 exe를 스마트 앱 컨트롤이 막는 문제는 코드 서명 인증서로도 풀린다 (Azure Trusted Signing은 월
약 10달러지만 개인 개발자는 미국·캐나다만, OV 인증서는 연 수십만 원 + 하드웨어 키). 지금 묶음은 PSF가 서명한
파일만 실행 파일로 쓰므로 필요 없다.

## 7. 순서 제안
1. §2 코드 변경 + 테스트 (로컬에서 `packaging/access.example.json`을 실제 값으로 복사해 확인)
2. §3·§4 gist·OAuth 준비 (사용자가 직접 — 콘솔·gist 작업)
3. §5-3 spec 작성, 로컬에서 `uv run pyinstaller packaging/dawoni.spec`로 먼저 성공시키기
4. §5-2 워크플로 추가, `workflow_dispatch`로 시험 → 태그 릴리스
