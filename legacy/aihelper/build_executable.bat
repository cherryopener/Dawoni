@echo off
rem ==============================================================================
rem AIHelper Windows 원클릭 실행 파일 (.exe) 빌드 스크립트
rem ==============================================================================
setlocal enabledelayedexpansion

cd /d "%~dp0\.."

echo ==> [1/3] React 프론트엔드 최신 번들 빌드...
if exist "frontend" (
    cd frontend
    call npm install
    call npm run build
    cd ..
)

echo ==> [2/3] PyInstaller 설치 확인...
if exist ".venv\Scripts\pip.exe" (
    .venv\Scripts\pip.exe install pyinstaller
    set PYINSTALLER=.venv\Scripts\pyinstaller.exe
) else (
    pip install pyinstaller
    set PYINSTALLER=pyinstaller
)

%PYINSTALLER% AIHelper.spec --clean --noconfirm

(
    echo @echo off
    echo chcp 65001 ^> nul
    echo cd /d "%%~dp0"
    echo ==============================================
    echo [AIHelper] 프로그램을 시작합니다...
    echo ==============================================
    echo AIHelper.exe %%*
    echo if %%ERRORLEVEL%% NEQ 0 (
    echo     echo.
    echo     echo [안내] 프로그램이 종료되었습니다 ^(코드: %%ERRORLEVEL%%^).
    echo     pause
    echo ^)
) > dist\AIHelper\AIHelper_실행.bat

echo ==============================================================================
echo [성공] 빌드가 완료되었습니다!
echo 산출물 위치: dist\AIHelper\AIHelper.exe
echo ==============================================================================
pause
