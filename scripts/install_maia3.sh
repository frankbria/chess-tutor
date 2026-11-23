#!/usr/bin/env bash
# Installs the Maia-3 UCI engine (AGPL-3) as an isolated uv tool, pinned to a commit.
# CPU-only torch keeps the install small; model weights download from Hugging Face on first run.
set -euo pipefail

MAIA3_COMMIT=1e13597c42d4858b7cfd7cfdae01e297263364b2

uv tool install --force "maia3 @ git+https://github.com/CSSLab/maia3@${MAIA3_COMMIT}" \
  --index https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match
