import { ArrowUpRight01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";

import type { ConceptStatus, ConceptSummary } from "@/lib/progress-api";
import { cn } from "@/lib/utils";

const GROUPS: {
  state: ConceptStatus;
  title: string;
  hint: string;
  practice: boolean;
}[] = [
  {
    state: "focus",
    title: "Focus",
    hint: "Your most frequent recent slips. Work on these first.",
    practice: true,
  },
  {
    state: "recurring",
    title: "Recurring",
    hint: "Showing up more than once.",
    practice: true,
  },
  {
    state: "improving",
    title: "Improving",
    hint: "Fewer slips lately. Keep it up.",
    practice: false,
  },
  { state: "new", title: "New", hint: "Seen once so far.", practice: false },
  {
    state: "mastered",
    title: "Mastered",
    hint: "You have stopped making these mistakes.",
    practice: false,
  },
];

// recency_score gains 1 per occurrence and decays by 0.8 per game, so above 1 means seen again lately.
const RECENT_SCORE = 1.5;

function themeLabel(theme: string): string {
  const spaced = theme.replace(/([a-z0-9])([A-Z])/g, "$1 $2");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function describeCount(concept: ConceptSummary): string {
  const seen = `seen ${concept.count}×`;
  return concept.recency_score >= RECENT_SCORE ? `${seen}, recently` : seen;
}

export function ConceptGroups({ concepts }: { concepts: ConceptSummary[] }) {
  return (
    <div className="grid min-w-0 gap-4">
      {GROUPS.map(({ state, title, hint, practice }) => {
        const members = concepts.filter((concept) => concept.state === state);
        if (members.length === 0) return null;
        const headingId = `concepts-${state}`;
        return (
          <section
            key={state}
            aria-labelledby={headingId}
            data-slot="concept-group"
            className={cn(
              "grid min-w-0 gap-2 rounded-lg border p-3",
              state === "focus" &&
                "border-primary bg-primary/5 ring-[3px] ring-primary/10",
            )}
          >
            <h3 id={headingId} className="font-semibold">
              {title}
            </h3>
            <p className="text-sm text-muted-foreground">{hint}</p>
            <ul className="grid min-w-0 gap-2">
              {members.map((concept) => (
                <li key={concept.concept} className="grid min-w-0 gap-1">
                  <p className="flex flex-wrap items-baseline justify-between gap-x-2">
                    <span className="font-medium">{concept.label}</span>
                    {state !== "mastered" && (
                      <span className="text-sm text-muted-foreground">
                        {describeCount(concept)}
                      </span>
                    )}
                  </p>
                  {practice && concept.practice_themes.length > 0 && (
                    <ul className="flex flex-wrap gap-2">
                      {concept.practice_themes.map((theme) => (
                        <li key={theme}>
                          <a
                            href={`https://lichess.org/training/${theme}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-label={`Practice ${themeLabel(theme)} puzzles on Lichess (opens in a new tab)`}
                            className="inline-flex min-h-9 items-center gap-1 rounded-md border px-2 text-sm text-primary transition-all outline-none hover:bg-primary/10 focus-visible:ring-[3px] focus-visible:ring-ring/50"
                          >
                            Practice {themeLabel(theme)}
                            <HugeiconsIcon
                              icon={ArrowUpRight01Icon}
                              size={14}
                              aria-hidden="true"
                            />
                          </a>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
