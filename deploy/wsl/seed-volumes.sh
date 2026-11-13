#!/bin/sh
# Fill the compose volumes once, from the Windows side (operations.md, "Containers"):
#   - archive: the calibrations of the repository's results/ (the desk's starting market);
#   - lake: a copy of the Windows lake (the containers then keep their own, which their worker updates);
#   - store: a database, when one is given, through `optlab restore` (checked, the empty one kept aside).
# Nothing is written outside the volumes; the Windows folders are mounted read-only. Run as root in WSL, after
# `docker compose -f deploy/compose.yaml build`:
#
#     sh deploy/wsl/seed-volumes.sh /mnt/c/Users/Luca/Code/projets/optlab-data /mnt/c/<the repository> [DATABASE]
set -eu
DATA=${1:?the Windows data folder, as /mnt/c/...}
REPO=${2:?the repository, as /mnt/c/...}
DATABASE=${3:-}
IMAGE=optlab-worker:${OPTLAB_TAG:-dev}
run() { docker run --rm --network none --user 10001:10001 --entrypoint sh "$@"; }

for v in store logs lake archive config backups; do  # labelled as compose labels its own, so `up` takes them over
  docker volume create --label com.docker.compose.project=optlab --label "com.docker.compose.volume=$v" \
    "optlab_$v" >/dev/null
done

echo "archive <- $REPO/results"
run -v optlab_archive:/data/archive -v "$REPO/results:/seed:ro" "$IMAGE" \
  -c 'cp /seed/spx_calibration.json /seed/simulated_calibration.json /data/archive/ && ls -l /data/archive'

echo "lake <- $DATA/lake"
start=$(date +%s)
run -v optlab_lake:/data/lake -v "$DATA/lake:/seed:ro" "$IMAGE" \
  -c 'cp -R /seed/. /data/lake/ && du -sh /data/lake'
echo "  $(( $(date +%s) - start )) s"

if [ -n "$DATABASE" ]; then
  echo "store <- $DATABASE (through optlab restore)"
  run -v optlab_backups:/data/backups -v "$(dirname "$DATABASE"):/seed:ro" "$IMAGE" \
    -c "mkdir -p /data/backups/manual && cp '/seed/$(basename "$DATABASE")' /data/backups/manual/optlab-seed.sqlite"
  docker run --rm --network none --user 10001:10001 -v optlab_store:/data/store -v optlab_backups:/data/backups \
    -v optlab_archive:/data/archive:ro -e OPTLAB_DATA=/data "$IMAGE" \
    restore /data/backups/manual/optlab-seed.sqlite --yes
fi
