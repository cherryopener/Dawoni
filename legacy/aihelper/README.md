# AIHelper (학원 강사용 AI 도우미)

> **학원 강사를 위한 수학·과학 특화 AI 작업 파트너**  
> 선생님이 이미 구독 중인 AI(Gemini, Claude, ChatGPT)의 공식 CLI를 연결하여 API 추가 비용 없이 교재 DB화, 서술형 채점, 해설 작성, 동형문제 생성을 지원합니다.

---

## 🌟 주요 특징

1. **내 구독 계정 그대로 활용 (API 비용 0원)**
   - 별도 API 키를 충전할 필요 없이, 선생님이 이미 구독 중인 계정(Google Gemini, Claude Pro/Max, ChatGPT Plus)의 공식 CLI 도구를 연결합니다.
2. **환각 및 지침 망각 방지 (무상태 컨텍스트 엔진)**
   - 대화가 길어질수록 AI가 이전 지침을 잊거나 엉뚱한 답변을 내놓는 현상을 방지하기 위해, 매 작업마다 독립된 세션으로 필요한 단원 개념·루브릭·지침만 골라 프롬프트를 전송합니다.
3. **수학·과학 특화**
   - LaTeX 수식을 Word 네이티브 수식(OMML)으로 완벽 변환하여 편집 가능한 DOCX로 출력합니다.
   - 문제 풀이 및 정답 검증 시 `sympy` 수식 검증을 거치며, 그래프 및 도식 생성을 지원합니다.
4. **개인 및 팀(Google Drive) 작업공간 지원**
   - 강사 혼자 사용할 때는 로컬 폴더로, 팀 단위로 협업할 때는 Google Drive 데스크톱 동기화 폴더를 지정하여 문항 DB, 해설, 학생 답안 및 성적을 안전하게 공유할 수 있습니다.
5. **크로스 플랫폼 지원 (macOS & Windows)**
   - 맥과 윈도우 모두에서 데스크톱 창(또는 브라우저)으로 편리하게 실행됩니다.

---

## 📋 시스템 요구사항

- **운영체제**: macOS (Apple Silicon / Intel) 또는 Windows 10/11
- **Python**: 3.11 이상
- **Node.js**: LTS 20 이상 권장 (Gemini CLI, Codex CLI 설치 시 필요)
- **구독 서비스**: Google 계정 (무료/Pro), Claude Pro/Max, ChatGPT Plus 중 1개 이상

---

## 🚀 빠른 설치

### macOS 사용자
터미널을 열고 다음 한 줄 명령어를 실행하세요:

```bash
git clone https://github.com/cherryopener/AIHelper.git && cd AIHelper && ./scripts/install_mac.sh
```

### Windows 사용자
PowerShell을 열고 다음 명령어를 실행하세요:

```powershell
git clone https://github.com/cherryopener/AIHelper.git; cd AIHelper; .\scripts\install_windows.ps1
```

### 수동 설치 (공통)

```bash
# 1. 저장소 복제 및 이동
git clone https://github.com/cherryopener/AIHelper.git
cd AIHelper

# 2. 가상환경 생성 및 활성화
python3 -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows:
.\.venv\Scripts\activate

# 3. 패키지 설치
pip install -e .
```

---

## 🖥️ 프로그램 실행

설치 후 프로젝트 폴더에서 아래 명령어 중 하나로 실행할 수 있습니다:

```bash
# macOS/Linux
./run.sh

# Windows
.\run.bat

# 또는 파이썬 직접 실행
python -m aihelper
```

> **참고**: 기본적으로 깔끔한 데스크톱 전용 창(`pywebview`)으로 실행됩니다. 크롬/사파리 등의 웹 브라우저 탭으로 열고 싶으시다면 `--browser` 옵션을 붙여 실행하세요:
> ```bash
> python -m aihelper --browser
> ```

---

## 🔑 AI 계정 연결 방법

프로그램을 처음 실행하면 **AI 연결 마법사**가 나타나며, 화면의 안내에 따라 3단계로 연결할 수 있습니다.

### 1. Google Gemini CLI
1. 앱에서 **[설치]** 클릭 → 터미널에서 `npm install -g @google/gemini-cli@latest` 실행
2. **[로그인]** 클릭 → 열린 터미널 창에서 Google 계정 브라우저 로그인 진행
3. 완료 후 앱에서 **[테스트]** 클릭 → 연결 완료 확인

### 2. Anthropic Claude Code
1. 앱에서 **[설치]** 클릭 → 공식 설치 스크립트 자동 실행
2. **[로그인]** 클릭 → 터미널에서 `/login` 입력 후 Claude Pro/Max 계정 로그인
3. 완료 후 앱에서 **[테스트]** 클릭 → 연결 완료 확인

### 3. OpenAI Codex CLI
1. 앱에서 **[설치]** 클릭 → `npm install -g @openai/codex@latest` 실행
2. **[로그인]** 클릭 → 터미널에서 `codex login` 실행 후 ChatGPT Plus 계정 로그인
3. 완료 후 앱에서 **[테스트]** 클릭 → 연결 완료 확인

---

## 📁 작업공간 구조 안내

지정한 작업공간(로컬 폴더 또는 Google Drive 공유 폴더)에는 다음과 같은 하위 폴더가 자동으로 구성됩니다:

```text
작업공간/
├── sources/               # 원본 자료 (PDF, HWP, DOCX, 문제 이미지)
├── bank/                  # 단원별로 정리된 문항 DB (JSON, 수식, 잘라낸 그림)
├── concepts/              # 단원별 개념 요약 카드
├── rubrics/               # 서술형 채점 기준표
├── students/              # 학생 관리
│   ├── submissions/       # 제출된 학생 답안지 (스캔/사진)
│   └── results/           # 채점 결과 및 성적표
├── templates/             # 학원 전용 DOCX 양식 (로고, 폰트, 여백)
├── exports/               # 최종 생성된 DOCX (해설지, 동형 문제지)
└── workspace.json         # 작업공간 설정 메타데이터
```

---

## ❓ 자주 묻는 질문 (FAQ) & 문제 해결

### Q1. "확인되지 않은 개발자" 또는 SmartScreen 경고가 뜹니다.
- **원인**: 본 소프트웨어는 오픈소스 프로젝트로 고가의 유료 코드 서명을 적용하지 않았습니다.
- **macOS 조치**: `시스템 설정` → `개인정보 보호 및 보안` → 하단의 `그래도 열기`를 클릭하세요.
- **Windows 조치**: 푸른색 PC 보호 창에서 `추가 정보` 클릭 → `실행` 버튼을 클릭하세요.

### Q2. Node.js가 설치되어 있지 않다고 경고가 뜹니다.
- Gemini CLI와 Codex CLI는 Node.js 환경이 필요합니다.
- [Node.js 공식 홈페이지(LTS 버전)](https://nodejs.org/)에서 설치하거나, 맥은 `brew install node`, 윈도우는 `winget install OpenJS.NodeJS.LTS`로 설치할 수 있습니다.

### Q3. AI 사용량 한도(Rate Limit / Quota)에 걸렸습니다.
- 구독형 서비스는 시간당 질문 횟수 제한이 있을 수 있습니다.
- AIHelper는 여러 AI 서비스를 등록해 둘 수 있으므로, 한도가 걸렸을 때 다른 AI(예: Gemini ↔ Claude)로 전환하여 작업을 이어갈 수 있습니다.

---

## 📦 독립 실행 파일(exe / app) 직접 빌드

동료 강사들에게 파이썬 설치 없이 바로 더블클릭할 수 있는 실행 파일(.app / .exe)을 배포하려면:

### macOS
```bash
./scripts/build_executable.sh
# -> dist/AIHelper.app 생성
```

### Windows
```cmd
scripts\build_executable.bat
rem -> dist\AIHelper\AIHelper.exe 생성
```

또한 GitHub 저장소의 **Releases** 탭에서 플랫폼별 빌드 완료된 zip 압축파일을 바로 다운로드할 수도 있습니다.

---

## 📜 라이선스

MIT License
