"""묶은 앱(PyInstaller)의 시작점.

인자 없이 더블클릭하면 화면을 열고, 인자가 있으면 `dawoni` 명령줄과 같다
(Windows 콘솔에서 `Dawoni.exe agent detect` 처럼 문제를 확인할 수 있게).
"""

import os
import sys

from dawoni.paths import AppPaths

# PyInstaller의 matplotlib 훅은 설정 폴더를 실행마다 새 임시 폴더로 바꿔
# 켤 때마다 폰트 캐시를 다시 만든다(수 초) → 앱 데이터 폴더에 고정
_mpl = AppPaths.default().cache / "matplotlib"
_mpl.mkdir(parents=True, exist_ok=True)
os.environ["MPLCONFIGDIR"] = str(_mpl)

from dawoni.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["ui"]))
