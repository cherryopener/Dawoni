# pyright: basic, reportMissingTypeStubs=false
"""비스듬히 찍은 답안지 사진의 원근 보정 (OpenCV).

종이(가장 큰 사각형 윤곽)를 찾아 정면에서 본 직사각형으로 편다. 확신이 없으면 손대지 않는다:
- 사각형이 사진 면적의 30% 미만이면 (종이를 못 찾음)
- 사각형이 사진 전체와 거의 같으면 (이미 반듯한 스캔)
잘못 펴는 것보다 원본이 낫기 때문이다.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

MIN_AREA_RATIO = 0.30
FULL_FRAME_RATIO = 0.95


def _order(pts: np.ndarray) -> np.ndarray:
    """네 점을 왼쪽 위, 오른쪽 위, 오른쪽 아래, 왼쪽 아래 순서로."""
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array(
        [pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]],
        dtype=np.float32,
    )


def find_paper(gray: np.ndarray) -> np.ndarray | None:
    """종이의 네 모서리 (없으면 None)."""
    h, w = gray.shape[:2]
    scale = 1000 / max(h, w)
    small = cv2.resize(gray, (int(w * scale), int(h * scale))) if scale < 1 else gray
    blur = cv2.GaussianBlur(small, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area_total = small.shape[0] * small.shape[1]
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        ratio = cv2.contourArea(approx) / area_total
        if ratio < MIN_AREA_RATIO or ratio > FULL_FRAME_RATIO:
            return None
        pts = approx.reshape(4, 2).astype(np.float32) / (scale if scale < 1 else 1)
        return _order(pts)
    return None


def correct_perspective(im: Image.Image) -> tuple[Image.Image, bool]:
    """(보정된 이미지, 보정했는가)."""
    rgb = np.asarray(im.convert("RGB"))
    corners = find_paper(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
    if corners is None:
        return im, False
    tl, tr, br, bl = corners
    width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    if width < 200 or height < 200:
        return im, False
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], np.float32)
    m = cv2.getPerspectiveTransform(corners, dst)
    warped = cv2.warpPerspective(rgb, m, (width, height), flags=cv2.INTER_CUBIC)
    return Image.fromarray(warped), True
