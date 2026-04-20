#!/usr/bin/env bash
# Build and push Ask RGV backend images to GHCR.
# Usage:
#   ./build-and-push.sh              # builds all three images
#   ./build-and-push.sh backend      # builds only askrgv-backend
#   ./build-and-push.sh ingestion    # builds only askrgv-ingestion
#   ./build-and-push.sh bge-m3       # builds only askrgv-bge-m3
#
# Requires: docker login to ghcr.io first, or set GHCR_PAT env var.
#   export GHCR_PAT=ghp_xxxxxxxxxxxx
#   ./build-and-push.sh

set -euo pipefail

REGISTRY="ghcr.io"
ORG="marava-tech"

TARGET="${1:-all}"

login() {
  if [ -n "${GHCR_PAT:-}" ]; then
    echo "$GHCR_PAT" | docker login "$REGISTRY" -u "${GHCR_USER:-$(git config user.email)}" --password-stdin
  else
    echo "Skipping login (assuming already authenticated). Set GHCR_PAT to login automatically."
  fi
}

build_push() {
  local name="$1"
  local context="$2"
  local image="$REGISTRY/$ORG/$name:latest"
  echo ""
  echo "==> Building $image from $context"
  docker build --platform linux/amd64 -t "$image" "$context"
  echo "==> Pushing $image"
  docker push "$image"
  echo "==> Done: $image"
}

login

case "$TARGET" in
  backend)
    build_push "askrgv-backend" "./app"
    ;;
  ingestion)
    build_push "askrgv-ingestion" "./ingestion-worker"
    ;;
  bge-m3)
    build_push "askrgv-bge-m3" "./embeddings"
    ;;
  all)
    build_push "askrgv-backend"   "./app"
    build_push "askrgv-ingestion" "./ingestion-worker"
    build_push "askrgv-bge-m3"   "./embeddings"
    ;;
  *)
    echo "Unknown target: $TARGET. Use: backend | ingestion | bge-m3 | all"
    exit 1
    ;;
esac

echo ""
echo "All done."
