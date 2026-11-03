# pyright: basic, reportMissingTypeStubs=false
"""테스트용 PDF 생성기. 정답(문항 영역·그림 귀속)을 알고 있는 시험지·교재를 만든다.

시험지 (A4, 2단, 2쪽) — AIHelper에서 터졌던 함정을 일부러 넣었다:
- 1쪽 머리말 배너(색 채운 사각형 + "수학 영역") → 그림으로 뽑히면 안 됨
- 1번: 글 + 선지 ①~⑤ (그림 없음)
- 2번: 벡터 그림(삼각형 + 원)
- 3번: <보기> 테두리 상자 (그림이 아니라 글 상자)
- 4번(오른쪽 단): 선지 ①~⑤가 각각 작은 그래프 그림
- 5번: 오른쪽 단 아래에서 시작해 2쪽 왼쪽 단 위로 이어짐 (이어진 부분에 그림)
- 2쪽 [서술형 1] [6점]: 래스터 이미지 포함
- 꼬리말 쪽 번호
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

W, H = 595, 842
LX, RX = 50, 315  # 왼쪽·오른쪽 단 시작 x
COL_W = 230
FONT = "korea"


def _t(
    page: pymupdf.Page, x: float, y: float, s: str, size: float = 10, width: float | None = None
) -> None:
    """글자 크기를 줄여서라도 단 너비 안에 넣는다 (단을 넘는 글은 레이아웃 시험을 망침)."""
    limit = width if width is not None else (COL_W if x < 297 or x >= RX else W - x - 40)
    if x < 297:
        limit = min(limit, 290 - x)
    while size > 5 and pymupdf.get_text_length(s, fontname=FONT, fontsize=size) > limit:
        size -= 0.5
    page.insert_text((x, y), s, fontname=FONT, fontsize=size)


def _graph(page: pymupdf.Page, x: float, y: float, kind: int) -> None:
    """선지용 작은 그래프 (45×45)."""
    page.draw_line((x, y + 40), (x + 45, y + 40))  # x축
    page.draw_line((x + 5, y), (x + 5, y + 45))  # y축
    pts = []
    for i in range(10):
        t = i / 9
        yy = {
            0: 40 - 35 * t,
            1: 5 + 35 * t,
            2: 40 - 35 * t * t,
            3: 20,
            4: 40 - 35 * (1 - (1 - t) ** 2),
        }[kind]
        pts.append(pymupdf.Point(x + 5 + 38 * t, y + yy))
    page.draw_polyline(pts)


def make_exam_pdf(path: Path) -> Path:
    doc = pymupdf.open()

    # ---------------- 1쪽
    p = doc.new_page(width=W, height=H)
    p.draw_rect(pymupdf.Rect(40, 30, 555, 72), color=(0, 0, 0), fill=(0.85, 0.85, 0.9))
    _t(p, 170, 58, "2027학년도 모의평가  수학 영역", 16, width=300)
    p.draw_line((297, 90), (297, 800))  # 단 구분선

    _t(p, LX, 110, "1. 이차방정식 x² - 5x + 6 = 0 의 두 근의 합은?")
    _t(p, LX + 10, 130, "① 1   ② 3   ③ 5   ④ 6   ⑤ 7")

    _t(p, LX, 175, "2. 그림과 같이 삼각형 ABC와 원이 있다.")
    _t(p, LX + 10, 192, "원의 반지름의 길이를 구하시오. [3점]")
    p.draw_polyline([(80, 300), (200, 300), (140, 210), (80, 300)])
    p.draw_circle((140, 268), 25)
    _t(p, 70, 312, "B", 9)
    _t(p, 202, 312, "C", 9)
    _t(p, 137, 205, "A", 9)

    _t(p, LX, 360, "3. <보기>에서 옳은 것만을 있는 대로 고른 것은?")
    p.draw_rect(pymupdf.Rect(60, 372, 270, 450), color=(0, 0, 0))
    _t(p, 140, 386, "<보 기>", 9)
    _t(p, 70, 405, "ㄱ. 두 근의 곱은 6이다.")
    _t(p, 70, 423, "ㄴ. 두 근은 모두 양수이다.")
    _t(p, 70, 441, "ㄷ. 판별식의 값은 음수이다.")
    _t(p, LX + 10, 470, "① ㄱ   ② ㄴ   ③ ㄱ, ㄴ   ④ ㄴ, ㄷ   ⑤ ㄱ, ㄴ, ㄷ")

    _t(p, RX, 110, "4. 함수 y = x 의 그래프로 옳은 것은?")
    for k, (gx, gy) in enumerate([(330, 140), (410, 140), (490, 140), (330, 220), (410, 220)]):
        _t(p, gx - 14, gy + 10, "①②③④⑤"[k], 10)
        _graph(p, gx, gy, k)

    _t(p, RX, 700, "5. 다음은 직선 l 위의 점 P에 대한 설명이다.")
    _t(p, RX, 718, "점 P의 좌표를 (a, b)라 할 때, a + b의 값은?")
    _t(p, 290, 825, "1", 9)

    # ---------------- 2쪽
    p = doc.new_page(width=W, height=H)
    p.draw_line((297, 50), (297, 800))
    _t(p, LX, 70, "(단, 점 P는 제1사분면 위에 있다.)")
    p.draw_line((70, 160), (230, 90))  # 직선 l
    p.draw_circle((150, 125), 3, fill=(0, 0, 0))
    _t(p, 155, 120, "P", 9)
    _t(p, 232, 92, "l", 9)
    _t(p, LX + 10, 190, "① 1   ② 2   ③ 3   ④ 4   ⑤ 5")

    _t(p, LX, 240, "[서술형 1] 다음 그림을 보고 물음에 답하시오. [6점]")
    img = Image.new("RGB", (200, 120), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((10, 10, 190, 110), outline="black", width=3)
    d.ellipse((60, 30, 140, 90), outline="blue", width=3)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    p.insert_image(pymupdf.Rect(70, 255, 230, 351), stream=buf.getvalue())
    _t(p, LX, 375, "(1) 그림의 원의 넓이를 구하는 과정을 서술하시오.")
    _t(p, 290, 825, "2", 9)

    doc.save(str(path))
    return path


def make_textbook_pdf(path: Path) -> Path:
    """교재 1단원 2쪽: 개념 설명 + 예제 풀이 (1단)."""
    doc = pymupdf.open()
    p = doc.new_page(width=W, height=H)
    _t(p, 50, 60, "Ⅲ. 이차함수의 최대·최소", 15, width=480)
    _t(p, 50, 100, "개념 1  이차함수의 표준형", 12, width=480)
    _t(p, 50, 122, "이차함수 y = a(x - p)² + q 의 그래프의 꼭짓점은 (p, q)이다.", 10, width=480)
    _t(p, 50, 160, "개념 2  완전제곱식으로 고치기", 12, width=480)
    _t(p, 50, 182, "y = x² + bx + c 는 y = (x + b/2)² - b²/4 + c 로 고칠 수 있다.", 10, width=480)
    _t(p, 50, 220, "개념 3  이차함수의 최댓값과 최솟값", 12, width=480)
    _t(
        p,
        50,
        242,
        "a > 0 이면 꼭짓점에서 최솟값을 갖고, a < 0 이면 꼭짓점에서 최댓값을 갖는다.",
        10,
        width=480,
    )
    p = doc.new_page(width=W, height=H)
    _t(p, 50, 60, "예제 1  이차함수 y = x² - 6x + 10 의 최솟값을 구하여라.", 11, width=480)
    _t(p, 50, 85, "풀이) y = (x - 3)² + 1 이므로 x = 3 일 때 최솟값 1", 10, width=480)
    _t(p, 50, 130, "개념 4  제한된 범위에서의 최대·최소", 12, width=480)
    _t(
        p,
        50,
        152,
        "꼭짓점의 x좌표가 범위에 포함되는지 확인하고, 양 끝값과 비교한다.",
        10,
        width=480,
    )
    _t(p, 50, 190, "예제 2  0 ≤ x ≤ 3 에서 y = -x² + 2x + 3 의 최댓값과 최솟값", 11, width=480)
    _t(p, 50, 215, "풀이) 꼭짓점 (1, 4)에서 최댓값 4, x = 3 일 때 최솟값 0", 10, width=480)
    doc.save(str(path))
    return path
