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
                if keys:  # a blank pgn cell would otherwise break every lookup
                    yield Opening(row["eco"], row["name"]), keys


class Book(NamedTuple):
    named_positions: dict[str, Opening]  # position key at the end of a named line -> its opening
    line_positions: frozenset[str]  # every position along any book line


@lru_cache(maxsize=1)
def load_book(data_directory: Path) -> Book:
    """Parse the book once; the index and the line positions come from the same pass."""
    index: dict[str, Opening] = {}
    line_positions: set[str] = set()
    for opening, keys in _book_lines(data_directory):
        index.setdefault(keys[-1], opening)
        line_positions.update(keys)
    if not index:
        raise RuntimeError(f"no openings found in {data_directory}: the book data is missing")
    return Book(index, frozenset(line_positions))


def opening_index(data_directory: Path = DATA_DIRECTORY) -> dict[str, Opening]:
    return load_book(data_directory).named_positions


def is_in_book(board: chess.Board) -> bool:
    return position_key(board) in opening_index()


def is_on_book_line(board: chess.Board) -> bool:
    """True when the position is any step of a book line, not only the end of a named entry."""
    return position_key(board) in load_book(DATA_DIRECTORY).line_positions


def lookup_opening(board: chess.Board) -> Opening | None:
    """The deepest named opening along the game's moves, so the name survives leaving book."""
    index = opening_index()
    replay = board.root()
    found = index.get(position_key(replay))
    for move in board.move_stack:
        replay.push(move)
        found = index.get(position_key(replay), found)
    return found
