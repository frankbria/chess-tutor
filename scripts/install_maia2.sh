#!/usr/bin/env bash
# Installs Maia-2 (MIT) into an isolated venv, pinned to a commit.
# CPU-only torch keeps the install small; the model weights download from Google Drive on first run.
set -euo pipefail

MAIA2_COMMIT=67bee6ce7e2f264344fe6d2026a2e18c76a82efa

# MAIA2_TORCH_INDEX overrides the torch wheel index (a CUDA index URL, or empty for PyPI's default build).
TORCH_INDEX="${MAIA2_TORCH_INDEX-https://download.pytorch.org/whl/cpu}"
index_args=()
if [ -n "$TORCH_INDEX" ]; then
  index_args=(--index "$TORCH_INDEX" --index-strategy unsafe-best-match)
fi

# maia2 ships no executables, so `uv tool install` would discard it: use a plain venv instead.
MAIA2_VENV="${MAIA2_VENV:-$HOME/.local/share/chess-tutor/maia2-venv}"
uv venv --python 3.12 "$MAIA2_VENV"
uv pip install --python "$MAIA2_VENV/bin/python" "maia2 @ git+https://github.com/CSSLab/maia2@${MAIA2_COMMIT}" "${index_args[@]}"

echo "maia2 interpreter (used by tools/maia2_move): $MAIA2_VENV/bin/python"
