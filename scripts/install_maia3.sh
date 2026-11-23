#!/usr/bin/env bash
# Installs the Maia-3 UCI engine (AGPL-3) as an isolated uv tool, pinned to a commit.
# CPU-only torch keeps the install small; model weights download from Hugging Face on first run.
set -euo pipefail

MAIA3_COMMIT=1e13597c42d4858b7cfd7cfdae01e297263364b2

# MAIA3_TORCH_INDEX overrides the torch wheel index (a CUDA index URL, or empty for PyPI's default build).
TORCH_INDEX="${MAIA3_TORCH_INDEX-https://download.pytorch.org/whl/cpu}"
index_args=()
if [ -n "$TORCH_INDEX" ]; then
  index_args=(--index "$TORCH_INDEX" --index-strategy unsafe-best-match)
fi

uv tool install --force "maia3 @ git+https://github.com/CSSLab/maia3@${MAIA3_COMMIT}" "${index_args[@]}"

echo "maia3 tool interpreter (used by tools/maia3_policy): $(uv tool dir)/maia3/bin/python"
