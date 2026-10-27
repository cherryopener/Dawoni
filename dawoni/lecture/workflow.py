"""강의 요약 흐름 (명령줄·서버 공용): 주소 → 자막(작업공간에 보관) → 요약 저장 → Word.

작업공간 `lectures/<영상ID>/`:
- `source.json`: 자막·영상 정보 (같은 영상은 다시 받지 않는다)
- `<요약ID>.json`: 요약본, `<요약ID>.notes.json`: 구간 노트 (어디서 나온 내용인지 확인용)
- `notes/<키>.json`: 구간 노트 재사용 (긴 강의가 중간에 실패해도 끝난 구간은 다시 부르지 않음)
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dawoni.export.docx.check import check_docx
from dawoni.export.docx.export import ExportOutcome
from dawoni.export.docx.fonts import FontSpec
from dawoni.export.docx.lecture import build_lecture_summary
from dawoni.fsio import read_json, write_json_atomic, write_model_atomic
from dawoni.grading.store import ScopeStore, safe_name
from dawoni.lecture.summarize import Progress, summarize
from dawoni.lecture.youtube import load_lecture, video_id_of
from dawoni.models.lecture import ChunkNotes, LectureSource, LectureSummary, PageMode
from dawoni.paths import WorkspacePaths
from dawoni.services import Services

Loader = Callable[[str], LectureSource]
"""주소 → 자막·영상 정보 (시험에서는 가짜로 바꾼다)."""


def _noop(done: int, total: int, message: str) -> None:
    pass


class LectureStore:
    def __init__(self, ws: WorkspacePaths) -> None:
        self.dir = ws.lectures

    def source(self, video_id: str) -> LectureSource | None:
        p = self.dir / video_id / "source.json"
        return LectureSource.model_validate(read_json(p)) if p.exists() else None

    def save_source(self, src: LectureSource) -> LectureSource:
        write_model_atomic(self.dir / src.video_id / "source.json", src)
        return src

    def save(self, s: LectureSummary, notes: list[ChunkNotes] | None = None) -> LectureSummary:
        d = self.dir / s.video_id
        write_model_atomic(d / f"{s.id}.json", s)
        if notes is not None:
            write_json_atomic(d / f"{s.id}.notes.json", [n.model_dump(mode="json") for n in notes])
        return s

    def all(self) -> list[LectureSummary]:
        if not self.dir.is_dir():
            return []
        out = [
            LectureSummary.model_validate(read_json(p))
            for p in self.dir.glob("*/*.json")
            if p.name != "source.json" and not p.name.endswith(".notes.json")
        ]
        return sorted(out, key=lambda s: s.created_at, reverse=True)

    def get(self, summary_id: str) -> LectureSummary:
        for p in self.dir.glob(f"*/{summary_id}.json"):
            return LectureSummary.model_validate(read_json(p))
        raise KeyError(f"강의 요약 {summary_id} 없음")

    def delete(self, summary_id: str) -> None:
        s = self.get(summary_id)
        for name in (f"{s.id}.json", f"{s.id}.notes.json"):
            (self.dir / s.video_id / name).unlink(missing_ok=True)


def get_source(
    ws: WorkspacePaths, url: str, *, refresh: bool = False, loader: Loader = load_lecture
) -> LectureSource:
    store = LectureStore(ws)
    cached = None if refresh else store.source(video_id_of(url))
    return cached or store.save_source(loader(url))


def run_summary(
    svc: Services,
    url: str,
    *,
    scope_id: str | None = None,
    pages: PageMode = 1,
    refresh: bool = False,
    loader: Loader = load_lecture,
    progress: Progress = _noop,
) -> LectureSummary:
    progress(0, 1, "자막 가져오는 중")
    src = get_source(svc.workspace, url, refresh=refresh, loader=loader)
    scope = ScopeStore(svc.workspace).get(scope_id) if scope_id else None
    run = summarize(
        svc.engine, src, scope=scope, pages=pages, progress=progress,
        note_cache=svc.workspace.lectures / src.video_id / "notes",
    )  # fmt: skip
    return LectureStore(svc.workspace).save(run.summary, run.notes)


def export_summary(
    ws: WorkspacePaths, s: LectureSummary, fonts: FontSpec, *, out: Path | None = None
) -> ExportOutcome:
    scope = ScopeStore(ws).get(s.scope_id) if s.scope_id else None
    w = build_lecture_summary(s, fonts, scope)
    path = out or ws.exports / f"{safe_name(s.title)}_강의요약.docx"
    path.parent.mkdir(parents=True, exist_ok=True)
    w.save(path)
    return ExportOutcome(path, check_docx(path), w.warnings, w.math_fallbacks)
