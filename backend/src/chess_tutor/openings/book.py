import csv
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


@lru_cache(maxsize=1)
def opening_index(data_directory: Path = DATA_DIRECTORY) -> dict[str, Opening]:
    index: dict[str, Opening] = {}
    for table in sorted(data_directory.glob("*.tsv")):
        with table.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                board = chess.Board()
                for token in row["pgn"].split():
                    if not token.endswith("."):
                        board.push_san(token)
                index.setdefault(position_key(board), Opening(row["eco"], row["name"]))
    if not index:
        raise RuntimeError(f"no openings found in {data_directory}: the book data is missing")
    return index


def is_in_book(board: chess.Board) -> bool:
    return position_key(board) in opening_index()


def lookup_opening(board: chess.Board) -> Opening | None:
    """The deepest named opening along the game's moves, so the name survives leaving book."""
    index = opening_index()
    replay = board.root()
    found = index.get(position_key(replay))
    for move in board.move_stack:
        replay.push(move)
        found = index.get(position_key(replay), found)
    return found
