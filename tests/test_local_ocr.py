# pyright: basic, reportMissingTypeStubs=false
"""로컬 OCR 글자 층 (RapidOCR 대신 가짜 엔진 — 설치 없이)."""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw

from dawoni.ingest.layout import analyze
from dawoni.ocr import local


def _scanned_pdf(path: Path) -> Path:
    im = Image.new("RGB", (1654, 2339), "white")
    ImageDraw.Draw(im).text((200, 300), "1. Find the speed", fill="black")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(str(path))
    return path


def _fake(image: Path) -> list[tuple[local.Box, str]]:
    assert image.exists()
    return [((200, 300, 900, 360), "1. 물체의 속력을 구하시오. (단, 중력 가속도는 g이다.)")] * 3


def test_text_layer_makes_scanned_pdf_readable(tmp_path: Path) -> None:
    pdf = _scanned_pdf(tmp_path / "scan.pdf")
    assert analyze(pdf).scanned
    out = local.add_text_layer(pdf, tmp_path / "scan.ocr.pdf", _fake, tmp_path / "w")
    with pymupdf.open(out) as doc:
        assert "속력을 구하시오" in doc.load_page(0).get_text()
    assert not analyze(out).scanned


def test_without_rapidocr_scanned_stays_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = _scanned_pdf(tmp_path / "scan.pdf")
    monkeypatch.setattr(local, "rapidocr_engine", lambda: None)
    assert local.analyze_scanned(pdf, analyze(pdf), tmp_path / "w") is None
    monkeypatch.setattr(local, "rapidocr_engine", lambda: _fake)
    got = local.analyze_scanned(pdf, analyze(pdf), tmp_path / "w")
    assert got is not None and not got.scanned
    assert any("로컬 OCR" in w for w in got.warnings)
