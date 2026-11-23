# maia3_policy

Out-of-process helper that prints Maia-3's move probabilities (softmax over legal moves), which `maia3-uci` computes but never emits. It imports `maia3`, so it is licensed `AGPL-3.0-or-later` and lives apart from the backend, which talks to it only over JSON lines on stdin/stdout. Run it with the Python interpreter of the maia3 uv tool (`<uv tool dir>/maia3/bin/python`), for example `python maia3_policy.py --model 5m --device cpu`; it prints `{"ready": true}` once the model is loaded.
