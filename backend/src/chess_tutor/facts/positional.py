"""Positional detectors: king safety, pawn structure, development and activity.

Each detector emits only notable facts so the coach is not flooded.
"""

from collections.abc import Callable

import chess

from chess_tutor.facts.detectors import piece_ref, side_of
from chess_tutor.facts.models import Fact, FactType, Side
from chess_tutor.openings.book import is_on_book_line
from chess_tutor.settings import PhaseThresholds

COLORS = (chess.WHITE, chess.BLACK)
CENTRE = (chess.D4, chess.E4, chess.D5, chess.E5)
HOME_MINORS = {
    chess.WHITE: (chess.B1, chess.G1, chess.C1, chess.F1),
    chess.BLACK: (chess.B8, chess.G8, chess.C8, chess.F8),
}
HOME_QUEEN = {chess.WHITE: chess.D1, chess.BLACK: chess.D8}
UNCASTLED_FROM_MOVE = 10
UNDEVELOPED_FROM_MOVE = 5
MIN_UNDEVELOPED_MINORS = 2
CENTRE_CONTROL_LEAD = 3
BAD_BISHOP_PAWNS = 5
PASSIVE_KING_DISTANCE = 3


def _name(color: chess.Color) -> str:
    return "White" if color == chess.WHITE else "Black"


def _relative_rank(color: chess.Color, square: chess.Square) -> int:
    """Rank counted from the owner's side, 1 to 8."""
    rank = chess.square_rank(square)
    return (rank if color == chess.WHITE else 7 - rank) + 1


def _non_pawn_material(board: chess.Board) -> int:
    values = {chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
    return sum(values.get(piece.piece_type, 0) for piece in board.piece_map().values())


def _is_opening(board: chess.Board, thresholds: PhaseThresholds) -> bool:
    return board.fullmove_number <= thresholds.opening_max_move and not _is_endgame(
        board, thresholds
    )


def _is_endgame(board: chess.Board, thresholds: PhaseThresholds) -> bool:
    return _non_pawn_material(board) <= thresholds.endgame_max_non_pawn_material


def _fact(
    board: chess.Board,
    fact_id: str,
    fact_type: FactType,
    side: Side,
    summary: str,
    squares: list[chess.Square],
    severity: int = 1,
) -> Fact:
    return Fact(
        id=fact_id,
        type=fact_type,
        side=side,
        summary=summary,
        severity=severity,
        squares=[chess.square_name(square) for square in squares],
        pieces=[piece_ref(board, square) for square in squares],
    )


def _pawns(board: chess.Board, color: chess.Color) -> list[chess.Square]:
    return sorted(board.pieces(chess.PAWN, color))


def _file_pawns(board: chess.Board, color: chess.Color, file: int) -> list[chess.Square]:
    return [square for square in _pawns(board, color) if chess.square_file(square) == file]


def _adjacent_files(file: int) -> list[int]:
    return [other for other in (file - 1, file + 1) if 0 <= other <= 7]


def _has_heavy_pieces(board: chess.Board, color: chess.Color) -> bool:
    return bool(board.pieces(chess.ROOK, color) | board.pieces(chess.QUEEN, color))


def _centre_is_open(board: chess.Board) -> bool:
    return any(
        not _file_pawns(board, chess.WHITE, file) and not _file_pawns(board, chess.BLACK, file)
        for file in (chess.square_file(chess.D4), chess.square_file(chess.E4))
    )


def _king_issues(board: chess.Board, color: chess.Color, thresholds: PhaseThresholds) -> list[str]:
    king = board.king(color)
    assert king is not None
    back_rank = 0 if color == chess.WHITE else 7
    on_back_rank = chess.square_rank(king) == back_rank
    castled = on_back_rank and chess.square_file(king) in (chess.square_file(chess.G1), 2)
    king_file = chess.square_file(king)
    issues: list[str] = []
    if (
        not castled
        and board.fullmove_number >= UNCASTLED_FROM_MOVE
        and on_back_rank
        and chess.square_file(chess.D1) <= king_file <= chess.square_file(chess.F1)
        and _centre_is_open(board)
    ):
        issues.append("the king is still uncastled in the centre with the centre open")
    if castled:
        shield_files = [king_file, *_adjacent_files(king_file)]
        missing = [
            chess.FILE_NAMES[file]
            for file in shield_files
            if not any(
                1 <= _relative_rank(color, pawn) - _relative_rank(color, king) <= 2
                for pawn in _file_pawns(board, color, file)
            )
        ]
        if missing:
            issues.append(f"the pawn shield has no pawn on the {', '.join(missing)}-file")
    enemy_has_heavy_pieces = _has_heavy_pieces(board, not color)
    if enemy_has_heavy_pieces:
        for file in [king_file, *_adjacent_files(king_file)]:
            if not _file_pawns(board, color, file):
                kind = "open" if not _file_pawns(board, not color, file) else "half-open"
                issues.append(f"the {chess.FILE_NAMES[file]}-file is {kind} next to the king")
    return issues


def king_safety(board: chess.Board, thresholds: PhaseThresholds | None = None) -> list[Fact]:
    thresholds = thresholds or PhaseThresholds()
    if _is_endgame(board, thresholds):
        return []
    facts: list[Fact] = []
    for color in COLORS:
        issues = _king_issues(board, color, thresholds)
        if issues:
            king = board.king(color)
            assert king is not None
            side = side_of(color)
            facts.append(
                _fact(
                    board,
                    f"king_safety:{side.value}",
                    FactType.KING_SAFETY,
                    side,
                    f"{_name(color)}: {'; '.join(issues)}",
                    [king],
                    len(issues),
                )
            )
    return facts


def _is_passed(board: chess.Board, color: chess.Color, pawn: chess.Square) -> bool:
    file = chess.square_file(pawn)
    rank = _relative_rank(color, pawn)
    return not any(
        _relative_rank(color, enemy) > rank
        for other_file in [file, *_adjacent_files(file)]
        for enemy in _file_pawns(board, not color, other_file)
    )


def _is_isolated(board: chess.Board, color: chess.Color, pawn: chess.Square) -> bool:
    return not any(
        _file_pawns(board, color, other) for other in _adjacent_files(chess.square_file(pawn))
    )


def _is_backward(board: chess.Board, color: chess.Color, pawn: chess.Square) -> bool:
    file = chess.square_file(pawn)
    rank = _relative_rank(color, pawn)
    if _is_isolated(board, color, pawn):
        return False
    # A friendly pawn level or behind on an adjacent file can advance to support it.
    if any(
        _relative_rank(color, friend) <= rank
        for other in _adjacent_files(file)
        for friend in _file_pawns(board, color, other)
    ):
        return False
    stop_square = pawn + (8 if color == chess.WHITE else -8)
    return any(
        board.piece_type_at(attacker) == chess.PAWN
        for attacker in board.attackers(not color, stop_square)
    )


def pawn_structure(board: chess.Board) -> list[Fact]:
    facts: list[Fact] = []
    for color in COLORS:
        side = side_of(color)
        name = _name(color)
        for file in range(8):
            pawns = _file_pawns(board, color, file)
            file_name = chess.FILE_NAMES[file]
            if len(pawns) >= 2:
                facts.append(
                    _fact(
                        board,
                        f"pawns:{side.value}:doubled:{file_name}",
                        FactType.PAWN_STRUCTURE,
                        side,
                        f"{name} has doubled pawns on the {file_name}-file",
                        pawns,
                    )
                )
        for pawn in _pawns(board, color):
            square_name = chess.square_name(pawn)
            rank = _relative_rank(color, pawn)
            checks: list[tuple[str, bool, int, str]] = [
                ("isolated", _is_isolated(board, color, pawn), 1, "an isolated pawn"),
                ("backward", _is_backward(board, color, pawn), 1, "a backward pawn"),
                (
                    "passed",
                    _is_passed(board, color, pawn),
                    rank - 1,
                    f"a passed pawn on its rank {rank}",
                ),
            ]
            for kind, applies, severity, description in checks:
                if applies:
                    facts.append(
                        _fact(
                            board,
                            f"pawns:{side.value}:{kind}:{square_name}",
                            FactType.PAWN_STRUCTURE,
                            side,
                            f"{name} has {description} on {square_name}",
                            [pawn],
                            severity,
                        )
                    )
    return facts


def _repeated_piece_moves(board: chess.Board, color: chess.Color) -> list[chess.Square]:
    """Squares of knights, bishops and queens of `color` that have moved more than once."""
    replay = board.root()
    move_counts: dict[chess.Square, int] = {}
    for move in board.move_stack:
        mover = replay.color_at(move.from_square)
        count = move_counts.pop(move.from_square, 0) + 1
        replay.push(move)
        if mover == color:
            # A repeat that stays in book is theory (Ruy Lopez Bb5-a4), so it does not count.
            move_counts[move.to_square] = 1 if is_on_book_line(replay) else count
        else:
            move_counts.pop(move.to_square, None)
    return sorted(
        square
        for square, count in move_counts.items()
        if count >= 2 and board.piece_type_at(square) in (chess.KNIGHT, chess.BISHOP, chess.QUEEN)
    )


def _undeveloped_minors(board: chess.Board, color: chess.Color) -> list[chess.Square]:
    return [
        square
        for square in HOME_MINORS[color]
        if (piece := board.piece_at(square)) is not None
        and piece.color == color
        and piece.piece_type in (chess.KNIGHT, chess.BISHOP)
    ]


def _centre_attacks(board: chess.Board, color: chess.Color) -> int:
    return sum(len(board.attackers(color, square)) for square in CENTRE)


def development(board: chess.Board, thresholds: PhaseThresholds | None = None) -> list[Fact]:
    thresholds = thresholds or PhaseThresholds()
    if not _is_opening(board, thresholds):
        return []
    facts: list[Fact] = []
    for color in COLORS:
        side = side_of(color)
        undeveloped = _undeveloped_minors(board, color)
        if len(undeveloped) < MIN_UNDEVELOPED_MINORS:
            continue
        issues: list[str] = []
        squares: list[chess.Square] = []
        if board.fullmove_number >= UNDEVELOPED_FROM_MOVE:
            squares += undeveloped
            names = ", ".join(chess.square_name(square) for square in undeveloped)
            issues.append(f"minor pieces still undeveloped on {names}")
        queen = board.pieces(chess.QUEEN, color)
        if queen and HOME_QUEEN[color] not in queen:
            squares += sorted(queen)
            issues.append("the queen came out before the minor pieces")
        repeated = _repeated_piece_moves(board, color)
        if repeated:
            squares += repeated
            names = ", ".join(chess.square_name(square) for square in repeated)
            issues.append(f"the piece on {names} has moved more than once")
        if issues:
            facts.append(
                _fact(
                    board,
                    f"development:{side.value}",
                    FactType.DEVELOPMENT,
                    side,
                    f"{_name(color)}: {'; '.join(issues)}",
                    list(dict.fromkeys(squares)),
                    len(issues),
                )
            )
    white_control = _centre_attacks(board, chess.WHITE)
    black_control = _centre_attacks(board, chess.BLACK)
    lead = white_control - black_control
    if abs(lead) >= CENTRE_CONTROL_LEAD:
        leader = chess.WHITE if lead > 0 else chess.BLACK
        centre_names = ", ".join(chess.square_name(square) for square in CENTRE)
        facts.append(
            _fact(
                board,
                "development:centre",
                FactType.DEVELOPMENT,
                side_of(leader),
                f"{_name(leader)} controls the centre squares {centre_names} "
                f"{max(white_control, black_control)} to {min(white_control, black_control)}",
                [],
                abs(lead),
            )
        )
    return facts


def _rook_facts(board: chess.Board, color: chess.Color) -> list[Fact]:
    side = side_of(color)
    facts: list[Fact] = []
    enemy_king = board.king(not color)
    assert enemy_king is not None
    for rook in sorted(board.pieces(chess.ROOK, color)):
        file = chess.square_file(rook)
        square_name = chess.square_name(rook)
        label = f"{_name(color)} rook on {square_name}"
        own, enemy = _file_pawns(board, color, file), _file_pawns(board, not color, file)
        if not own:
            kind = "open" if not enemy else "half-open"
            facts.append(
                _fact(
                    board,
                    f"activity:{side.value}:rook_file:{square_name}",
                    FactType.ACTIVITY,
                    side,
                    f"{label} is on the {kind} {chess.FILE_NAMES[file]}-file",
                    [rook],
                    2 if kind == "open" else 1,
                )
            )
        enemy_back_rank_pawns = [
            pawn for pawn in _pawns(board, not color) if _relative_rank(color, pawn) == 7
        ]
        if _relative_rank(color, rook) == 7 and (
            enemy_back_rank_pawns or _relative_rank(color, enemy_king) == 8
        ):
            facts.append(
                _fact(
                    board,
                    f"activity:{side.value}:rook_seventh:{square_name}",
                    FactType.ACTIVITY,
                    side,
                    f"{label} is on the seventh rank",
                    [rook],
                    2,
                )
            )
    return facts


def _bad_bishop_facts(board: chess.Board, color: chess.Color) -> list[Fact]:
    side = side_of(color)
    facts: list[Fact] = []
    for bishop in sorted(board.pieces(chess.BISHOP, color)):
        light = bool(chess.BB_LIGHT_SQUARES & chess.BB_SQUARES[bishop])
        same_colour_pawns = sum(
            bool(chess.BB_LIGHT_SQUARES & chess.BB_SQUARES[pawn]) == light
            for pawn in _pawns(board, color)
        )
        if same_colour_pawns >= BAD_BISHOP_PAWNS:
            square_name = chess.square_name(bishop)
            facts.append(
                _fact(
                    board,
                    f"activity:{side.value}:bad_bishop:{square_name}",
                    FactType.ACTIVITY,
                    side,
                    f"{_name(color)} bishop on {square_name} is hemmed in by "
                    f"{same_colour_pawns} own pawns on its colour",
                    [bishop],
                    same_colour_pawns - BAD_BISHOP_PAWNS + 1,
                )
            )
    return facts


def _passive_king_facts(board: chess.Board, color: chess.Color) -> list[Fact]:
    king = board.king(color)
    assert king is not None
    distance = min(chess.square_distance(king, square) for square in CENTRE)
    if distance < PASSIVE_KING_DISTANCE:
        return []
    side = side_of(color)
    return [
        _fact(
            board,
            f"activity:{side.value}:passive_king",
            FactType.ACTIVITY,
            side,
            f"{_name(color)} king on {chess.square_name(king)} is {distance} steps from the centre",
            [king],
            distance - PASSIVE_KING_DISTANCE + 1,
        )
    ]


def activity(board: chess.Board, thresholds: PhaseThresholds | None = None) -> list[Fact]:
    thresholds = thresholds or PhaseThresholds()
    if _is_opening(board, thresholds):
        return []
    detectors: list[Callable[[chess.Board, chess.Color], list[Fact]]] = [
        _rook_facts,
        _bad_bishop_facts,
    ]
    if _is_endgame(board, thresholds):
        detectors.append(_passive_king_facts)
    return [fact for color in COLORS for detector in detectors for fact in detector(board, color)]


def positional_facts(board: chess.Board) -> list[Fact]:
    return [*king_safety(board), *pawn_structure(board), *development(board), *activity(board)]
