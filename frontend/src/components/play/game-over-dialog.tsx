"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { describeResult } from "@/lib/chess-board";
import type { GameResult, PlayerColor } from "@/lib/game-api";

const HEADINGS = { won: "You won!", lost: "You lost", drawn: "Draw" } as const;

export function GameOverDialog({
  gameId,
  open,
  result,
  reason,
  playerColor,
  onClose,
}: {
  gameId: number;
  open: boolean;
  result: GameResult;
  reason?: string;
  playerColor: PlayerColor;
  onClose: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogTitle>{HEADINGS[describeResult(result, playerColor)]}</DialogTitle>
        <DialogDescription>
          {result}
          {reason === "timeout" ? " on time" : reason ? ` by ${reason.replaceAll("_", " ")}` : ""}
        </DialogDescription>
        <Button asChild>
          <Link href={`/review/${gameId}`}>Review game</Link>
        </Button>
        <DialogClose asChild>
          <Button variant="outline">Close</Button>
        </DialogClose>
      </DialogContent>
    </Dialog>
  );
}
