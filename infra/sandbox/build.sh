#!/usr/bin/env bash
# Build the conduit sandbox image (infra/sandbox/Dockerfile).
#
# Assembles a CLEAN build context from the repo source -- crucially dropping any
# host-built native extension (a macOS Mach-O `*.so` would poison the Linux wheel
# with "invalid ELF header") -- then runs the multi-stage build (maturin compiles
# a Linux wheel; a slim runtime installs it + the echo AgentServer).
#
# Usage: infra/sandbox/build.sh [tag]   (default tag: conduit-sandbox:dev)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
TAG="${1:-conduit-sandbox:dev}"
CTX="$(mktemp -d)"
trap 'rm -rf "$CTX"' EXIT

cp "$REPO/Cargo.toml" "$REPO/Cargo.lock" "$REPO/pyproject.toml" "$REPO/README.md" "$CTX/"
cp -R "$REPO/src" "$CTX/src"
cp -R "$REPO/python" "$CTX/python"
rm -f "$CTX"/python/conduit_sdk/*.so   # drop host (macOS) ext; Linux wheel is built fresh
cp "$REPO/infra/sandbox/Dockerfile" "$REPO/infra/sandbox/echo_agent.py" "$CTX/"

echo "building $TAG from clean context $CTX ..."
docker build "$CTX" -t "$TAG"
echo "built $TAG"
