"use client";

import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { describeResult } from "@/lib/chess-board";
import type { GameResult, PlayerColor } from "@/lib/game-api";

const HEADINGS = { won: "You won!", lost: "You lost", drawn: "Draw" } as const;

export function GameOverDialog({
  open,
  result,
  reason,
  playerColor,
  onClose,
}: {
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
          {reason ? ` by ${reason.replaceAll("_", " ")}` : ""}
        </DialogDescription>
        <DialogClose asChild>
          <Button>Close</Button>
        </DialogClose>
      </DialogContent>
    </Dialog>
  );
}
