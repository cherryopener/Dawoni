# HWP 읽기와 한글 자동화 — 알려진 문제

다워니는 HWPX(zip + XML)를 한글 없이 직접 읽는다. **HWP(바이너리)** 는 2026-11-10부터 먼저 python-hwpx로
한글 없이 HWPX로 바꾼다 (맥에서도, 창 없이). python-hwpx가 실패하거나 수식·표 등을 다 옮기지 못했다고 할 때만
그 PC에 설치된 한글을 COM 자동화(`HWPFrame.HwpObject`)로 띄워 HWPX로 저장한 뒤 읽는다
([`dawoni/ingest/convert.py`](../dawoni/ingest/convert.py)). 아래 문제는 그 한글 경로에서만 생긴다.

## 1. 한글이 깔려 있는데 "한글이 설치되어 있지 않습니다" (2026-11-05)

**현상**: 다른 강사 PC(한글 2024 설치됨)에서 HWP 시험지를 넣으면 거절됐다. 로그(`%APPDATA%\Dawoni\logs`):

```
pywintypes.com_error: (-2147221005, '잘못된 클래스 문자열입니다.', None, None)
```

**원인**: `-2147221005`(`0x800401F3`, `CO_E_CLASSSTRING`) — Windows에 `HWPFrame.HwpObject`가 등록되어
있지 않다. 한글 프로그램은 실행되지만 **자동화 등록만 빠진 설치**였다. 그 PC에서
`reg query HKCR\HWPFrame.HwpObject`가 "지정한 레지스트리 키를 찾을 수 없습니다"였다. (정상인 개발 PC:
한컴오피스 2024 Edu 13.0.0.304, `HKLM\SOFTWARE\Classes\HWPFrame.HwpObject`에 PC 전체로 등록.)
보안 모듈(개발자 도구)과는 상관없다 — 그것은 한글이 뜬 뒤 파일을 열 때 쓰인다.

**해결**: Windows 설정 → 앱 → 설치된 앱 → 한컴오피스 → 수정(복구), 관리자 권한으로. 복구한 뒤
`reg query HKCR\HWPFrame.HwpObject`가 `HwpObject Class`를 보여 주면 된다. 복구로도 등록되지 않으면 그
판에 자동화가 없는 것 — 한컴 고객지원에 "HwpAutomation(HWPFrame.HwpObject) 등록"을 문의하거나,
HWPX·PDF로 저장해 넣는다.

**앱에서 고친 것** (2026-11-06): 자동화 객체를 만들다 실패하면 이유를 나눠 알린다.

- 등록 없음(`CO_E_CLASSSTRING`, `REGDB_E_CLASSNOTREG`) → "이 PC의 한글에 자동화 등록이 없습니다 … 복구 설치
  … 아니면 HWPX(또는 PDF)로 저장해 넣어 주세요"
- 그 밖의 실패 → "한글을 띄우지 못했습니다 (원래 오류)"

예전에는 모두 "한글이 설치되어 있지 않습니다"였다. 화면은 한때(9c283f5) HWP·HWPX를 아예 받지 않았으나
다시 받는다 — HWPX는 한글 없이 읽고, HWP는 안 되면 위 안내가 뜬다.

## 2. 보안 모듈을 넣었는데도 매번 '접근 허용' 창 (2026-11-06)

**현상**: 한컴 자동화 보안 모듈(`FilePathCheckerModuleExample.dll`)을 설치하고 레지스트리에 등록했는데도,
한글이 파일을 열 때마다 [모두 허용]을 눌러야 했다.

**원인**: `RegisterModule("FilePathCheckDLL", 이름)`의 이름은 레지스트리
`HKCU\Software\HNC\HwpAutomation\Modules`에 적은 **값 이름**이어야 한다. 한컴 예제대로 등록하면 이름이
`FilePathCheckerModuleExample`인데, 코드는 `FilePathCheckerModule`로 불러 늘 `False`(실패)였다.

**고친 것**: 레지스트리의 값 이름을 읽어 차례로 붙이고(`convert.register_security_module`), 못 읽으면
`FilePathCheckerModuleExample` → `FilePathCheckerModule` 순으로 시도한다. `experiments/hwpx_probe.py`도
같은 함수를 쓴다. 개발 PC 실측: `FilePathCheckerModuleExample`로 붙고, `real_data/docs`의 HWP가 창 없이
1.5초에 HWPX로 바뀜.

## 강사 PC 확인 순서

1. HWPX로 저장할 수 있으면 HWPX로 넣는다 (한글 상태와 상관없음).
2. HWP를 넣어 "자동화 등록이 없습니다"가 뜨면 → 한컴오피스 복구 설치 (위 1).
3. HWP가 열릴 때마다 '접근 허용' 창이 뜨면 → 창에서 [모두 허용]을 누르거나, 보안 모듈을 설치하고
   `HKCU\Software\HNC\HwpAutomation\Modules`에 등록 (위 2).
