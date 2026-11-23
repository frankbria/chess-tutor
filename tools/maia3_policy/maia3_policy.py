# SPDX-License-Identifier: AGPL-3.0-or-later
"""Long-lived Maia-3 policy helper: JSON lines in, JSON lines out.

Request:  {"fen": "...", "elo": 1500, "k": 5}
Response: {"moves": [{"uci": "e2e4", "probability": 0.31}, ...]} or {"error": "..."}

Run with the Python interpreter of the maia3 uv tool (see scripts/install_maia3.sh).
"""

import json
import sys

import chess
import torch
from maia3.dataset import get_legal_moves_mask
from maia3.uci import Maia3UCIEngine, parse_args


def legal_move_probabilities(engine: Maia3UCIEngine, fen: str, elo: int, k: int) -> list[dict]:
    board = chess.Board(fen)
    if board.is_game_over():
        raise ValueError("the game is over")
    engine.board = board
    engine.self_elo = engine.oppo_elo = elo
    engine._reset_history()
    tokens = engine._tokens_from_history(engine.history).unsqueeze(0).to(engine.cfg.device)
    elos = torch.tensor([elo], dtype=torch.long, device=engine.cfg.device)
    with torch.no_grad():
        logits_move, _, _ = engine.model(tokens, elos, elos)
    mask = get_legal_moves_mask(board, engine.all_moves_dict).to(engine.cfg.device)
    probabilities = torch.softmax(logits_move[0].float().masked_fill(~mask, float("-inf")), dim=-1)
    top_probabilities, top_indices = torch.topk(probabilities, k=min(k, int(mask.sum())))
    moves = []
    for probability, index in zip(top_probabilities.tolist(), top_indices.tolist()):
        move = engine._move_from_index(index)
        if move is not None:
            moves.append({"uci": move.uci(), "probability": probability})
    return moves


def main() -> None:
    engine = Maia3UCIEngine(parse_args(sys.argv[1:]))
    engine.ensure_model_loaded()
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            moves = legal_move_probabilities(
                engine, request["fen"], int(request["elo"]), int(request["k"])
            )
            print(json.dumps({"moves": moves}), flush=True)
        except Exception as error:  # report every failure to the caller instead of dying
            print(json.dumps({"error": f"{type(error).__name__}: {error}"}), flush=True)


if __name__ == "__main__":
    main()
