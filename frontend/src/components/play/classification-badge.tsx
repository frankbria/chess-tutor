import { cn } from "@/lib/utils";

export const CLASSIFICATION_STYLE: Record<string, { symbol: string; label: string; tone: string }> = {
  brilliant: { symbol: "!!", label: "Brilliant", tone: "bg-primary text-primary-foreground" },
  great: { symbol: "!", label: "Great move", tone: "bg-primary/80 text-primary-foreground" },
  inaccuracy: { symbol: "?!", label: "Inaccuracy", tone: "bg-accent text-accent-foreground" },
  mistake: { symbol: "?", label: "Mistake", tone: "bg-destructive/80 text-white" },
  blunder: { symbol: "??", label: "Blunder", tone: "bg-destructive text-white" },
};

export function ClassificationBadge({ classification }: { classification: string }) {
  const badge = CLASSIFICATION_STYLE[classification];
  if (!badge) return null;
  return (
    <span
      title={badge.label}
      aria-label={badge.label}
      className={cn("rounded px-1.5 py-0.5 font-mono text-xs font-bold", badge.tone)}
    >
      {badge.symbol}
    </span>
  );
}
