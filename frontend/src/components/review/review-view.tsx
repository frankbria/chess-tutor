"use client";

import { ArrowLeft01Icon, ArrowRight01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useMemo, useRef, useState, type FormEvent, type PointerEvent } from "react";

import { Chessboard } from "@/components/play/chessboard";
import { OpeningLabel } from "@/components/play/opening-label";
import { ClassificationBadge, CLASSIFICATION_STYLE } from "@/components/play/classification-badge";
import { EvalGraph } from "@/components/review/eval-graph";
import { Button } from "@/components/ui/button";
import { moveLabel, positionAfter } from "@/lib/chess-board";
import { ApiError, type CoachMessage, type GameState } from "@/lib/game-api";
import { httpReviewApi, type Review, type ReviewApi } from "@/lib/review-api";
import { cn } from "@/lib/utils";

const SWIPE_DISTANCE_PX = 50;

// The post-game summary is written in the background; poll for it briefly, then give up.
const SUMMARY_RETRY_DELAYS_MS = [2000, 4000, 8000];

const NO_MESSAGE = "Could not load the review. Try again.";

export function ReviewView({ gameId, api = httpReviewApi }: { gameId: number; api?: ReviewApi }) {
  const [data, setData] = useState<{ game: GameState; review: Review; notes: CoachMessage[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ply, setPly] = useState(0);
  const [summaryRetries, setSummaryRetries] = useState(0);
  const [draft, setDraft] = useState("");
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const swipeStart = useRef<number | null>(null);
  const totalPlies = data?.game.moves.length ?? 0;

  useEffect(() => {
    let current = true;
    Promise.all([api.getGame(gameId), api.getReview(gameId), api.getCoachMessages(gameId)])
      .then(([game, review, notes]) => {
        if (!current) return;
        setData({ game, review, notes });
        setPly(game.moves.length);
      })
      .catch((failure: unknown) => current && setError(failure instanceof ApiError ? failure.message : NO_MESSAGE));
    return () => {
      current = false;
    };
  }, [api, gameId]);

  const summaryPending = data !== null && data.review.summary === null;
  useEffect(() => {
    if (!summaryPending || summaryRetries >= SUMMARY_RETRY_DELAYS_MS.length) return;
    const timer = setTimeout(() => {
      api
        .getReview(gameId)
        .then((review) => setData((known) => known && { ...known, review }))
        .catch(() => undefined) // the first load already succeeded; keep what is shown
        .finally(() => setSummaryRetries((count) => count + 1));
    }, SUMMARY_RETRY_DELAYS_MS[summaryRetries]);
    return () => clearTimeout(timer);
  }, [api, gameId, summaryPending, summaryRetries]);

  useEffect(() => {
    const keys: Record<string, (from: number) => number> = {
      ArrowLeft: (from) => from - 1,
      ArrowRight: (from) => from + 1,
      Home: () => 0,
      End: () => totalPlies,
    };
    const onKey = (event: KeyboardEvent) => {
      const target = keys[event.key];
      if (!target || event.target instanceof HTMLInputElement) return;
      event.preventDefault();
      setPly((from) => Math.min(totalPlies, Math.max(0, target(from))));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [totalPlies]);

  const position = useMemo(() => (data ? positionAfter(data.game.moves, ply) : null), [data, ply]);

  if (error) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error}
      </p>
    );
  }
  if (!data || !position) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const { game, review, notes } = data;
  const step = (change: number) => setPly((from) => Math.min(totalPlies, Math.max(0, from + change)));
  const moment = review.key_moments.find((candidate) => candidate.ply === ply);
  const notesHere = notes.filter((note) => note.ply === ply);
  const classification = moment?.classification ?? notesHere.find((note) => note.classification)?.classification;

  const ask = async (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || asking) return;
    setAsking(true);
    setAskError(null);
    try {
      await api.askAboutPosition(gameId, ply, text);
      const stored = await api.getCoachMessages(gameId);
      setData((known) => known && { ...known, notes: stored });
      setDraft("");
    } catch (failure) {
      setAskError(failure instanceof ApiError ? failure.message : "Could not reach the coach. Try again.");
    } finally {
      setAsking(false);
    }
  };

  // A horizontal drag past the threshold steps the game; pan-y keeps vertical scrolling working.
  const endSwipe = (event: PointerEvent) => {
    if (swipeStart.current === null) return;
    const distance = event.clientX - swipeStart.current;
    swipeStart.current = null;
    if (Math.abs(distance) >= SWIPE_DISTANCE_PX) step(distance > 0 ? -1 : 1);
  };

  return (
    <div className="grid min-w-0 gap-6 md:grid-cols-[minmax(0,32rem)_minmax(0,1fr)]">
      <div className="grid min-w-0 content-start gap-3">
        <div
          className="touch-pan-y"
          onPointerDown={(event) => (swipeStart.current = event.clientX)}
          onPointerUp={endSwipe}
          onPointerCancel={() => (swipeStart.current = null)}
        >
          <Chessboard
            fen={position.fen}
            orientation={game.player_color}
            turnColor={position.turnColor}
            inCheck={position.inCheck}
            lastMove={position.lastMove}
            syncToken={0}
            onMove={() => undefined}
          />
        </div>
        <div className="flex items-center justify-between gap-2">
          <Button variant="outline" size="icon" aria-label="Previous move" disabled={ply === 0} onClick={() => step(-1)}>
            <HugeiconsIcon icon={ArrowLeft01Icon} />
          </Button>
          <p className="font-mono text-sm" aria-live="polite">
            {ply === 0 ? "Start position" : moveLabel(ply, game.moves[ply - 1])}
            {ply === review.book_exit?.ply && (
              <span className="ml-2 rounded-sm bg-muted px-1.5 py-0.5 font-sans text-xs text-muted-foreground">
                Out of book
              </span>
            )}
          </p>
          <Button variant="outline" size="icon" aria-label="Next move" disabled={ply === totalPlies} onClick={() => step(1)}>
            <HugeiconsIcon icon={ArrowRight01Icon} />
          </Button>
        </div>
        <OpeningLabel
          step={review.opening_line[ply - 1]}
          bookExit={review.book_exit}
          moves={game.moves}
          playerColor={game.player_color}
        />
        <p className="text-xs text-muted-foreground">Use the arrow keys, Home and End, or swipe the board.</p>
      </div>

      <div className="grid min-w-0 content-start gap-6">
        <section aria-labelledby="summary-heading" className="grid gap-2">
          <h2 id="summary-heading" className="text-lg font-semibold">
            Summary
          </h2>
          <p className="text-sm text-muted-foreground">
            {game.result}
            {review.accuracy !== null && ` · Accuracy ${Math.round(review.accuracy)}%`}
            {review.performance_rating !== null && ` · Performance ${Math.round(review.performance_rating)}`}
          </p>
          {review.summary && <p className="text-sm">{review.summary.summary}</p>}
        </section>

        <section aria-labelledby="graph-heading" className="grid min-w-0 gap-2">
          <h2 id="graph-heading" className="text-lg font-semibold">
            Winning chances
          </h2>
          <EvalGraph points={review.win_probabilities} totalPlies={totalPlies} ply={ply} />
        </section>

        <section aria-labelledby="moments-heading" className="grid gap-2">
          <h2 id="moments-heading" className="text-lg font-semibold">
            Key moments
          </h2>
          {review.key_moments.length === 0 ? (
            <p className="text-sm text-muted-foreground">No key moments: a clean game.</p>
          ) : (
            <ul aria-label="Key moments" className="grid gap-2">
              {review.key_moments.map((candidate) => (
                <li key={candidate.ply}>
                  <Button
                    variant="outline"
                    aria-current={candidate.ply === ply}
                    onClick={() => setPly(candidate.ply)}
                    className={cn("h-auto w-full justify-start gap-2 py-2", candidate.ply === ply && "border-primary")}
                  >
                    <span className="font-mono">{moveLabel(candidate.ply, candidate.san)}</span>
                    <ClassificationBadge classification={candidate.classification} />
                    <span className="text-muted-foreground">
                      {CLASSIFICATION_STYLE[candidate.classification]?.label}
                    </span>
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-label="This move" className="grid gap-2">
          <div role="status" className="flex flex-wrap items-center gap-2 text-sm">
            {classification && (
              <>
                <ClassificationBadge classification={classification} />
                <span>{CLASSIFICATION_STYLE[classification]?.label}</span>
              </>
            )}
          </div>
          {moment && (
            <p className="text-sm">
              Best was {moment.best_move_san}
              {moment.win_probability_before !== null && moment.win_probability_after !== null && (
                <span className="text-muted-foreground">
                  {" "}
                  (your chances went from {Math.round(moment.win_probability_before)}% to{" "}
                  {Math.round(moment.win_probability_after)}%)
                </span>
              )}
            </p>
          )}
          {notesHere.map((note, index) => (
            <p
              key={index}
              className={cn(
                "rounded-md border border-l-4 bg-card p-3 text-sm",
                note.role === "player" ? "border-l-muted-foreground bg-muted" : "border-l-primary",
              )}
            >
              {note.role === "player" && (
                <span className="font-semibold">{note.kind === "answer" ? "Your answer: " : "You asked: "}</span>
              )}
              {note.text}
            </p>
          ))}
          <form onSubmit={ask} className="flex gap-2">
            <input
              aria-label="Ask the coach"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder={ply === 0 ? "Ask about the start position…" : `Ask about the position after ${moveLabel(ply, game.moves[ply - 1])}…`}
              className="h-10 min-w-0 flex-1 rounded-md border bg-background px-3 text-sm outline-none transition-all focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
            />
            <Button type="submit" disabled={asking || !draft.trim()}>
              {asking ? "Asking…" : "Ask"}
            </Button>
          </form>
          {askError && (
            <p role="alert" className="text-sm text-destructive">
              {askError}
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
