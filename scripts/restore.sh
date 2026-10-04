#!/usr/bin/env sh
# Restore a backup into a fresh install, e.g. after moving to the Pi:
#
#   scripts/restore.sh backups/tado-<stamp>.dump backups/matter-<stamp>.tgz
#   docker compose up -d --build
#
# Refuses to touch a database that already has devices or a Matter volume
# that already holds a fabric: never merge two histories or two fabrics.
set -eu
cd "$(dirname "$0")/.."
[ $# -eq 2 ] || { echo "usage: $0 <tado-*.dump> <matter-*.tgz>" >&2; exit 2; }
dump=$(realpath "$1")
matter=$(realpath "$2")

docker compose stop matter-server collector >/dev/null 2>&1 || true
docker compose up -d --wait postgres >/dev/null

devices=$(docker compose exec -T postgres psql -U tado -d tado -Atc 'SELECT count(*) FROM devices')
if [ "$devices" != 0 ]; then
    echo "refusing: database already has $devices device(s)" >&2
    exit 1
fi
# Same image trick as backup.sh: unpack as uid 1000, the server's user.
docker compose run --rm --no-deps -v "$(dirname "$matter"):/backup:ro" \
    --entrypoint sh matter-server -c '
        if [ -n "$(ls -A /data)" ]; then
            echo "refusing: Matter volume already holds a fabric" \
                 "(docker volume rm <project>_matter_data if it is a stray one)" >&2
            exit 1
        fi
        tar xzf "/backup/$1" -C /data' sh "$(basename "$matter")"

# Schema and roles come from db/init.sql; the dump only supplies rows.
docker compose exec -T postgres pg_restore -U tado -d tado --data-only \
    --single-transaction <"$dump"

echo "restored. start everything with: docker compose up -d --build"
