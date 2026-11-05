import * as P from "@radix-ui/react-popover";
import {
  BadgeCheck,
  Bell,
  BookOpenCheck,
  CircleAlert,
  CircleCheck,
  CircleX,
  FileQuestion,
  ListChecks,
  UserRoundPen,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import { Count } from "@/components/ui/count";
import { Segmented } from "@/components/ui/segmented";
import { cn } from "@/lib/cn";
import { bellCount, isTodo, markRead, useNotices } from "@/lib/notices";
import { go } from "@/lib/router";
import type { Notice, NoticeKind } from "@/lib/types";

const ICON: Record<NoticeKind, { icon: LucideIcon; cls: string }> = {
  unreadable: { icon: FileQuestion, cls: "bg-bad-soft text-bad" },
  rename: { icon: UserRoundPen, cls: "bg-bad-soft text-bad" },
  review: { icon: ListChecks, cls: "bg-warn-soft text-warn" },
  key: { icon: CircleAlert, cls: "bg-warn-soft text-warn" },
  rubric: { icon: BadgeCheck, cls: "bg-warn-soft text-warn" },
  proposal: { icon: BookOpenCheck, cls: "bg-accent-soft text-accent-text" },
  job_done: { icon: CircleCheck, cls: "bg-ok-soft text-ok" },
  job_failed: { icon: CircleX, cls: "bg-bad-soft text-bad" },
};

function Row({ n, unread, onOpen }: { n: Notice; unread: boolean; onOpen: () => void }) {
  const { icon: Icon, cls } = ICON[n.kind];
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full cursor-pointer items-start gap-3 rounded-lg px-3 py-2.5 text-left hover:bg-muted"
    >
      <span className={cn("mt-0.5 grid size-8 shrink-0 place-items-center rounded-full", cls)}>
        <Icon className="size-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="font-medium">{n.title}</span>
          {unread && <span className="size-1.5 rounded-full bg-accent" aria-label="읽지 않음" />}
          <span className="ml-auto shrink-0 text-[12px] text-text-3">{n.time}</span>
        </span>
        {n.exam && <span className="block text-[12px] text-text-3">{n.exam}</span>}
        <span className="mt-0.5 block text-[13px] leading-snug text-text-2">{n.detail}</span>
      </span>
    </button>
  );
}

/** 맨 위 종 버튼: 누르면 모든 시험의 알림을 한 번에 본다. 누른 알림은 그 화면으로 데려간다. */
export function NotificationsButton() {
  const { notices, read } = useNotices();
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<"todo" | "all">("todo");
  const count = bellCount(notices, read);
  const todo = notices.filter(isTodo);
  const news = notices.filter((n) => !isTodo(n));
  const shown = filter === "todo" ? todo : notices;
  const openNotice = (n: Notice) => {
    markRead([n.id]);
    setOpen(false);
    go(...n.link);
  };
  return (
    <P.Root open={open} onOpenChange={setOpen}>
      <P.Trigger
        className="relative grid size-9 cursor-pointer place-items-center rounded-lg text-text-2 hover:bg-muted data-[state=open]:bg-muted"
        aria-label={count > 0 ? `알림 ${count}개` : "알림"}
      >
        <Bell className="size-4" />
        <Count n={count} className="absolute -right-0.5 -top-0.5" />
      </P.Trigger>
      <P.Portal>
        <P.Content
          align="end"
          sideOffset={6}
          className="z-50 flex max-h-[min(640px,80vh)] w-[400px] flex-col rounded-[var(--radius-card)] border border-line bg-card text-text"
        >
          <div className="flex items-center gap-2 border-b border-line px-4 py-3">
            <span className="flex-1 text-[15px] font-medium">알림</span>
            <Segmented
              value={filter}
              onChange={setFilter}
              options={[
                { value: "todo", label: `확인할 것 ${todo.length}` },
                { value: "all", label: "전체" },
              ]}
            />
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-1.5">
            {shown.length === 0 && <div className="px-3 py-8 text-center text-text-3">확인할 것이 없습니다.</div>}
            {filter === "all" ? (
              <>
                <div className="px-3 pb-1 pt-2 text-[12px] text-text-3">강사 확인 필요</div>
                {todo.map((n) => (
                  <Row key={n.id} n={n} unread={false} onOpen={() => openNotice(n)} />
                ))}
                <div className="px-3 pb-1 pt-3 text-[12px] text-text-3">작업 소식</div>
                {news.map((n) => (
                  <Row key={n.id} n={n} unread={!read.has(n.id)} onOpen={() => openNotice(n)} />
                ))}
              </>
            ) : (
              shown.map((n) => <Row key={n.id} n={n} unread={false} onOpen={() => openNotice(n)} />)
            )}
          </div>
          <div className="flex items-center border-t border-line px-4 py-2.5 text-[12px] text-text-3">
            <span className="flex-1">확인할 것은 처리하면 저절로 사라집니다.</span>
            {news.some((n) => !read.has(n.id)) && (
              <button
                type="button"
                onClick={() => markRead(news.map((n) => n.id))}
                className="cursor-pointer text-text-2 hover:text-text"
              >
                소식 모두 읽음
              </button>
            )}
          </div>
        </P.Content>
      </P.Portal>
    </P.Root>
  );
}
