"""Dawoni(다워니) — AI CLI 징검다리 학원 강사 도구."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dawoni")
except PackageNotFoundError:  # 소스 트리에서 설치 없이 실행할 때
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
