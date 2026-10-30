# pyright: basic, reportMissingTypeStubs=false
"""무료 로컬 OCR (RapidOCR, 선택 설치)로 스캔 PDF에 보이지 않는 글자 층을 입힌다.

글자 층이 생기면 디지털 PDF와 같은 길(레이아웃 분석 → 문항 영역 → AI 구조화)을 탄다.
학생 답안이 아니라 인쇄된 시험지·교재용이며, 사진은 PC 밖으로 나가지 않는다.

설치: `uv sync --extra local-ocr` (onnxruntime이 아직 Python 3.15용 빌드가 없어 3.14에서만).
설치되어 있지 않으면 `rapidocr_engine()`이 None — 스캔 PDF는 예전처럼 거절된다.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

import pymupdf

from dawoni.ingest.layout import DocumentLayout, analyze

log = logging.getLogger(__name__)

Box = tuple[float, float, float, float]
OcrEngine = Callable[[Path], list[tuple[Box, str]]]
"""그림 한 장 → (픽셀 좌표 상자, 글자) 목록."""

OCR_DPI = 200
INSTALL_HINT = "무료 로컬 OCR을 설치하면 읽을 수 있습니다: uv sync --extra local-ocr (Python 3.14)"


def rapidocr_engine() -> OcrEngine | None:
    try:
        mod: Any = importlib.import_module("rapidocr")
    except ImportError:
        return None
    engine: Any = mod.RapidOCR()

    def run(image: Path) -> list[tuple[Box, str]]:
        out: Any = engine(str(image))
        boxes: Any = getattr(out, "boxes", None)
        texts: Any = getattr(out, "txts", None)
        if boxes is None or texts is None:
            return []
        lines: list[tuple[Box, str]] = []
        for box, text in zip(boxes, texts, strict=False):
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            lines.append(((min(xs), min(ys), max(xs), max(ys)), str(text)))
        return lines

    return run


def add_text_layer(pdf: Path, out: Path, engine: OcrEngine, work: Path) -> Path:
    """쪽마다 그림으로 렌더링해 OCR하고, 그 자리에 보이지 않는 글자(render_mode 3)를 넣은 사본."""
    work.mkdir(parents=True, exist_ok=True)
    scale = 72 / OCR_DPI
    doc = pymupdf.open(pdf)
    with doc:
        for i in range(doc.page_count):
            page: pymupdf.Page = doc.load_page(i)
            img = work / f"ocr_{i + 1:03d}.png"
            page.get_pixmap(dpi=OCR_DPI).save(str(img))
            for (x0, y0, _x1, y1), text in engine(img):
                height = (y1 - y0) * scale
                if not text.strip() or height <= 0:
                    continue
                page.insert_text(
                    (page.rect.x0 + x0 * scale, page.rect.y0 + y1 * scale - height * 0.2),
                    text,
                    fontsize=max(4.0, height * 0.8),
                    fontname="korea",
                    render_mode=3,
                )
        doc.save(str(out))
    log.info("스캔 PDF에 로컬 OCR 글자 층: %s", out)
    return out


def analyze_scanned(
    path: Path, layout: DocumentLayout, work: Path, *, exclude: Collection[int] = ()
) -> DocumentLayout | None:
    """스캔본이면 로컬 OCR 글자 층을 입혀 다시 분석한다. OCR이 없거나 글자를 못 찾으면 None."""
    if not layout.scanned:
        return layout
    engine = rapidocr_engine()
    if engine is None:
        return None
    copy = add_text_layer(path, work / f"{path.stem}.ocr.pdf", engine, work / "ocr_pages")
    again = analyze(copy, exclude=exclude)
    if again.scanned:
        return None
    again.warnings.append("스캔 PDF — 무료 로컬 OCR(RapidOCR)로 글자 층을 만들어 읽었습니다")
    return again
