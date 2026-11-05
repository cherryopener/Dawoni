"""`dawoni rules …` — 전역 규칙 관리.

dawoni rules list
dawoni rules add "학생에게는 항상 존댓말로 첨삭해" [--yes]   # AI가 정제한 초안 → --yes면 저장
dawoni rules on <ID> | off <ID> | rm <ID>
dawoni rules preview grade                                # 채점 프롬프트에 들어갈 규칙 레이어
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, get_args

from dawoni.agents import AgentError
from dawoni.config import AGENT_NAMES
from dawoni.context import TaskFailed
from dawoni.context.assembler import render_rules
from dawoni.context.tasks import RULE_TARGET_LABELS, RuleTarget
from dawoni.services import open_services
from dawoni.workspace import WorkspaceError


def _out(s: str) -> None:
    sys.stdout.write(s + "\n")


def _err(s: str) -> int:
    sys.stderr.write(f"오류: {s}\n")
    return 1


def _ws(args: argparse.Namespace) -> Path | None:
    return Path(args.workspace) if args.workspace else None


def _targets(applies_to: list[Any]) -> str:
    return ", ".join("전체" if t == "all" else RULE_TARGET_LABELS[t] for t in applies_to)


def _cmd_list(args: argparse.Namespace) -> int:
    svc = open_services(_ws(args))
    rules = svc.rules.all()
    if not rules:
        _out("등록된 규칙이 없습니다.")
    for r in rules:
        _out(f"[{'ON ' if r.enabled else 'OFF'}] {r.id}  {r.title}  ({_targets(r.applies_to)})")
        _out(f"      {r.text}")
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    from dawoni.context.refine import refine_rule

    svc = open_services(_ws(args))
    if args.cli:
        svc.config.default_agent = args.cli
    try:
        res = refine_rule(svc.engine, args.request, svc.rules)
    except (AgentError, TaskFailed) as e:
        return _err(str(e))
    d = res.value
    _out(f"제목: {d.title}")
    _out(f"규칙: {d.text}")
    _out(f"적용: {_targets(d.applies_to)}")
    for c in d.conflicts:
        _out(f"  ! 충돌/중복: {c.rule_id} — {c.reason}")
    if d.clarification_needed:
        _out(f"  ? 확인 필요: {d.clarification_needed}")
    _out(f"({res.agent}/{res.model}, {res.duration_s:.1f}초, 시도 {res.attempts}회)")
    if not args.yes:
        _out("저장하지 않았습니다. 내용이 맞으면 --yes를 붙여 다시 실행하세요.")
        return 0
    rule = svc.rules.save(d.to_rule(args.request))
    _out(f"저장: {rule.id}")
    return 0


def _toggle(args: argparse.Namespace, enabled: bool) -> int:
    svc = open_services(_ws(args))
    try:
        rule = svc.rules.resolve(args.id)
    except KeyError as e:
        return _err(str(e))
    svc.rules.set_enabled(rule.id, enabled)
    _out(f"{'켬' if enabled else '끔'}: {rule.title}")
    return 0


def _cmd_on(args: argparse.Namespace) -> int:
    return _toggle(args, True)


def _cmd_off(args: argparse.Namespace) -> int:
    return _toggle(args, False)


def _cmd_rm(args: argparse.Namespace) -> int:
    svc = open_services(_ws(args))
    try:
        rule = svc.rules.resolve(args.id)
    except KeyError as e:
        return _err(str(e))
    svc.rules.delete(rule.id)
    _out(f"삭제: {rule.title}")
    return 0


def _cmd_preview(args: argparse.Namespace) -> int:
    svc = open_services(_ws(args))
    target: RuleTarget = args.target
    rules = svc.rules.active_for(target)
    _out(f"[{RULE_TARGET_LABELS[target]}] 작업에 들어가는 규칙 {len(rules)}개")
    _out(render_rules(rules) if rules else "(없음)")
    return 0


def _wrap(fn: Any) -> Any:
    def run(args: argparse.Namespace) -> int:
        try:
            return int(fn(args))
        except WorkspaceError as e:
            return _err(str(e))

    return run


def register(sub: Any) -> None:
    rp = sub.add_parser("rules", help="전역 규칙 (ON/OFF)")
    rp.add_argument("--workspace", help="작업공간 경로 (없으면 설정값)")
    rs = rp.add_subparsers(dest="rules_command", required=True)

    rs.add_parser("list", help="규칙 목록").set_defaults(func=_wrap(_cmd_list))

    ra = rs.add_parser("add", help="자연어 요구 → AI 정제 초안 (--yes면 저장)")
    ra.add_argument("request")
    ra.add_argument("--yes", action="store_true")
    ra.add_argument("--cli", choices=AGENT_NAMES)
    ra.set_defaults(func=_wrap(_cmd_add))

    on = rs.add_parser("on", help="규칙 켜기")
    on.add_argument("id")
    on.set_defaults(func=_wrap(_cmd_on))
    off = rs.add_parser("off", help="규칙 끄기")
    off.add_argument("id")
    off.set_defaults(func=_wrap(_cmd_off))

    rm = rs.add_parser("rm", help="규칙 삭제")
    rm.add_argument("id")
    rm.set_defaults(func=_wrap(_cmd_rm))

    pv = rs.add_parser("preview", help="작업 프롬프트에 들어갈 규칙 레이어 보기")
    pv.add_argument("target", choices=get_args(RuleTarget))
    pv.set_defaults(func=_wrap(_cmd_preview))
