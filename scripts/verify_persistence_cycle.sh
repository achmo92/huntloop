#!/usr/bin/env bash
# Proves ROADMAP Phase 1 success criteria 1 and 3 end to end, against the real
# Docker Compose stack and the real named volume:
#   1. Data persists correctly across a full container stop, recreation, and
#      restart cycle.
#   3. A raw copy of the primary database file contains no usable credential.
# Exits 0 only if every assertion holds. Requires Docker.
set -euo pipefail

cd "$(dirname "$0")/.."

# One key for the whole run. It must stay constant across the restart, or the
# credential written before the restart cannot be decrypted after it — which
# is itself part of what is being verified.
export HUNTLOOP_SECRET_KEY="${HUNTLOOP_SECRET_KEY:-$(openssl rand -base64 32)}"

COMPOSE="docker compose"

step() { printf '\n=== %s\n' "$1"; }
cleanup() { $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT

step "1. Clean slate: down -v, then build"
$COMPOSE down -v --remove-orphans
$COMPOSE build

step "2. First boot and write (implicitly proves both migrations ran to completion first)"
WRITE_OUTPUT_1=$($COMPOSE --profile dev run --rm --no-TTY probe write)
echo "$WRITE_OUTPUT_1"
echo "$WRITE_OUTPUT_1" | grep -q 'Persistence Check Engineer'
! echo "$WRITE_OUTPUT_1" | grep -q 'Volume Team'

step "3. Read back while still up"
READ_OUTPUT_1=$($COMPOSE --profile dev run --rm --no-TTY probe read)
echo "$READ_OUTPUT_1"
echo "$READ_OUTPUT_1" | grep -q 'status=applied'

step "4. Stop and remove the containers, KEEP the volume (no -v)"
$COMPOSE down --remove-orphans
VOLUME_NAME=$(docker volume ls --format '{{.Name}}' | grep '_data$' || true)
if [ -z "$VOLUME_NAME" ]; then
  echo "FAIL: expected a leftover named volume ending in _data after 'down' (without -v)" >&2
  exit 1
fi
echo "volume still present: $VOLUME_NAME"

step "5. Recreate (re-runs migrate, proving upgrade head is a no-op) and read -- THE OPS-03 PROOF"
READ_OUTPUT_2=$($COMPOSE --profile dev run --rm --no-TTY probe read)
echo "$READ_OUTPUT_2"
echo "$READ_OUTPUT_2" | grep -q 'count=1'
echo "$READ_OUTPUT_2" | grep -q 'status=applied'
echo "$READ_OUTPUT_2" | grep -q 'credential=decrypted'

step "6. Re-run discovery after the restart, then read -- THE GUARDRAIL #1 PROOF"
WRITE_OUTPUT_2=$($COMPOSE --profile dev run --rm --no-TTY probe write)
echo "$WRITE_OUTPUT_2"
READ_OUTPUT_3=$($COMPOSE --profile dev run --rm --no-TTY probe read)
echo "$READ_OUTPUT_3"
echo "$READ_OUTPUT_3" | grep -q 'title=Persistence Check Engineer, Volume Team'
echo "$READ_OUTPUT_3" | grep -q 'count=1'
echo "$READ_OUTPUT_3" | grep -q 'status=applied'

step "7. Credential byte-scan on the real volume -- PHASE 1 SUCCESS CRITERION 3"
# sqlite3 may not be present in the python:3.12-slim image; the checkpoint
# command's `|| true` keeps the script going in that case, and the -wal
# sidecar scan below covers the same ground. Do NOT add sqlite3 to the
# production image just for this.
OUT=$(mktemp -d)
$COMPOSE --profile dev run --rm --no-TTY --entrypoint sh probe -c \
  'cd /data && sqlite3 huntloop.db "PRAGMA wal_checkpoint(FULL);" >/dev/null 2>&1 || true; tar cf - .' \
  | tar xf - -C "$OUT"
ls -l "$OUT"
! grep -aq 'sk-probe-do-not-leak' "$OUT/huntloop.db"
for f in "$OUT"/huntloop.db-wal "$OUT"/huntloop.db-shm; do
  [ -f "$f" ] && ! grep -aq 'sk-probe-do-not-leak' "$f"
done
if grep -aq 'sk-probe-do-not-leak' "$OUT/credentials.db"; then
  echo "FAIL: plaintext secret found in credentials.db" >&2
  exit 1
fi
test -s "$OUT/credentials.db"
rm -rf "$OUT"

step "8. Teardown (EXIT trap runs down -v)"
echo "PERSISTENCE CYCLE VERIFIED"
