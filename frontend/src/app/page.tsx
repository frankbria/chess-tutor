import { Chess01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";

import { Button } from "@/components/ui/button";

export default function HomePage() {
  return (
    <section className="flex flex-1 flex-col items-center justify-center gap-6 text-center">
      <div className="rounded-2xl bg-gradient-to-br from-primary to-accent p-4 text-primary-foreground shadow-lg">
        <HugeiconsIcon icon={Chess01Icon} size={48} aria-hidden="true" />
      </div>
      <h1 className="text-3xl font-bold tracking-tight">Chess Tutor</h1>
      <p className="max-w-sm text-muted-foreground">
        Play at your level with a coach that notices what you miss.
      </p>
      {/* Enabled once game creation exists (P1.7, #19). */}
      <Button size="lg" className="w-full max-w-xs" disabled>
        Play
      </Button>
    </section>
  );
}
