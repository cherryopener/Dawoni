"""개발·디버그용 명령줄. UI 없이 각 단계를 실행해 볼 수 있게 한다.

dawoni version
dawoni schema item-content            # AI 출력 스키마(JSON Schema) 출력
dawoni workspace init ~/DawoniWork
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from dawoni import __version__
from dawoni.models import Figure, Item, ItemContent, Job, Source
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
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
