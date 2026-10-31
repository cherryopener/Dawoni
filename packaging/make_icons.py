"""학원 로고(`logo.png`)에서 앱 아이콘을 만든다: `uv run python packaging/make_icons.py`

로고 오른쪽 위의 ㄷㅇ 기호만 잘라, 흰 둥근 사각형 위에 올린다.
결과(icon.png·icon.icns·icon.ico, 화면용 기호·탭 아이콘)는 커밋한다
— CI가 빌드할 때 다시 만들지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
WEB_ASSETS = HERE.parent / "frontend" / "src" / "assets"
SIZE = 1024


def symbol(logo: Image.Image) -> Image.Image:
    """오른쪽 위 기호 영역을 잘라 흰 배경을 투명으로 (가장자리는 밝기에 따라 반투명)."""
    w, h = logo.size
    box = logo.crop((int(w * 0.6), 0, w, int(h * 0.55))).convert("RGBA")
    px = box.load()
    assert px is not None
    for y in range(box.height):
        for x in range(box.width):
            r, g, b, a = px[x, y]
            white = min(r, g, b)
            if white > 235 or b > r + 15:  # 배경, 또는 아래 '육' 글자(남색)와 그 가장자리
                px[x, y] = (r, g, b, 0)
            elif white > 200 and max(r, g, b) - white < 12:  # 흰 배경과 섞인 가장자리
                px[x, y] = (r, g, b, int(a * (255 - white) / 55))
    bbox = box.getchannel("A").point(lambda a: 255 if a > 128 else 0).getbbox()
    assert bbox is not None
    return box.crop(bbox)


def icon(sym: Image.Image) -> Image.Image:
    tile = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    # macOS 아이콘 격자: 1024 안에 824 둥근 사각형
    m = (SIZE - 824) // 2
    ImageDraw.Draw(tile).rounded_rectangle(
        (m, m, SIZE - m, SIZE - m), radius=185, fill=(255, 255, 255, 255)
    )
    inner = 640
    k = inner / max(sym.size)  # 로고의 기호는 약 260px → 키운다 (thumbnail은 줄이기만 함)
    s = sym.resize((round(sym.width * k), round(sym.height * k)), Image.Resampling.LANCZOS)
    tile.alpha_composite(s, ((SIZE - s.width) // 2, (SIZE - s.height) // 2))
    return tile


def main() -> None:
    sym = symbol(Image.open(HERE / "logo.png"))
    img = icon(sym)
    img.save(HERE / "icon.png")
    img.save(HERE / "icon.icns")
    img.save(HERE / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    WEB_ASSETS.mkdir(exist_ok=True)
    sym.save(WEB_ASSETS / "logo-mark.png")  # 사이드바 (투명 배경)
    img.resize((64, 64), Image.Resampling.LANCZOS).save(WEB_ASSETS / "favicon.png")


if __name__ == "__main__":
    main()
