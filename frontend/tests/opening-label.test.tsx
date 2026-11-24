import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { OpeningLabel } from "@/components/play/opening-label";
import type { OpeningStep } from "@/lib/game-api";

const NAJDORF: OpeningStep = {
  ply: 10,
  in_book: true,
  eco: "B90",
  opening: "Sicilian Defense",
  variation: "Najdorf Variation",
};

const MOVES = ["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6"];

describe("OpeningLabel", () => {
  it("shows the code, opening and variation", () => {
    render(<OpeningLabel step={NAJDORF} bookExit={null} moves={[]} playerColor="white" />);
    expect(screen.getByTestId("opening-label")).toHaveTextContent(
      "B90 · Sicilian Defense · Najdorf Variation",
    );
    expect(screen.queryByText(/Left book/)).not.toBeInTheDocument();
  });

  it("leaves out a variation the line does not have", () => {
    const step = { ...NAJDORF, eco: "B20", variation: null };
    render(<OpeningLabel step={step} bookExit={null} moves={[]} playerColor="white" />);
    expect(screen.getByTestId("opening-label")).toHaveTextContent(/^B20 · Sicilian Defense$/);
  });

  it("says who left the book, as you or the opponent", () => {
    const exit = { ply: 6, side: "black" } as const;
    const { rerender } = render(
      <OpeningLabel step={undefined} bookExit={exit} moves={MOVES} playerColor="white" />,
    );
    expect(screen.getByText("Left book at 3... Nf6 (opponent)")).toBeInTheDocument();

    rerender(<OpeningLabel step={undefined} bookExit={exit} moves={MOVES} playerColor="black" />);
    expect(screen.getByText("Left book at 3... Nf6 (you)")).toBeInTheDocument();
  });

  it("names a white exit with the move number and a single dot", () => {
    render(
      <OpeningLabel
        step={undefined}
        bookExit={{ ply: 5, side: "white" }}
        moves={MOVES}
        playerColor="white"
      />,
    );
    expect(screen.getByText("Left book at 3. Bc4 (you)")).toBeInTheDocument();
  });

  it("renders nothing before there is anything to say", () => {
    const { container } = render(
      <OpeningLabel step={undefined} bookExit={null} moves={[]} playerColor="white" />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
