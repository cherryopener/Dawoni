# pyright: basic, reportMissingTypeStubs=false
"""답안 사진·스캔 PDF → AI에게 보낼 페이지 이미지.

- 사진: EXIF 회전 바로잡기, 긴 변 2400px로 축소, 약한 자동 대비. **이진화는 하지 않는다**
  원근 보정은 하지 않는다 — 답안은 복합기로 스캔한다 (OpenCV 116MB를 빼려고 2026-11-01 제거)
  (AI 비전은 원본에 가까운 이미지를 더 잘 읽고, 연필 자국·지운 흔적이 사라지지 않게).
- PDF: 페이지마다 200dpi로 렌더링.
- HEIC(아이폰): Pillow가 못 읽으므로 macOS에서는 내장 `sips`로 JPEG 변환.
- 원본은 그대로 보관한다 (강사가 판독문과 나란히 확인할 때 원본을 본다).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

MAX_SIDE = 2400
PDF_DPI = 200
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
HEIC_SUFFIXES = {".heic", ".heif"}


class PageError(Exception):
    pass


@dataclass(frozen=True)
class PreparedPage:
    number: int
    """1부터. 여러 파일이면 이어서 매긴다."""
    original: Path
    image: Path
    """AI에게 보낼 PNG."""


def _heic_to_jpeg(src: Path, dst: Path) -> Path:
    if sys.platform != "darwin" or shutil.which("sips") is None:
        raise PageError(
            f"HEIC 사진은 이 PC에서 바로 읽을 수 없습니다: {src.name}. "
            "사진 앱에서 JPEG로 내보내 주세요."
        )
    r = subprocess.run(
        ["sips", "-s", "format", "jpeg", str(src), "--out", str(dst)],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0 or not dst.exists():
        raise PageError(f"HEIC 변환 실패: {src.name} ({r.stderr.strip()[:200]})")
    return dst


def prepare_image(src: Path, dst: Path, max_side: int = MAX_SIDE) -> Path:
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            if max(im.size) > max_side:
                im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            im = ImageOps.autocontrast(im, cutoff=1)
            im.save(dst, "PNG", optimize=True)
    except OSError as e:
        raise PageError(f"이미지를 읽을 수 없습니다: {src.name} ({e})") from e
    return dst


def render_pdf(src: Path, out_dir: Path, start: int) -> list[Path]:
    try:
        doc = pymupdf.open(src)
    except (RuntimeError, ValueError) as e:
        raise PageError(f"PDF를 열 수 없습니다: {src.name} ({e})") from e
    paths: list[Path] = []
    with doc:
        for i in range(doc.page_count):
            pix = doc.load_page(i).get_pixmap(dpi=PDF_DPI)
            p = out_dir / f"page_{start + i:02d}.png"
            pix.save(str(p))
            paths.append(p)
    return paths


def prepare_pages(sources: list[Path], out_dir: Path) -> list[PreparedPage]:
    """파일들을 페이지 이미지로. 원본은 out_dir/original_NN.<확장자>로 복사."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[PreparedPage] = []
    n = 1
    for k, src in enumerate(sources, start=1):
        if not src.is_file():
            raise PageError(f"파일이 없습니다: {src}")
        suffix = src.suffix.lower()
        original = out_dir / f"original_{k:02d}{suffix}"
        shutil.copy2(src, original)
        if suffix == ".pdf":
            for img in render_pdf(src, out_dir, n):
                pages.append(PreparedPage(n, original, img))
                n += 1
            continue
        readable = src
        if suffix in HEIC_SUFFIXES:
            readable = _heic_to_jpeg(src, out_dir / f"converted_{k:02d}.jpg")
        elif suffix not in IMAGE_SUFFIXES:
            raise PageError(f"지원하지 않는 형식입니다: {src.name}")
        img = prepare_image(readable, out_dir / f"page_{n:02d}.png")
        pages.append(PreparedPage(n, original, img))
        n += 1
    if not pages:
        raise PageError("페이지가 없습니다.")
    return pages
