import time

import chess

from chess_tutor.openings.tracking import BookExit, OpeningStep, track_opening


def moves_from(san_moves: str) -> list[chess.Move]:
    board = chess.Board()
    return [board.push_san(san) for san in san_moves.split()]


NAJDORF = "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6"


def test_the_najdorf_line_is_identified_with_its_eco_at_each_ply() -> None:
    track = track_opening(moves_from(NAJDORF))

    assert [(step.ply, step.eco) for step in track.line] == [
        (1, "B00"),
        (2, "B20"),
        (3, "B27"),
        (4, "B50"),
        (5, "B50"),
        (6, "B50"),
        (7, "B50"),  # Nxd4 has no entry of its own, so the opening so far carries over
        (8, "B54"),
        (9, "B54"),
        (10, "B90"),
    ]
    assert all(step.in_book for step in track.line)
    assert track.book_exit is None
    assert track.line[-1] == OpeningStep(10, True, "B90", "Sicilian Defense", "Najdorf Variation")


def test_the_name_is_split_into_opening_and_variation_at_the_first_colon() -> None:
    track = track_opening(moves_from("e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6"))

    assert (track.line[1].opening, track.line[1].variation) == ("Sicilian Defense", None)
    assert (track.line[7].opening, track.line[7].variation) == (
        "Sicilian Defense",
        "Modern Variations, Main Line",
    )


def test_an_early_deviation_reports_its_ply_and_side() -> None:
    track = track_opening(moves_from("e4 e5 Qh5 Nc6 Bc4 Nf6"))

    assert track.book_exit == BookExit(ply=6, side="black")
    assert [step.in_book for step in track.line] == [True] * 5 + [False]


def test_a_white_deviation_names_white() -> None:
    track = track_opening(moves_from("e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 h3 h6 a3"))

    assert track.book_exit is not None and track.book_exit.side == "white"
    assert track.book_exit.ply % 2 == 1


def test_the_opening_name_survives_leaving_book() -> None:
    track = track_opening(moves_from("e4 e5 Qh5 Nc6 Bc4 Nf6"))

    assert (track.line[4].eco, track.line[4].opening, track.line[4].variation) == (
        "C20",
        "King's Pawn Game",
        "Wayward Queen Attack",
    )
    assert (track.line[5].eco, track.line[5].in_book) == ("C20", False)


def test_a_position_reached_by_transposition_is_still_in_book() -> None:
    track = track_opening(moves_from("Nf3 d5 d4 Nf6 c4"))

    assert track.book_exit is None and track.line[-1].in_book


def test_a_game_stays_out_of_book_after_a_later_transposition_back() -> None:
    # Ply 2 leaves every line; the position after 3.e4 is on a book line again.
    track = track_opening(moves_from("Nh3 Nh6 Ng1 Ng8 e4"))

    assert track.book_exit == BookExit(ply=2, side="black")
    assert [step.in_book for step in track.line] == [True, False, False, False, False]


def test_an_empty_game_has_no_line_and_no_exit() -> None:
    track = track_opening([])

    assert track.line == [] and track.book_exit is None


def test_a_transposition_after_leaving_book_keeps_the_last_in_book_name() -> None:
    track = track_opening(moves_from("e4 e5 Nf3 Nc6 Ng1 Nb8"))

    assert track.book_exit == BookExit(5, "white")
    last_in_book = track.line[3]
    assert last_in_book.eco == "C44"
    assert [(step.eco, step.opening, step.variation) for step in track.line[4:]] == [
        (last_in_book.eco, last_in_book.opening, last_in_book.variation)
    ] * 2


def test_a_forty_move_game_is_tracked_quickly() -> None:
    moves = moves_from("e4 e5 Nf3 Nc6 " + "Ng1 Nb8 Nf3 Nc6 " * 19)
    assert len(moves) == 80
    track_opening(moves)  # warm the cached book

    started = time.perf_counter()
    track_opening(moves)

    assert time.perf_counter() - started < 0.05
