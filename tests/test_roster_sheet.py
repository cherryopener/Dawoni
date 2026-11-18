"""명단 파일: 엑셀·CSV는 그대로, 사진(jpg·png)은 AI가 읽는다. 강사가 고른 반은 반이 빈 줄에만
(2026-11-18 사용자: "반 명단을 jpg든 xlsx로든 줄 것")."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from openpyxl import Workbook
from PIL import Image
from tests_support import fake_prompts

from dawoni.agents import AgentClient
from dawoni.config import AppConfig
from dawoni.context import ContextEngine
from dawoni.paths import AppPaths
from dawoni.students.sheet import read_roster_file

MakeFake = Callable[..., Path]


def _engine(app_paths: AppPaths, exe: Path) -> ContextEngine:
    cfg = AppConfig()
    cfg.agents["agy"].executable = str(exe)
    cfg.agents["claude"].enabled = False
    cfg.agents["codex"].enabled = False
    return ContextEngine(AgentClient(cfg, app_paths))


def test_roster_photo_is_read_by_ai(
    app_paths: AppPaths, make_fake: MakeFake, tmp_path: Path
) -> None:
    read = {
        "rows": [
            {"name": "가학생", "class_name": None, "school": "가중"},
            {"name": "나학생A", "class_name": "MSS2-K", "school": None},
        ],
        "uncertain": ["2번째 줄: '나'가 흐림"],
    }
    exe = make_fake("script", script=[{"response": json.dumps(read, ensure_ascii=False)}])
    photo = tmp_path / "명단.jpg"
    Image.new("RGB", (40, 30), "white").save(photo)
    rows, notes = read_roster_file(_engine(app_paths, exe), photo, "MSS1-J")
    assert rows == [
        {"name": "가학생", "class_name": "MSS1-J", "school": "가중"},
        {"name": "나학생A", "class_name": "MSS2-K", "school": None},
    ], "고른 반은 반이 빈 줄에만"
    assert any("사진에서 2명" in n for n in notes) and any("흐림" in n for n in notes)
    assert any("1명을 MSS1-J 반으로" in n for n in notes)
    prompt = fake_prompts(exe)[0]
    assert "학생 명단" in prompt and "MSS1-J 반으로 정했다" in prompt
    assert "전화번호" in prompt, "전화번호는 옮기지 않게"


def test_roster_table_keeps_its_classes(app_paths: AppPaths, tmp_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for row in (["이름", "반"], ["가학생", ""], ["다학생", "MSS2-K"]):
        ws.append(row)
    path = tmp_path / "list.xlsx"
    wb.save(path)
    engine = ContextEngine(AgentClient(AppConfig(), app_paths))  # 표는 AI를 부르지 않는다
    rows, notes = read_roster_file(engine, path, "MSS1-J")
    assert [(r["name"], r["class_name"]) for r in rows] == [
        ("가학생", "MSS1-J"),
        ("다학생", "MSS2-K"),
    ]
    assert any("1명을 MSS1-J 반으로" in n for n in notes)
    rows, _ = read_roster_file(engine, path)
    assert rows[0]["class_name"] is None, "반을 고르지 않으면 그대로"
