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
    return board.epd()  # EPD omits the move counters, so transpositions match


@lru_cache(maxsize=1)
def opening_index() -> dict[str, Opening]:
    index: dict[str, Opening] = {}
    for table in sorted(DATA_DIRECTORY.glob("*.tsv")):
        with table.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                board = chess.Board()
                for token in row["pgn"].split():
                    if not token.endswith("."):
                        board.push_san(token)
                index.setdefault(position_key(board), Opening(row["eco"], row["name"]))
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
