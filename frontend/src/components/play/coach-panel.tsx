"use client";

import {
  ArrowDown01Icon,
  ArrowUp01Icon,
  BubbleChatIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { ClassificationBadge } from "@/components/play/classification-badge";
import { Button } from "@/components/ui/button";
import type { CoachMessage, CoachMessageKind } from "@/lib/game-api";
import { cn } from "@/lib/utils";

const KIND_STYLE: Record<CoachMessageKind, string> = {
  comment: "border-l-primary bg-card",
  question: "border-l-accent bg-accent/15",
  hint: "border-l-secondary-foreground bg-secondary",
  answer: "border-l-muted-foreground bg-muted",
  skipped: "border-l-muted-foreground bg-muted/50",
  asked: "border-l-muted-foreground bg-muted",
};

const KIND_LABEL: Record<CoachMessageKind, string> = {
  comment: "Comment",
  question: "Question",
  hint: "Hint",
  answer: "Answer",
  skipped: "Skipped",
  asked: "You asked",
};

/** "3. Nxe5" for a white ply, "3... Nf6" for black; falls back to the ply when the move is unknown. */
function moveLabel(ply: number, san: string | undefined) {
  if (ply === 0) return "Start position";
  if (!san) return `Move ${ply}`;
  const number = Math.ceil(ply / 2);
  return ply % 2 === 1 ? `${number}. ${san}` : `${number}... ${san}`;
}

const hintLabel = (message: CoachMessage) =>
  message.hint_level
    ? `${KIND_LABEL[message.kind]} ${message.hint_level}`
    : KIND_LABEL[message.kind];

function Message({
  message,
  san,
}: {
  message: CoachMessage;
  san: string | undefined;
}) {
  return (
    <li
      data-kind={message.kind}
      data-provisional={message.provisional || undefined}
      className={cn(
        "flex flex-col gap-1 rounded-md border border-l-4 p-3 text-sm transition-all",
        KIND_STYLE[message.kind],
        message.provisional && "italic opacity-70",
      )}
    >
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span className="font-semibold uppercase">{hintLabel(message)}</span>
        <span className="font-mono">{moveLabel(message.ply, san)}</span>
        <span className="rounded border px-1.5 py-0.5 capitalize">
          {message.phase}
        </span>
        {message.classification && (
          <ClassificationBadge classification={message.classification} />
        )}
      </div>
      <p
        className={cn(
          "break-words",
          message.kind === "skipped" && "italic text-muted-foreground",
        )}
      >
        {message.kind === "skipped"
          ? "You skipped this question."
          : message.text}
      </p>
    </li>
  );
}

interface CoachPanelProps {
  messages: CoachMessage[];
  /** The game's moves in SAN, used to name the move each message refers to. */
  moves: string[];
  /** Enables the text input; omit until the player can answer or ask the coach. */
  onSend?: (text: string) => void;
  /** Offers a Skip button for the coach's open question. */
  onSkip?: () => void;
}

/** A side panel from `md` up; on a phone a bar above the bottom nav that expands into the history. */
export function CoachPanel({
  messages,
  moves,
  onSend,
  onSkip,
}: CoachPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [seenCount, setSeenCount] = useState(0);
  const [draft, setDraft] = useState("");
  const listRef = useRef<HTMLOListElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);

  // Everything shown while open counts as read once it closes.
  const setOpen = (open: boolean) => {
    if (!open) setSeenCount(messages.length);
    setExpanded(open);
  };

  useEffect(() => {
    const list = listRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [messages.length, expanded]);

  const unread = expanded ? 0 : messages.length - seenCount;
  const latest = messages.at(-1);
  const collapse = (event: KeyboardEvent) => {
    if (event.key !== "Escape" || !expanded) return;
    setOpen(false);
    toggleRef.current?.focus();
  };
  const send = (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!onSend || !text) return;
    onSend(text);
    setDraft("");
  };

  return (
    <section
      aria-label="Coach"
      data-slot="coach-panel"
      onKeyDown={collapse}
      className={cn(
        "flex min-w-0 flex-col rounded-xl border bg-card",
        "max-md:fixed max-md:inset-x-2 max-md:bottom-[calc(4.25rem+env(safe-area-inset-bottom))] max-md:z-20 max-md:shadow-lg",
      )}
    >
      <Button
        ref={toggleRef}
        variant="ghost"
        aria-expanded={expanded}
        aria-controls="coach-history"
        onClick={() => setOpen(!expanded)}
        className="h-auto min-h-12 justify-start gap-2 rounded-xl px-3 py-2 text-left md:hidden"
      >
        <HugeiconsIcon icon={BubbleChatIcon} className="size-5 text-primary" />
        <span className="font-semibold">Coach</span>
        {!expanded && latest && (
          <span className="min-w-0 flex-1 truncate font-normal text-muted-foreground">
            {latest.text}
          </span>
        )}
        {unread > 0 && (
          <span
            data-testid="unread-badge"
            className="ml-auto rounded-full bg-primary px-2 py-0.5 text-xs font-bold text-primary-foreground"
          >
            {unread}
            <span className="sr-only"> unread</span>
          </span>
        )}
        <HugeiconsIcon
          icon={expanded ? ArrowDown01Icon : ArrowUp01Icon}
          className="ml-auto size-4"
        />
      </Button>
      <h2 className="hidden min-h-12 items-center gap-2 px-3 py-2 font-semibold md:flex">
        <HugeiconsIcon icon={BubbleChatIcon} className="size-5 text-primary" />
        Coach
      </h2>
      <div
        aria-live="polite"
        role="status"
        className="sr-only"
        data-testid="coach-live-region"
      >
        {latest?.text}
      </div>
      <div
        id="coach-history"
        className={cn(
          "min-w-0 flex-col gap-3 px-3 pb-3",
          expanded ? "flex" : "hidden md:flex",
        )}
      >
        <ol
          ref={listRef}
          aria-label="Coach messages"
          className="flex max-h-[40vh] min-h-24 flex-col gap-2 overflow-y-auto md:max-h-96"
        >
          {messages.length === 0 && (
            <li className="text-sm text-muted-foreground">
              The coach will comment on your notable moves here.
            </li>
          )}
          {messages.map((message, index) => (
            <Message
              key={`${message.ply}-${message.role}-${message.kind}-${index}`}
              message={message}
              san={moves[message.ply - 1]}
            />
          ))}
        </ol>
        <form onSubmit={send} className="flex gap-2">
          <input
            aria-label="Message the coach"
            value={draft}
            disabled={!onSend}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={
              onSend
                ? onSkip
                  ? "What was your thought?"
                  : "Ask the coach…"
                : "Questions and answers unlock in a later update"
            }
            className="h-10 min-w-0 flex-1 rounded-md border bg-background px-3 text-sm outline-none transition-all focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50"
          />
          <Button type="submit" disabled={!onSend || !draft.trim()}>
            Send
          </Button>
          {onSkip && (
            <Button type="button" variant="outline" onClick={onSkip}>
              Skip
            </Button>
          )}
        </form>
      </div>
    </section>
  );
}
