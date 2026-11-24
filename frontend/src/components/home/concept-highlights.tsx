"use client";

import { ArrowUpRight01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useState } from "react";

import { themeLabel } from "@/components/progress/concept-groups";
import { useProgressApi } from "@/components/progress/progress-api-context";
import type { ConceptSummary } from "@/lib/progress-api";

const RECENT_IMPROVEMENT_COUNT = 3;

const cardClass = "grid w-full max-w-sm gap-2 rounded-lg border p-4 text-left";

/** Today's focus (the top FOCUS concept, with a practice link) and recent concept wins. */
export function ConceptHighlights() {
  const api = useProgressApi();
  const [concepts, setConcepts] = useState<ConceptSummary[] | null>(null);

  useEffect(() => {
    let current = true;
    api
      .getConcepts()
      .then((loaded) => current && setConcepts(loaded))
      .catch(() => undefined); // optional cards; the Play button still works
    return () => {
      current = false;
    };
  }, [api]);

  if (concepts === null) return null;
  const focus = concepts.find((concept) => concept.state === "focus");
  // shortcut: updated_at also moves when only the count changes, so an old win can resurface; use a state-transition timestamp once one exists.
  const improvements = concepts
    .filter((concept) => concept.state === "improving" || concept.state === "mastered")
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, RECENT_IMPROVEMENT_COUNT);

  return (
    <>
      <section aria-labelledby="focus-heading" data-slot="todays-focus" className={`${cardClass} border-primary bg-primary/5`}>
        <h2 id="focus-heading" className="text-lg font-semibold">
          Today&apos;s focus
        </h2>
        {focus ? (
          <>
            <p className="font-medium">{focus.label}</p>
            {focus.practice_themes.length > 0 && (
              <a
                href={`https://lichess.org/training/${focus.practice_themes[0]}`}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={`Practice ${themeLabel(focus.practice_themes[0])} puzzles on Lichess (opens in a new tab)`}
                className="inline-flex min-h-9 items-center gap-1 justify-self-start rounded-md border px-2 text-sm text-primary transition-all outline-none hover:bg-primary/10 focus-visible:ring-[3px] focus-visible:ring-ring/50"
              >
                Practice {themeLabel(focus.practice_themes[0])}
                <HugeiconsIcon icon={ArrowUpRight01Icon} size={14} aria-hidden="true" />
              </a>
            )}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Play a few games and the coach will pick what to work on.</p>
        )}
      </section>
      {improvements.length > 0 && (
        <section aria-labelledby="improvements-heading" data-slot="recent-improvements" className={cardClass}>
          <h2 id="improvements-heading" className="text-lg font-semibold">
            Recent improvements
          </h2>
          <ul className="grid gap-1 text-sm">
            {improvements.map((concept) => (
              <li key={concept.concept} className="flex justify-between gap-2">
                <span className="font-medium">{concept.label}</span>
                <span className="text-muted-foreground">{concept.state === "mastered" ? "Mastered" : "Improving"}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
