from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import chess

from chess_tutor.openings.book import Opening, is_on_book_line, opening_index, position_key


@dataclass(frozen=True)
class OpeningStep:
    """The state after one ply; `eco`, `opening` and `variation` are the deepest name so far."""

    ply: int
    in_book: bool
    eco: str | None
    opening: str | None
    variation: str | None


@dataclass(frozen=True)
class BookExit:
    ply: int
    side: Literal["white", "black"]


@dataclass(frozen=True)
class OpeningTrack:
    line: list[OpeningStep]
    book_exit: BookExit | None


def _split(name: str) -> tuple[str, str | None]:
    opening, _, variation = name.partition(": ")
    return opening, variation or None


def track_opening(moves: Iterable[chess.Move]) -> OpeningTrack:
    """Compare each move of a game, played from the start, with the opening book.

    A ply is in book while its position lies on some book line (transpositions count). The book
    exit is the first ply whose position does not. Once the game has left the book it stays out:
    a later position that happens to transpose back onto a book line is not in book again.
    """
    index = opening_index()
    board = chess.Board()
    found: Opening | None = None
    book_exit: BookExit | None = None
    line: list[OpeningStep] = []
    for ply, move in enumerate(moves, start=1):
        mover: Literal["white", "black"] = "white" if board.turn == chess.WHITE else "black"
        board.push(move)
        if book_exit is None and not is_on_book_line(board):
            book_exit = BookExit(ply, mover)
        found = index.get(position_key(board), found)
        opening, variation = _split(found.name) if found else (None, None)
        line.append(
            OpeningStep(ply, book_exit is None, found.eco if found else None, opening, variation)
        )
    return OpeningTrack(line, book_exit)
