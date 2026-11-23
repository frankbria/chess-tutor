"use client";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

const CHOICES = [
  { piece: "q", label: "Queen", symbol: "♛" },
  { piece: "r", label: "Rook", symbol: "♜" },
  { piece: "b", label: "Bishop", symbol: "♝" },
  { piece: "n", label: "Knight", symbol: "♞" },
] as const;

export function PromotionDialog({
  open,
  onChoose,
  onCancel,
}: {
  open: boolean;
  onChoose: (piece: string) => void;
  onCancel: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onCancel()}>
      <DialogContent>
        <DialogTitle>Promote your pawn</DialogTitle>
        <DialogDescription>Choose the piece it becomes.</DialogDescription>
        <div className="grid grid-cols-4 gap-2">
          {CHOICES.map(({ piece, label, symbol }) => (
            <Button key={piece} variant="outline" className="h-20 flex-col" onClick={() => onChoose(piece)}>
              <span aria-hidden className="text-3xl">{symbol}</span>
              {label}
            </Button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
