#!/bin/sh
# Deploy a release of the desk in WSL's Docker, or go back to the one before (ADR-0019, operations.md "Deploy").
# Run by the admin, as root in WSL, from the repository checked out at the release's tag (its compose.yaml):
#
#     sh deploy/wsl/deploy.sh v1.0.0            # pull by digest, copy the database, migrate, restart, check /ready
#     sh deploy/wsl/deploy.sh --rollback        # the version before, and the database as it was before the last deploy
#     sh deploy/wsl/deploy.sh --local TAG       # images already here as optlab-api:TAG and optlab-worker:TAG (a drill)
#     sh deploy/wsl/deploy.sh --status          # what runs, what ran before, the copy a rollback would restore
#
# A deploy: (1) the images of the release are pulled by the digests its GitHub release lists and tagged
# optlab-<api|worker>:<tag> here; (2) the database is copied (SQLite's online backup, read-only on the live file) into
# the backups volume; (3) api and worker stop; (4) the new worker image migrates the database; (5) both start on the
# new images; (6) /api/v1/ready must answer 200 within OPTLAB_READY_SECONDS (180). If any step after (2) fails, the
# previous images come back with the copy of (2), and the script exits 1. Nothing leaves the machine but the pulls.
set -eu
# Every function declares its variables `local` (dash and bash have it): sh's are global otherwise, and a helper that
# sets `tag` would change the caller's (the drill of A9 deployed the running version again that way).

REPO_SLUG=${OPTLAB_REPOSITORY:-Luca-CS/option-pricing-lab}
REGISTRY=${OPTLAB_REGISTRY:-ghcr.io/luca-cs}
PROJECT=${OPTLAB_PROJECT:-optlab}  # another name for a drill: its own volumes, never the desk's
STATE=${OPTLAB_DEPLOY_STATE:-/var/lib/optlab-deploy/$PROJECT}
PORT=${OPTLAB_PORT:-8765}
READY_SECONDS=${OPTLAB_READY_SECONDS:-180}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
compose() { docker compose -p "$PROJECT" -f "$ROOT/deploy/compose.yaml" "$@"; }
say() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { say "error: $*"; exit 1; }
read_state() { cat "$STATE/$1" 2>/dev/null || true; }

mkdir -p "$STATE"
chmod 700 "$STATE" "$(dirname "$STATE")"

pull() {  # pull a release's images by the digests of its GitHub release, and tag them here by the release's name
  local tag=$1 notes image digest
  notes=$(curl -fsSL "https://api.github.com/repos/$REPO_SLUG/releases/tags/$tag") || fail "no GitHub release $tag"
  for image in api worker; do
    digest=$(printf '%s' "$notes" | grep -o "$image=sha256:[0-9a-f]\{64\}" | head -1 | cut -d= -f2)
    [ -n "$digest" ] || fail "the release $tag lists no digest for $image"
    say "pull $REGISTRY/optlab-$image@$digest"
    docker pull -q "$REGISTRY/optlab-$image@$digest" >/dev/null
    docker tag "$REGISTRY/optlab-$image@$digest" "optlab-$image:$tag"
  done
}

has_images() {
  docker image inspect "optlab-api:$1" "optlab-worker:$1" >/dev/null 2>&1
}

copy_database() {  # SQLite's online backup of the live database, by any image (no optlab code: no migration on open)
  local image_tag=$1 name=$2
  OPTLAB_TAG=$image_tag compose run --rm --no-deps -T --entrypoint python worker -c "
import os, sqlite3
if not os.path.exists('/data/store/optlab.sqlite'):
    raise SystemExit(3)  # nothing to copy yet: a first deploy
os.makedirs('/data/backups/manual', exist_ok=True)
src = sqlite3.connect('file:/data/store/optlab.sqlite?mode=ro', uri=True)
dst = sqlite3.connect('/data/backups/manual/$name')
src.backup(dst)
dst.close(); src.close()
"
}

ready() {  # /ready answers 200 (the database, and a worker that serves the queue) within READY_SECONDS
  local waited=0
  while [ "$waited" -lt "$READY_SECONDS" ]; do
    if curl -fs -o /dev/null "http://127.0.0.1:$PORT/api/v1/ready"; then
      return 0
    fi
    sleep 5
    waited=$((waited + 5))
  done
  return 1
}

start() {  # api and worker on the images of a tag
  OPTLAB_TAG=$1 compose up -d --no-build api worker
}

restore_and_start() {  # the images of `back_tag`, the database of `back_copy` (a file of backups/manual)
  local back_tag=$1 back_copy=$2
  compose stop api worker >/dev/null 2>&1 || true
  if [ -n "$back_copy" ]; then
    say "restore backups/manual/$back_copy with $back_tag"
    OPTLAB_TAG=$back_tag compose run --rm --no-deps -T worker restore "/data/backups/manual/$back_copy" --yes
  fi
  start "$back_tag"
  ready || fail "$back_tag does not answer /ready either: see docker compose -p $PROJECT logs"
  say "back on $back_tag"
}

deploy() {
  local tag=$1 local_only=$2 current copy stamp status
  current=$(read_state current)
  [ "$tag" != "$current" ] || fail "$tag already runs"
  if [ "$local_only" = no ]; then pull "$tag"; fi
  has_images "$tag" || fail "no images optlab-api:$tag and optlab-worker:$tag here"
  copy=""
  if docker volume inspect "${PROJECT}_store" >/dev/null 2>&1; then
    stamp=$(date -u +%Y%m%d-%H%M%S)-000
    copy="optlab-$stamp-before-$tag.sqlite"
    status=0
    copy_database "${current:-$tag}" "$copy" || status=$?
    case $status in
      0) say "database copied to backups/manual/$copy" ;;
      3) copy=""; say "no database yet: nothing to copy" ;;
      *) fail "the copy of the database failed: nothing changed" ;;
    esac
  fi
  say "stop api and worker (${current:-nothing deployed yet})"
  compose stop api worker >/dev/null 2>&1 || true
  if OPTLAB_TAG=$tag compose run --rm --no-deps -T worker migrate && start "$tag" && ready; then
    printf '%s\n' "$current" >"$STATE/previous"
    printf '%s\n' "$tag" >"$STATE/current"
    printf '%s\n' "$copy" >"$STATE/copy"
    say "$tag deployed and ready on 127.0.0.1:$PORT (rollback: sh deploy/wsl/deploy.sh --rollback)"
    return 0
  fi
  say "$tag failed: back to ${current:-nothing} (its logs: docker compose -p $PROJECT logs --tail 50 api worker)"
  if [ -n "$current" ]; then
    restore_and_start "$current" "$copy"
  else
    compose stop api worker >/dev/null 2>&1 || true
  fi
  exit 1
}

rollback() {
  local current previous copy
  current=$(read_state current) previous=$(read_state previous) copy=$(read_state copy)
  [ -n "$previous" ] || fail "nothing to go back to (current: ${current:-none})"
  has_images "$previous" || fail "the images of $previous are gone: deploy it again instead"
  say "rollback from $current to $previous, the database of before $current's deploy (${copy:-none})"
  restore_and_start "$previous" "$copy"
  printf '%s\n' "$previous" >"$STATE/current"
  printf '\n' >"$STATE/previous"
  printf '\n' >"$STATE/copy"
}

case "${1:-}" in
  --rollback) rollback ;;
  --status) printf 'current: %s\nprevious: %s\ncopy: %s\n' "$(read_state current)" "$(read_state previous)" "$(read_state copy)" ;;
  --local) deploy "${2:?the tag of images already here}" yes ;;
  v[0-9]*) deploy "$1" no ;;
  *) sed -n '2,16p' "$0"; exit 2 ;;
esac
