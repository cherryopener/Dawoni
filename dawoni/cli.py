"""개발·디버그용 명령줄. UI 없이 각 단계를 실행해 볼 수 있게 한다.

dawoni version
dawoni schema item-content            # AI 출력 스키마(JSON Schema) 출력
dawoni workspace init ~/DawoniWork
dawoni agent detect                   # 설치된 AI CLI 확인
dawoni agent test --cli agy           # 이미지 1장 판독 연결 테스트
dawoni rules list|add|on|off|rm|preview  # 전역 규칙 (cli_rules.py)
dawoni scope … / dawoni exam …        # 채점 파이프라인 (cli_exam.py)
dawoni ingest layout|exam|textbook    # PDF DB화 (cli_ingest.py)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from dawoni import __version__
from dawoni.config import AGENT_NAMES, AgentName, load_config
from dawoni.logs import setup_logging
from dawoni.models import Figure, Item, ItemContent, Job, Source
from dawoni.paths import AppPaths
from dawoni.workspace import WorkspaceError, create_workspace

SCHEMAS: dict[str, type[BaseModel]] = {
    "item-content": ItemContent,
    "item": Item,
    "figure": Figure,
    "job": Job,
    "source": Source,
}


def _out(s: str) -> None:
    sys.stdout.write(s + "\n")


def _cmd_version(_: argparse.Namespace) -> int:
    _out(f"dawoni {__version__} (Python {sys.version.split()[0]})")
    return 0


def _cmd_schema(args: argparse.Namespace) -> int:
    model = SCHEMAS[args.name]
    _out(json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2))
    return 0


def _cmd_workspace_init(args: argparse.Namespace) -> int:
    try:
        ws = create_workspace(Path(args.path).expanduser(), args.name)
    except WorkspaceError as e:
        sys.stderr.write(f"오류: {e}\n")
        return 1
    _out(f"작업공간 준비 완료: {ws.root}")
    return 0


def _cmd_agent_detect(_: argparse.Namespace) -> int:
    from dawoni.agents.detect import detect

    cfg = load_config(AppPaths.default())
    for name in AGENT_NAMES:
        d = detect(name, cfg.agent(name).executable)
        if not d.installed:
            _out(f"{name:7} 미설치")
            continue
        login = {True: "로그인됨", False: "로그인 필요", None: "로그인 상태 확인 불가"}[d.logged_in]
        _out(f"{name:7} {d.version or '?':12} {login:14} {d.path}")
        for note in d.notes:
            _out(f"        ! {note}")
    return 0


def _cmd_agent_test(args: argparse.Namespace) -> int:
    from dawoni.agents import AgentClient, AgentError
    from dawoni.agents.selftest import evaluate, selftest_request

    paths = AppPaths.default()
    paths.ensure()
    setup_logging(paths.logs)
    cfg = load_config(paths)
    name: AgentName = args.cli or cfg.default_agent
    client = AgentClient(cfg, paths)

    def progress(msg: str) -> None:
        sys.stderr.write(f"  … {msg}\n")

    try:
        res = client.run(
            selftest_request(),
            agent=name,
            model=args.model,
            fallback=False,
            keep_sandbox=args.keep,
            on_progress=progress,
        )
    except AgentError as e:
        sys.stderr.write(f"실패: {e.user_message}\n  {e.code}: {e.detail}\n")
        if e.sandbox:
            sys.stderr.write(f"  기록: {e.sandbox}\n")
        return 1
    report = evaluate(res)
    u = res.usage
    _out(
        f"{res.agent} / {res.model or '기본 모델'} — {res.duration_s:.1f}초, "
        f"토큰 {u.total_tokens or '?'}, 스키마 강제 {'O' if res.schema_enforced else 'X'}"
    )
    _out(json.dumps(report.answer, ensure_ascii=False))
    for w in res.warnings:
        _out(f"  ! {w}")
    if report.passed:
        _out("통과: 이미지 판독·한글·수식·JSON 응답 모두 정상")
        return 0
    for p in report.problems:
        _out(f"  ✗ {p}")
    return 2


def _cmd_ui(args: argparse.Namespace) -> int:
    from dawoni.server.launch import run_ui

    return run_ui(port=args.port, window=not args.no_window)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dawoni", description="Dawoni 개발용 명령줄")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="버전 표시").set_defaults(func=_cmd_version)

    sp = sub.add_parser("schema", help="데이터 모델의 JSON Schema 출력")
    sp.add_argument("name", choices=sorted(SCHEMAS))
    sp.set_defaults(func=_cmd_schema)

    wp = sub.add_parser("workspace", help="작업공간 관리")
    wsub = wp.add_subparsers(dest="ws_command", required=True)
    wi = wsub.add_parser("init", help="작업공간 폴더 만들기")
    wi.add_argument("path")
    wi.add_argument("--name")
    wi.set_defaults(func=_cmd_workspace_init)

    ap = sub.add_parser("agent", help="AI CLI 연결")
    asub = ap.add_subparsers(dest="agent_command", required=True)
    asub.add_parser("detect", help="설치·로그인 상태 확인").set_defaults(func=_cmd_agent_detect)
    at = asub.add_parser("test", help="이미지 1장 판독 연결 테스트 (실제 AI 호출)")
    at.add_argument("--cli", choices=AGENT_NAMES)
    at.add_argument("--model", help="이번 테스트에만 쓸 모델 (예: claude-sonnet-5-5-medium)")
    at.add_argument("--keep", action="store_true", help="성공해도 격리 폴더 보존")
    at.set_defaults(func=_cmd_agent_test)

    ui = sub.add_parser("ui", help="화면(앱 창) 열기")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument(
        "--no-window", action="store_true", help="창 없이 서버만 (주소를 브라우저로 열기)"
    )
    ui.set_defaults(func=_cmd_ui)

    from dawoni import cli_exam, cli_export, cli_fig, cli_gen, cli_ingest, cli_rules

    cli_rules.register(sub)
    cli_exam.register(sub)
    cli_ingest.register(sub)
    cli_export.register(sub)
    cli_gen.register(sub)
    cli_fig.register(sub)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
