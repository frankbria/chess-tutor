import csv
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

import chess

DATA_DIRECTORY = Path(__file__).parent / "data"


class Opening(NamedTuple):
    eco: str
    name: str


def position_key(board: chess.Board) -> str:
    # EPD omits the move counters, so transpositions match; index and lookup both use epd(),
    # so the en-passant field is encoded the same way on both sides.
    return board.epd()


def _book_lines(data_directory: Path) -> Iterator[tuple[Opening, list[str]]]:
    """Each book line's opening and the position key after each of its moves."""
    for table in sorted(data_directory.glob("*.tsv")):
        with table.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                board = chess.Board()
                keys: list[str] = []
                for token in row["pgn"].split():
                    if not token.endswith("."):
                        board.push_san(token)
                        keys.append(position_key(board))
                yield Opening(row["eco"], row["name"]), keys


@lru_cache(maxsize=1)
def opening_index(data_directory: Path = DATA_DIRECTORY) -> dict[str, Opening]:
    index: dict[str, Opening] = {}
    for opening, keys in _book_lines(data_directory):
        index.setdefault(keys[-1], opening)
    if not index:
        raise RuntimeError(f"no openings found in {data_directory}: the book data is missing")
    return index


@lru_cache(maxsize=1)
def _line_positions(data_directory: Path = DATA_DIRECTORY) -> frozenset[str]:
    return frozenset(key for _, keys in _book_lines(data_directory) for key in keys)


def is_in_book(board: chess.Board) -> bool:
    return position_key(board) in opening_index()


def is_on_book_line(board: chess.Board) -> bool:
    """True when the position is any step of a book line, not only the end of a named entry."""
    return position_key(board) in _line_positions()


def lookup_opening(board: chess.Board) -> Opening | None:
    """The deepest named opening along the game's moves, so the name survives leaving book."""
    index = opening_index()
    replay = board.root()
    found = index.get(position_key(replay))
    for move in board.move_stack:
        replay.push(move)
        found = index.get(position_key(replay), found)
    return found
