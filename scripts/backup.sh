#!/usr/bin/env sh
# Back up what can't be recreated: the readings, and the Matter fabric (lose
# that and every device has to be re-paired from the tado app).
#
#   scripts/backup.sh [dir]          default ./backups; keeps the newest 14
#
# Fine to run while the stack is up (pg_dump reads a consistent snapshot) --
# e.g. nightly from cron:
#   0 3 * * *  /home/pi/tado_monitor/scripts/backup.sh >/dev/null
# Before moving to another host, stop matter-server first so the fabric
# files aren't mid-write: docker compose stop matter-server collector
set -eu
cd "$(dirname "$0")/.."
dir="${1:-backups}"
mkdir -p "$dir"
dir=$(cd "$dir" && pwd)
stamp=$(date +%Y%m%d-%H%M%S)

docker compose up -d --wait postgres >/dev/null
docker compose exec -T postgres pg_dump -U tado -Fc tado >"$dir/tado-$stamp.dump"

# Through the matter-server image, so the files are read as their owner
# (uid 1000) and it works whether or not the server is running. Lock and
# pid files belong to the running process; restored elsewhere they'd be stale.
docker compose run --rm --no-deps -v "$dir:/backup" --entrypoint tar \
    matter-server czf "/backup/matter-$stamp.tgz" \
    --exclude='*.lock' --exclude='*.pid' -C /data .

for pattern in 'tado-*.dump' 'matter-*.tgz'; do
    ls -1t "$dir"/$pattern | tail -n +15 | xargs -r rm --
done
echo "$dir/tado-$stamp.dump"
echo "$dir/matter-$stamp.tgz"
