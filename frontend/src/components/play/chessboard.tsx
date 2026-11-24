"use client";

import { Chessground } from "chessground";
import type { Api } from "chessground/api";
import type { Key } from "chessground/types";
import { useEffect, useRef } from "react";

import "chessground/assets/chessground.base.css";
import "chessground/assets/chessground.cburnett.css";
import "./board-theme.css";

import type { Destinations } from "@/lib/chess-board";
import type { PlayerColor } from "@/lib/game-api";

export interface ChessboardProps {
  fen: string;
  orientation: PlayerColor;
  turnColor: PlayerColor;
  inCheck: boolean;
  lastMove?: [string, string];
  /** Squares the player may move to, or undefined when the board is locked. */
  destinations?: Destinations;
  /** An Active-mode move shown as an arrow without being played. */
  proposal?: [string, string];
  /** Bump to snap the pieces back to `fen` after a rejected move. */
  syncToken: number;
  onMove: (from: string, to: string) => void;
}

/** Thin chessground wrapper; needs real layout, so it is exercised in the browser, not jsdom. */
export function Chessboard({ fen, orientation, turnColor, inCheck, lastMove, destinations, proposal, syncToken, onMove }: ChessboardProps) {
  const container = useRef<HTMLDivElement>(null);
  const board = useRef<Api | null>(null);
  const onMoveRef = useRef(onMove);

  useEffect(() => {
    onMoveRef.current = onMove;
  });

  useEffect(() => {
    if (!container.current) return;
    const api = Chessground(container.current, {
      movable: { free: false, showDests: true, events: { after: (from, to) => onMoveRef.current(from, to) } },
      premovable: { enabled: false },
      draggable: { showGhost: true },
    });
    board.current = api;
    return () => {
      api.destroy();
      board.current = null;
    };
  }, []);

  useEffect(() => {
    board.current?.set({
      fen,
      orientation,
      turnColor,
      check: inCheck,
      lastMove: lastMove as [Key, Key] | undefined,
      movable: {
        color: destinations ? orientation : undefined,
        dests: (destinations ?? new Map()) as Map<Key, Key[]>,
      },
    });
  }, [fen, orientation, turnColor, inCheck, lastMove, destinations, syncToken]);

  useEffect(() => {
    board.current?.setAutoShapes(proposal ? [{ orig: proposal[0] as Key, dest: proposal[1] as Key, brush: "blue" }] : []);
  }, [proposal]);

  return <div ref={container} className="aspect-square w-full" data-testid="chessboard" />;
}
