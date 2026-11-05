"""진단 묶음 — 버그를 개발자에게 알릴 때 필요한 파일을 zip 하나로 묶어 작업공간에 저장한다.

이메일 서버 없이, 작업공간(공유 폴더)의 `진단묶음/`에 두면 동기화 프로그램이 개발자 PC로 옮긴다.
묶기 전에 `plan_bundle`로 무엇이 들어가는지 목록을 보여 주고, 강사가 고른 묶음만 넣는다.

학생 답안·이름이 들어갈 수 있다 — 실물 답안지와 맞춰 보며 고치기 위해 일부러 넣는다
(학원 안에서만 공유, 개인정보 이용 동의 받음).
"""

from __future__ import annotations

import json
import platform
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dawoni import __version__
from dawoni.paths import AppPaths, WorkspacePaths

BUNDLE_DIR = "진단묶음"
RECENT_SANDBOX_DAYS = 3
"""실패한 AI 호출 기록(격리 폴더)은 최근 며칠 것만."""
MAX_FILE_BYTES = 30_000_000
"""이보다 큰 파일(오래 쌓인 호출 기록 등)은 끝부분만 넣는다."""


@dataclass
class Group:
    key: str
    title: str
    hint: str
    files: list[tuple[Path, str]] = field(default_factory=lambda: list[tuple[Path, str]]())
    """(원본, zip 안 경로)."""
    default: bool = True

    @property
    def size(self) -> int:
        return sum(p.stat().st_size for p, _ in self.files if p.is_file())


def _tree(root: Path, arc: str, pattern: str = "*") -> list[tuple[Path, str]]:
    if not root.is_dir():
        return []
    return [
        (p, f"{arc}/{p.relative_to(root).as_posix()}")
        for p in sorted(root.rglob(pattern))
        if p.is_file() and not p.name.startswith(".~")
    ]


def plan_bundle(app: AppPaths, ws: WorkspacePaths | None, exam_id: str | None) -> list[Group]:
    logs = Group(
        "logs",
        "앱 기록",
        "오류 기록(error.log)·전체 기록(dawoni.log)·AI 호출 기록(calls.jsonl)",
        _tree(app.logs, "logs"),
    )
    cutoff = datetime.now() - timedelta(days=RECENT_SANDBOX_DAYS)
    failed: list[tuple[Path, str]] = []
    if app.sandboxes.is_dir():
        for d in sorted(app.sandboxes.iterdir()):
            if d.is_dir() and datetime.fromtimestamp(d.stat().st_mtime) >= cutoff:
                failed += _tree(d, f"failed_calls/{d.name}")
    sandboxes = Group(
        "failed_calls",
        f"실패한 AI 호출 (최근 {RECENT_SANDBOX_DAYS}일)",
        "AI에게 보낸 프롬프트·첨부 그림(학생 답안 포함)·AI 응답 원본",
        failed,
    )
    groups = [logs, sandboxes]
    if ws is not None:
        groups.append(
            Group(
                "learning",
                "규칙 · 메모리 · 스킬",
                "강사 규칙, 다워니가 배운 메모, 스킬 (md·json)",
                _tree(ws.rules, "workspace/rules")
                + _tree(ws.memory, "workspace/memory")
                + _tree(ws.skills, "workspace/skills"),
            )
        )
        if exam_id:
            exam = ws.exams / exam_id
            groups.append(
                Group(
                    "exam",
                    f"시험 {exam_id}",
                    "문항·루브릭·판독·채점 결과(json)와 답안 스캔본 — 학생 이름·답안 포함",
                    _tree(exam, f"workspace/exams/{exam_id}"),
                )
            )
    return groups


def _add(z: zipfile.ZipFile, src: Path, arc: str) -> None:
    size = src.stat().st_size
    if size <= MAX_FILE_BYTES:
        z.write(src, arc)
        return
    with src.open("rb") as f:
        f.seek(size - MAX_FILE_BYTES)
        z.writestr(arc + ".tail", f.read())


def write_bundle(
    app: AppPaths,
    ws: WorkspacePaths,
    groups: list[Group],
    *,
    note: str,
    machine: str,
    teacher: str | None,
    extra: dict[str, Any],
) -> Path:
    """고른 묶음을 `작업공간/진단묶음/<시각>-<PC>.zip`으로. 저장한 경로."""
    out_dir = ws.root / BUNDLE_DIR
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out_dir / f"{stamp}-{machine or 'pc'}.zip"
    report = [
        f"# 다워니 진단 묶음 {stamp}",
        "",
        f"- 보낸 사람: {teacher or '(이름 없음)'} · PC: {machine}",
        f"- 다워니 {__version__} · {platform.platform()} · Python {platform.python_version()}",
        f"- 들어 있는 것: {', '.join(g.title for g in groups) or '없음'}",
        "",
        "## 무슨 일이 있었나",
        "",
        note.strip() or "(설명 없음)",
        "",
    ]
    tmp = out.with_suffix(".zip.part")  # 동기화 프로그램이 반쯤 쓴 파일을 올리지 않게
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.md", "\n".join(report))
        z.writestr("context.json", json.dumps(extra, ensure_ascii=False, indent=2, default=str))
        for g in groups:
            for src, arc in g.files:
                if src.is_file():
                    _add(z, src, arc)
    tmp.replace(out)
    return out
