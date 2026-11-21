#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -v
python3 kit/check/check.py -- python3 -u bot.py
if [[ "${1:-}" == "--docker" ]]; then
  docker_cli="${DOCKER:-docker}"
  context="$PWD"
  arena_mount="$PWD/kit/arena"
  if [[ "$docker_cli" == *.exe ]]; then
    context="$(wslpath -w "$context")"
    arena_mount="$(wslpath -w "$arena_mount")"
  fi
  "$docker_cli" build --platform linux/amd64 -t mech-royale:phase1 "$context"
  python3 kit/check/check.py --map-dir /arena -- "$docker_cli" run --rm -i \
    --network none --cpus 1 --memory 512m --memory-swap 512m --read-only \
    --pids-limit 128 --cap-drop ALL --tmpfs /tmp:rw,size=64m \
    --user 65534:65534 -v "$arena_mount:/arena:ro" mech-royale:phase1
fi
