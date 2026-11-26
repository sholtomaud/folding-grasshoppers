#!/usr/bin/env bash
# Run a command that needs Python, uv or Node.
#
#   scripts/tools.sh <command> [args...]
#
# On a Mac without uv on PATH (the normal developer setup) the command runs in
# the dev container (Containerfile) through Apple's `container` CLI, with the
# repo mounted at /work. Where uv is already installed (GitHub Actions, Linux,
# inside the container itself) it runs directly.
#
#   CARDBOARD_TOOLS=container|host   force one or the other
#   CARDBOARD_IMAGE=cardboardcad-dev image tag (built on first use)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${CARDBOARD_IMAGE:-cardboardcad-dev}"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
[ "$#" -gt 0 ] || die "usage: scripts/tools.sh <command> [args...]"

mode="${CARDBOARD_TOOLS:-}"
[ "${CARDBOARD_IN_CONTAINER:-0}" = "1" ] && mode=host
if [ -z "$mode" ]; then
  if command -v uv >/dev/null 2>&1; then
    mode=host
  elif command -v container >/dev/null 2>&1; then
    mode=container
  else
    die "need either Apple's container CLI (https://github.com/apple/container) or uv on PATH"
  fi
fi

case "$mode" in
  host)
    cd "$ROOT"
    exec "$@"
    ;;
  container)
    command -v container >/dev/null 2>&1 || die "Apple's container CLI is not installed (https://github.com/apple/container)"
    if ! container image inspect "$IMAGE" >/dev/null 2>&1; then
      printf '==> Building dev image %s (first run only)\n' "$IMAGE" >&2
      container build --tag "$IMAGE" --file "$ROOT/Containerfile" "$ROOT"
    fi
    # Pass through only the build knobs the scripts read; never secrets.
    envs=()
    for v in TARGET RUNTIME_MODE MACOSX_DEPLOYMENT_TARGET SKIP_SERVER_SMOKE; do
      if [ -n "${!v:-}" ]; then envs+=(--env "$v=${!v}"); fi
    done
    exec container run --rm \
      --volume "$ROOT:/work" \
      --workdir /work \
      ${envs[@]+"${envs[@]}"} \
      "$IMAGE" "$@"
    ;;
  *) die "CARDBOARD_TOOLS must be host or container (got $mode)" ;;
esac
