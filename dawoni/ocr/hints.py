"""선택 기능: 외부 OCR(CLOVA, Mathpix)의 결과를 판독 프롬프트에 **참고 자료**로 넣는다.

- 강사가 키를 넣고 켰을 때만 동작한다 (학생 답안이 외부 서비스로 전송되므로).
- 결과는 판독문이 아니다. AI가 이미지를 우선하고, 이 결과는 애매한 글자를 판단할 때만 참고한다.
- 이 PC에는 키가 없어 **실제 서비스로는 검증하지 못했다**. 요청 형식은 각 서비스 공개 문서 기준.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

from dawoni.config import OcrSettings

Opener = Callable[[urllib.request.Request, float], bytes]


def _default_opener(req: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return cast(bytes, resp.read())


class HintError(Exception):
    pass


class HintProvider(Protocol):
    name: str

    def read(self, image: Path) -> str: ...


class ClovaOcr:
    """네이버 CLOVA OCR (General, V2)."""

    name = "CLOVA OCR"

    def __init__(self, invoke_url: str, secret: str, opener: Opener = _default_opener) -> None:
        self.url, self.secret, self.opener = invoke_url, secret, opener

    def read(self, image: Path) -> str:
        body = {
            "version": "V2",
            "requestId": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "images": [
                {
                    "format": image.suffix.lstrip(".").lower() or "png",
                    "name": image.stem,
                    "data": base64.b64encode(image.read_bytes()).decode(),
                }
            ],
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-OCR-SECRET": self.secret},
            method="POST",
        )
        data = cast(dict[str, Any], json.loads(self.opener(req, 60)))
        images = cast(list[dict[str, Any]], data.get("images") or [])
        if not images:
            raise HintError("CLOVA 응답에 결과가 없음")
        out: list[str] = []
        for f in cast(list[dict[str, Any]], images[0].get("fields") or []):
            out.append(str(f.get("inferText", "")))
            out.append("\n" if f.get("lineBreak") else " ")
        return "".join(out).strip()


class MathpixOcr:
    """Mathpix (v3/text) — 손글씨 수식에 강함."""

    name = "Mathpix"

    def __init__(self, app_id: str, app_key: str, opener: Opener = _default_opener) -> None:
        self.app_id, self.app_key, self.opener = app_id, app_key, opener

    def read(self, image: Path) -> str:
        mime = "image/png" if image.suffix.lower() == ".png" else "image/jpeg"
        src = f"data:{mime};base64,{base64.b64encode(image.read_bytes()).decode()}"
        req = urllib.request.Request(
            "https://api.mathpix.com/v3/text",
            data=json.dumps({"src": src, "formats": ["text"]}).encode(),
            headers={
                "Content-Type": "application/json",
                "app_id": self.app_id,
                "app_key": self.app_key,
            },
            method="POST",
        )
        data = cast(dict[str, Any], json.loads(self.opener(req, 60)))
        if data.get("error"):
            raise HintError(f"Mathpix 오류: {data['error']}")
        return str(data.get("text", "")).strip()


def providers_from(settings: OcrSettings) -> list[HintProvider]:
    found: list[HintProvider] = []
    if settings.clova_enabled and settings.clova_invoke_url and settings.clova_secret:
        found.append(ClovaOcr(settings.clova_invoke_url, settings.clova_secret))
    if settings.mathpix_enabled and settings.mathpix_app_id and settings.mathpix_app_key:
        found.append(MathpixOcr(settings.mathpix_app_id, settings.mathpix_app_key))
    return found


def collect_hints(providers: list[HintProvider], pages: list[Path]) -> tuple[str | None, list[str]]:
    """(프롬프트에 넣을 참고 텍스트, 경고 목록). 외부 OCR 실패는 판독을 막지 않는다."""
    if not providers:
        return None, []
    blocks: list[str] = []
    warnings: list[str] = []
    for p in providers:
        for n, page in enumerate(pages, start=1):
            try:
                text = p.read(page)
            except (HintError, OSError, ValueError) as e:
                warnings.append(f"{p.name} {n}쪽 실패: {e}")
                continue
            if text:
                blocks.append(f"<{p.name} · {n}쪽>\n{text}")
    if not blocks:
        return None, warnings
    head = (
        "[참고용 외부 OCR 결과 — 틀릴 수 있다. 판독은 이미지를 우선하고, "
        "애매한 글자를 판단할 때만 참고할 것. 이 결과를 그대로 옮기지 말 것]"
    )
    return head + "\n" + "\n\n".join(blocks), warnings
