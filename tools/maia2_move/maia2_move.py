# SPDX-License-Identifier: MIT
"""Long-lived Maia-2 move helper: JSON lines in, JSON lines out.

Request:  {"fen": "...", "elo": 1500}
Response: {"uci": "e2e4"} (sampled from Maia-2's move distribution) or {"error": "..."}

Run with the interpreter of the maia2 venv (see scripts/install_maia2.sh).
"""

import json
import os
import random
import sys

from maia2 import inference, model


def main() -> None:
    reply = sys.stdout
    sys.stdout = sys.stderr  # maia2 prints progress to stdout; keep that channel for replies only
    maia2_model = model.from_pretrained(
        type="rapid", device="cpu", save_root=os.path.expanduser("~/.cache/maia2")
    )
    prepared = inference.prepare()
    print(json.dumps({"ready": True}), file=reply, flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            probabilities, _ = inference.inference_each(
                maia2_model, prepared, request["fen"], int(request["elo"]), int(request["elo"])
            )
            moves = list(probabilities)
            uci = random.choices(moves, weights=list(probabilities.values()))[0]
            print(json.dumps({"uci": uci}), file=reply, flush=True)
        except Exception as error:  # report every failure to the caller instead of dying
            print(
                json.dumps({"error": f"{type(error).__name__}: {error}"}), file=reply, flush=True
            )


if __name__ == "__main__":
    main()
