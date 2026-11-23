"use client";

import { Loading03Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Chessboard } from "@/components/play/chessboard";
import { GameOverDialog } from "@/components/play/game-over-dialog";
import { MoveList } from "@/components/play/move-list";
import { PromotionDialog } from "@/components/play/promotion-dialog";
import { destinationsFrom, isInCheck, isPromotion, lastMoveSquares } from "@/lib/chess-board";
import { ApiError, httpGameApi, type GameApi, type GameState } from "@/lib/game-api";

const errorMessage = (error: unknown) =>
  error instanceof ApiError ? error.message : "Could not reach the game server. Try again.";

export function PlayBoard({ gameId, api = httpGameApi }: { gameId: number; api?: GameApi }) {
  const [game, setGame] = useState<GameState | null>(null);
  const [thinking, setThinking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [promotion, setPromotion] = useState<{ from: string; to: string } | null>(null);
  const [gameOverReason, setGameOverReason] = useState<string>();
  const [dialogDismissed, setDialogDismissed] = useState(false);
  const [confirmingResign, setConfirmingResign] = useState(false);
  const [syncToken, setSyncToken] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [connectionLost, setConnectionLost] = useState(false);
  const plyCount = useRef(0);

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
    api.getGame(gameId).then(accept, (error: unknown) => setLoadError(errorMessage(error)));
    return api.subscribe(gameId, {
      onSnapshot: () => {
        setConnectionLost(false);
        void resync();
      },
      onOpponentMove: () => {
        setConnectionLost(false);
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

  const submitMove = async (uci: string) => {
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

  if (!game && loadError) {
    return (
      <div role="alert" className="flex flex-col items-start gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-4 text-destructive">
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

  const myTurn = game.status === "active" && game.side_to_move === game.player_color && !thinking;
  const handleMove = (from: string, to: string) => {
    if (isPromotion(game.legal_moves, from, to)) setPromotion({ from, to });
    else void submitMove(`${from}${to}`);
  };
  const cancelPromotion = () => {
    setPromotion(null);
    setSyncToken((token) => token + 1);
  };

  return (
    <div className="grid gap-4 md:grid-cols-[minmax(0,40rem)_minmax(16rem,1fr)]">
      <Chessboard
        fen={game.fen}
        orientation={game.player_color}
        turnColor={game.side_to_move}
        inCheck={isInCheck(game.fen)}
        lastMove={lastMoveSquares(game.moves)}
        destinations={myTurn ? destinationsFrom(game.legal_moves) : undefined}
        syncToken={syncToken}
        onMove={handleMove}
      />
      <aside className="flex flex-col gap-4 rounded-xl border bg-card p-4">
        <p role="status" className="flex min-h-6 items-center gap-2 font-medium text-primary">
          {thinking ? (
            <>
              <HugeiconsIcon icon={Loading03Icon} className="size-4 animate-spin" />
              Opponent is thinking…
            </>
          ) : game.status === "finished" ? (
            "Game over"
          ) : myTurn ? (
            "Your move"
          ) : null}
        </p>
        {connectionLost && <p role="status" className="text-sm text-muted-foreground">Connection lost, reconnecting…</p>}
        {message && (
          <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm text-destructive">
            {message}
          </p>
        )}
        <MoveList moves={game.moves} />
        {game.status === "active" &&
          (confirmingResign ? (
            <div className="flex gap-2">
              <Button variant="destructive" onClick={() => void resign()}>Confirm resign</Button>
              <Button variant="outline" onClick={() => setConfirmingResign(false)}>Keep playing</Button>
            </div>
          ) : (
            <Button variant="outline" onClick={() => setConfirmingResign(true)}>Resign</Button>
          ))}
      </aside>
      <PromotionDialog
        open={promotion !== null}
        onCancel={cancelPromotion}
        onChoose={(piece) => {
          if (promotion) void submitMove(`${promotion.from}${promotion.to}${piece}`);
          setPromotion(null);
        }}
      />
      {game.status === "finished" && game.result && (
        <GameOverDialog
          open={!dialogDismissed}
          result={game.result}
          reason={gameOverReason}
          playerColor={game.player_color}
          onClose={() => setDialogDismissed(true)}
        />
      )}
    </div>
  );
}
