"use client";

import {
  LockIcon,
  Loading03Icon,
  Undo02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { CoachPanel } from "@/components/play/coach-panel";
import { ClockDisplay } from "@/components/play/clock-display";
import { Chessboard } from "@/components/play/chessboard";
import { GameOverDialog } from "@/components/play/game-over-dialog";
import { MoveList } from "@/components/play/move-list";
import { PromotionDialog } from "@/components/play/promotion-dialog";
import {
  destinationsFrom,
  isInCheck,
  isPromotion,
  lastMoveSquares,
} from "@/lib/chess-board";
import { cn } from "@/lib/utils";
import {
  ApiError,
  httpGameApi,
  type CoachMessage,
  type GameApi,
  type GameState,
  type Proposal,
} from "@/lib/game-api";

const sameMessage = (a: CoachMessage, b: CoachMessage) =>
  a.ply === b.ply &&
  a.role === b.role &&
  a.kind === b.kind &&
  a.text === b.text &&
  a.hint_level === b.hint_level;

/** The stream and the history fetch can overlap, so a message is only added once. */
const withoutRepeats = (known: CoachMessage[], incoming: CoachMessage[]) =>
  incoming.filter(
    (message) => !known.some((existing) => sameMessage(existing, message)),
  );

const errorMessage = (error: unknown) =>
  error instanceof ApiError
    ? error.message
    : "Could not reach the game server. Try again.";

export function PlayBoard({
  gameId,
  api = httpGameApi,
}: {
  gameId: number;
  api?: GameApi;
}) {
  const [game, setGame] = useState<GameState | null>(null);
  const [thinking, setThinking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [promotion, setPromotion] = useState<{
    from: string;
    to: string;
  } | null>(null);
  const [gameOverReason, setGameOverReason] = useState<string>();
  const [dialogDismissed, setDialogDismissed] = useState(false);
  const [confirmingResign, setConfirmingResign] = useState(false);
  const [syncToken, setSyncToken] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [connectionLost, setConnectionLost] = useState(false);
  const [coachMessages, setCoachMessages] = useState<CoachMessage[]>([]);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [proposing, setProposing] = useState(false);
  const [settling, setSettling] = useState(false);
  // Messages before this index belong to proposals that were already rethought.
  const [hintFloor, setHintFloor] = useState(0);
  const plyCount = useRef(0);
  const hasCoach = game !== null && game.mode !== "regular";
  const isActive = game?.mode === "active";

  // The server is the source of truth; ignore anything older than what is already shown.
  const accept = useCallback((next: GameState) => {
    if (next.moves.length < plyCount.current) return;
    plyCount.current = next.moves.length;
    setGame(next);
  }, []);

  const resync = useCallback(async () => {
    try {
      accept(await api.getGame(gameId));
    } catch (error) {
      setMessage(errorMessage(error));
    }
    setSyncToken((token) => token + 1);
  }, [api, gameId, accept]);

  useEffect(() => {
    api
      .getGame(gameId)
      .then(accept, (error: unknown) => setLoadError(errorMessage(error)));
    return api.subscribe(gameId, {
      onCoachComment: ({ ply, text, classification, phase, hint_level }) => {
        // Active mode's hints and remarks are stored as hints, so show them as hints live too.
        const kind =
          classification === "hint" || classification === "remark"
            ? "hint"
            : "comment";
        const comment: CoachMessage = {
          ply,
          role: "coach",
          kind,
          text,
          classification,
          phase,
          hint_level,
        };
        setCoachMessages((known) => [
          ...known,
          ...withoutRepeats(known, [comment]),
        ]);
      },
      onSnapshot: () => {
        setConnectionLost(false);
        void resync();
      },
      onOpponentMove: ({ clock }) => {
        setConnectionLost(false);
        // Switch the running clock now instead of waiting for the refetch.
        if (clock) setGame((current) => current && { ...current, clock });
        void resync();
      },
      onGameOver: ({ reason }) => {
        setConnectionLost(false);
        setGameOverReason(reason);
        void resync();
      },
      onConnectionLost: () => setConnectionLost(true),
    });
  }, [api, gameId, accept, resync, loadAttempt]);

  useEffect(() => {
    if (!hasCoach) return;
    api.getCoachMessages(gameId).then(
      (history) =>
        setCoachMessages((known) => [
          ...history,
          ...withoutRepeats(history, known),
        ]),
      (error: unknown) => setMessage(errorMessage(error)),
    );
  }, [api, gameId, hasCoach]);

  useEffect(() => {
    if (!isActive) return;
    api.getPendingProposal(gameId).then(
      (pending) => pending && setProposal(pending),
      (error: unknown) => setMessage(errorMessage(error)),
    );
  }, [api, gameId, isActive]);

  const proposeMove = async (uci: string) => {
    setMessage(null);
    setProposing(true);
    try {
      setProposal(await api.proposeMove(gameId, uci));
    } catch (error) {
      setMessage(errorMessage(error));
      await resync();
    } finally {
      setProposing(false);
    }
  };

  const rethink = async () => {
    setMessage(null);
    setSettling(true);
    try {
      await api.rethink(gameId);
      setProposal(null);
      setHintFloor(coachMessages.length);
      setSyncToken((token) => token + 1); // snap the pieces back to the pre-move position
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setSettling(false);
    }
  };

  const lockIn = async () => {
    setMessage(null);
    setSettling(true);
    setThinking(true);
    try {
      accept(await api.lockIn(gameId));
      setProposal(null);
    } catch (error) {
      setMessage(errorMessage(error));
      await resync();
      // The server may still hold the proposal; keep the recovery controls only if it does.
      setProposal(await api.getPendingProposal(gameId).catch(() => proposal));
    } finally {
      setThinking(false);
      setSettling(false);
    }
  };

  const submitMove = async (uci: string) => {
    if (game?.mode === "active") return proposeMove(uci);
    setMessage(null);
    setThinking(true);
    try {
      accept(await api.playMove(gameId, uci));
    } catch (error) {
      setMessage(errorMessage(error));
      await resync();
    } finally {
      setThinking(false);
    }
  };

  const resign = async () => {
    setConfirmingResign(false);
    try {
      accept(await api.resign(gameId));
    } catch (error) {
      setMessage(errorMessage(error));
    }
  };

  const takeBack = async () => {
    setMessage(null);
    try {
      const next = await api.takeback(gameId);
      plyCount.current = 0; // a takeback is the one change that shortens the move list
      accept(next);
      // The server drops the coach messages of the removed plies, so replace rather than merge.
      if (hasCoach) setCoachMessages(await api.getCoachMessages(gameId));
    } catch (error) {
      setMessage(errorMessage(error));
    }
  };

  if (!game && loadError) {
    return (
      <div
        role="alert"
        className="flex flex-col items-start gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-4 text-destructive"
      >
        <p>{loadError}</p>
        <Button
          variant="outline"
          onClick={() => {
            setLoadError(null);
            setLoadAttempt((attempt) => attempt + 1);
          }}
        >
          Retry
        </Button>
      </div>
    );
  }
  if (!game) return <p role="status">Loading game…</p>;

  const myTurn =
    game.status === "active" &&
    game.side_to_move === game.player_color &&
    !thinking &&
    !proposing &&
    !proposal;
  const proposalHint =
    proposal &&
    coachMessages.findLast(
      (entry, index) =>
        index >= hintFloor &&
        entry.kind === "hint" &&
        entry.ply === proposal.ply &&
        (entry.hint_level == null ||
          entry.hint_level === Math.min(proposal.rethinks + 1, 3)),
    );
  const handleMove = (from: string, to: string) => {
    if (isPromotion(game.legal_moves, from, to)) setPromotion({ from, to });
    else void submitMove(`${from}${to}`);
  };
  const cancelPromotion = () => {
    setPromotion(null);
    setSyncToken((token) => token + 1);
  };

  return (
    <div
      className={cn(
        "grid gap-4 md:grid-cols-[minmax(0,40rem)_minmax(16rem,1fr)]",
        hasCoach && "max-md:pb-16",
      )}
    >
      <div className="flex flex-col gap-3">
        <Chessboard
          fen={game.fen}
          orientation={game.player_color}
          turnColor={game.side_to_move}
          inCheck={isInCheck(game.fen)}
          lastMove={lastMoveSquares(game.moves)}
          destinations={myTurn ? destinationsFrom(game.legal_moves) : undefined}
          syncToken={syncToken}
          proposal={
            proposal
              ? [proposal.uci.slice(0, 2), proposal.uci.slice(2, 4)]
              : undefined
          }
          onMove={handleMove}
        />
        {proposal && (
          <section
            aria-label="Proposed move"
            className="flex flex-col gap-3 rounded-xl border-2 border-primary/40 bg-primary/5 p-3"
          >
            <p className="font-semibold">Proposed move: {proposal.san}</p>
            {proposalHint && (
              <p
                data-testid="proposal-hint"
                className="rounded-md bg-secondary p-2 text-sm"
              >
                {proposalHint.hint_level ? (
                  <strong>Hint {proposalHint.hint_level}: </strong>
                ) : null}
                {proposalHint.text}
              </p>
            )}
            <div className="grid grid-cols-2 gap-3">
              <Button
                variant="outline"
                disabled={thinking || settling}
                onClick={() => void rethink()}
                className="min-h-14 text-base"
              >
                <HugeiconsIcon icon={Undo02Icon} data-icon="inline-start" />
                Rethink
              </Button>
              <Button
                disabled={thinking || settling}
                onClick={() => void lockIn()}
                className="min-h-14 text-base"
              >
                <HugeiconsIcon icon={LockIcon} data-icon="inline-start" />
                Lock in
              </Button>
            </div>
          </section>
        )}
      </div>
      <aside className="flex flex-col gap-4 rounded-xl border bg-card p-4">
        <p
          role="status"
          className="flex min-h-6 items-center gap-2 font-medium text-primary"
        >
          {thinking ? (
            <>
              <HugeiconsIcon
                icon={Loading03Icon}
                className="size-4 animate-spin"
              />
              Opponent is thinking…
            </>
          ) : game.status === "finished" ? (
            "Game over"
          ) : myTurn ? (
            "Your move"
          ) : null}
        </p>
        {game.clock && (
          <div className="flex flex-col gap-2">
            {(["opponent", "player"] as const).map((who) => {
              const side =
                who === "player"
                  ? game.player_color
                  : game.player_color === "white"
                    ? "black"
                    : "white";
              return (
                <ClockDisplay
                  key={who}
                  label={who === "player" ? "Your clock" : "Opponent clock"}
                  remainingMs={
                    side === "white"
                      ? game.clock!.white_ms
                      : game.clock!.black_ms
                  }
                  running={game.clock!.running === side}
                />
              );
            })}
          </div>
        )}
        {connectionLost && (
          <p role="status" className="text-sm text-muted-foreground">
            Connection lost, reconnecting…
          </p>
        )}
        {message && (
          <p
            role="alert"
            className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm text-destructive"
          >
            {message}
          </p>
        )}
        <MoveList moves={game.moves} />
        {hasCoach && <CoachPanel messages={coachMessages} moves={game.moves} />}
        {game.status === "active" && game.mode === "passive" && (
          <Button
            variant="outline"
            disabled={thinking || game.moves.length === 0}
            onClick={() => void takeBack()}
          >
            <HugeiconsIcon icon={Undo02Icon} data-icon="inline-start" />
            Takeback
          </Button>
        )}
        {game.status === "active" &&
          (confirmingResign ? (
            <div className="flex gap-2">
              <Button variant="destructive" onClick={() => void resign()}>
                Confirm resign
              </Button>
              <Button
                variant="outline"
                onClick={() => setConfirmingResign(false)}
              >
                Keep playing
              </Button>
            </div>
          ) : (
            <Button variant="outline" onClick={() => setConfirmingResign(true)}>
              Resign
            </Button>
          ))}
      </aside>
      <PromotionDialog
        open={promotion !== null}
        onCancel={cancelPromotion}
        onChoose={(piece) => {
          if (promotion)
            void submitMove(`${promotion.from}${promotion.to}${piece}`);
          setPromotion(null);
        }}
      />
      {game.status === "finished" && game.result && (
        <GameOverDialog
          gameId={gameId}
          open={!dialogDismissed}
          result={game.result}
          reason={
            gameOverReason ?? (game.ended_on_time ? "timeout" : undefined)
          }
          playerColor={game.player_color}
          onClose={() => setDialogDismissed(true)}
        />
      )}
    </div>
  );
}
