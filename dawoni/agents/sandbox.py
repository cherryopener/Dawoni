"""AI CLI 호출마다 만드는 격리 작업 폴더.

- 첨부 파일은 `inputs/`에 ASCII 이름으로 복사한다 (원본 폴더·한글 파일명을 CLI에 노출하지 않음).
- 실행 기록(stdin 프롬프트, stdout 이벤트, stderr)을 남겨 실패 원인을 나중에 볼 수 있게 한다.
- 성공하면 지우고, 실패하면 남긴다 (기본값).
"""

from __future__ import annotations

import json
import secrets
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

_SHRINKABLE = {".png", ".jpg", ".jpeg", ".webp"}
JPEG_IF_BELOW = 0.7
"""JPEG가 PNG의 이만큼보다 작을 때만 JPEG로 (스캔 사진). 인쇄 글자 쪽은 PNG가 더 작다."""


def shrink_image(src: Path, dst_stem: Path, max_px: int) -> Path:
    """긴 변을 `max_px`로 줄이고, 더 작으면 JPEG(품질 90)로 저장한다. 결과 경로.

    토큰은 픽셀 수로 정해진다(파일 형식이 아님): claude는 긴 변 1568px로 줄여 보므로 그
    이하로 줄이는 것만 토큰이 줄고, gemini는 768px 타일 수로 센다. JPEG는 파일만 작아진다."""
    with Image.open(src) as im:
        im.load()
        if max(im.size) <= max_px and src.suffix.lower() in {".jpg", ".jpeg"}:
            dst = dst_stem.with_suffix(src.suffix.lower())
            shutil.copy2(src, dst)
            return dst
        x = im.convert("RGB") if im.mode not in ("RGB", "L") else im
        if max(x.size) > max_px:
            s = max_px / max(x.size)
            x = x.resize((round(x.width * s), round(x.height * s)), Image.Resampling.LANCZOS)
        png = dst_stem.with_suffix(".png")
        x.save(png, "PNG", optimize=True)
        jpg = dst_stem.with_suffix(".jpg")
        x.save(jpg, "JPEG", quality=90)
        if jpg.stat().st_size < JPEG_IF_BELOW * png.stat().st_size:
            png.unlink()
            return jpg
        jpg.unlink()
        return png


_SAFE = "abcdefghijklmnopqrstuvwxyz0123456789-_"


def _slug(s: str) -> str:
    out = "".join(c if c in _SAFE else "-" for c in s.lower())
    return out.strip("-")[:40] or "task"


@dataclass(frozen=True)
class Sandbox:
    root: Path

    @property
    def inputs(self) -> Path:
        return self.root / "inputs"

    @property
    def schema_path(self) -> Path:
        return self.root / "output.schema.json"

    @property
    def log_dir(self) -> Path:
        """CLI가 볼 필요 없는 기록. 작업 폴더 안에 두되 숨김 폴더로."""
        return self.root / ".dawoni"

    @classmethod
    def create(cls, base: Path, task: str) -> Sandbox:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        root = base / f"{stamp}-{_slug(task)}-{secrets.token_hex(3)}"
        root.mkdir(parents=True)
        sb = cls(root)
        sb.log_dir.mkdir()
        return sb

    def add_attachments(
        self, paths: tuple[Path, ...] | list[Path], *, max_image_px: int | None = None
    ) -> list[Path]:
        """첨부 파일을 복사하고 작업 폴더 기준 상대 경로 목록을 돌려준다.

        `max_image_px`: 그림은 긴 변을 이만큼으로 줄여(필요하면 JPEG로) 넣는다 (역할별)."""
        if not paths:
            return []
        self.inputs.mkdir(exist_ok=True)
        rels: list[Path] = []
        for i, src in enumerate(paths, start=1):
            if not src.is_file():
                raise FileNotFoundError(f"첨부 파일이 없습니다: {src}")
            stem = f"input_{i:02d}"
            if max_image_px and src.suffix.lower() in _SHRINKABLE:
                dst = shrink_image(src, self.inputs / stem, max_image_px)
                rels.append(Path("inputs") / dst.name)
                continue
            name = stem + src.suffix.lower()
            shutil.copy2(src, self.inputs / name)
            rels.append(Path("inputs") / name)
        return rels

    def write_schema(self, schema: dict[str, Any]) -> Path:
        self.schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        return self.schema_path

    def log_path(self, name: str) -> Path:
        return self.log_dir / name

    def remove(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def prune_sandboxes(base: Path, max_age_days: float = 7) -> int:
    """오래된(실패 기록용으로 남은) 격리 폴더 정리."""
    if not base.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for d in base.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        except OSError:
            pass
    return removed
