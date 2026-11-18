#!/usr/bin/env sh
# Nightly PostgreSQL backup with offsite sync, 14-day retention, and alerts (ADR-012, F20 AC4).
#
# Usage:
#   sh scripts/backup.sh --once       # run one backup, sync offsite, prune, and exit
#   sh scripts/backup.sh              # loop every BACKUP_INTERVAL_SECONDS (default 86400)
set -eu

PG_HOST="${POSTGRES_HOST:-db}"
PG_PORT="${POSTGRES_PORT:-5432}"
PG_DB="${POSTGRES_DB:-macy}"
PG_USER="${POSTGRES_USER:-owner}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
OFFSITE_DIR="${OFFSITE_BACKUP_DIR:-/offsite}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-86400}"
BACKUP_HEARTBEAT_URL="${BACKUP_HEARTBEAT_URL:-}"
BACKUP_ALERT_URL="${BACKUP_ALERT_URL:-}"

log() { printf 'backup: %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

http_get() {
    target="$1"
    [ -n "$target" ] || return 0
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time 10 "$target" >/dev/null 2>&1 || true
    elif command -v wget >/dev/null 2>&1; then
        wget -q -T 10 -O /dev/null "$target" >/dev/null 2>&1 || true
    fi
}

http_post_json() {
    target="$1"
    payload="$2"
    [ -n "$target" ] || return 0
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time 10 -H "Content-Type: application/json" -d "$payload" "$target" >/dev/null 2>&1 || true
    elif command -v wget >/dev/null 2>&1; then
        wget -q -T 10 -O /dev/null --header="Content-Type: application/json" --post-data="$payload" "$target" >/dev/null 2>&1 || true
    fi
}

fail_backup() {
    reason="$1"
    log "ERROR: $reason"
    rm -f "${BACKUP_DIR}/.last_backup_ok"
    if [ -n "${BACKUP_HEARTBEAT_URL:-}" ]; then
        http_get "${BACKUP_HEARTBEAT_URL%/}/fail"
    fi
    if [ -n "${BACKUP_ALERT_URL:-}" ]; then
        clean_reason="$(printf '%s' "$reason" | tr '\n\r\134"' '    ')"
        payload="{\"service\":\"backup\",\"db\":\"${PG_DB}\",\"host\":\"${PG_HOST}\",\"error\":\"${clean_reason}\"}"
        http_post_json "$BACKUP_ALERT_URL" "$payload"
    fi
    return 1
}

prune_dir() {
    target_dir="$1"
    [ -d "$target_dir" ] || return 0
    find "$target_dir" -maxdepth 1 -type f -name 'macy_*.dump' -mtime "+$RETENTION_DAYS" -exec rm -f {} +
}

run_backup_once() {
    [ -n "${POSTGRES_PASSWORD:-}" ] || { fail_backup "POSTGRES_PASSWORD is not set"; return 1; }
    mkdir -p "$BACKUP_DIR" "$OFFSITE_DIR" || { fail_backup "failed to create backup directories"; return 1; }

    timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
    dump_name="macy_${PG_DB}_${timestamp}.dump"
    local_path="${BACKUP_DIR}/${dump_name}"
    offsite_path="${OFFSITE_DIR}/${dump_name}"

    log "creating dump ${dump_name} from ${PG_HOST}:${PG_PORT}/${PG_DB}"
    if ! PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
        -h "$PG_HOST" \
        -p "$PG_PORT" \
        -U "$PG_USER" \
        -d "$PG_DB" \
        -Fc \
        -f "$local_path"; then
        rm -f "$local_path"
        fail_backup "pg_dump failed for ${PG_DB}"
        return 1
    fi

    if [ ! -s "$local_path" ]; then
        rm -f "$local_path"
        fail_backup "dump file '$local_path' is empty"
        return 1
    fi

    if ! pg_restore --list "$local_path" >/dev/null; then
        rm -f "$local_path"
        fail_backup "pg_restore verification failed for '$local_path'"
        return 1
    fi

    if ! cp -f "$local_path" "$offsite_path" || [ ! -s "$offsite_path" ]; then
        fail_backup "offsite copy '$offsite_path' failed"
        return 1
    fi

    if [ -n "${OFFSITE_SYNC_CMD:-}" ]; then
        log "running custom offsite sync command"
        if ! DUMP_FILE="$local_path" OFFSITE_FILE="$offsite_path" sh -c "$OFFSITE_SYNC_CMD"; then
            fail_backup "OFFSITE_SYNC_CMD failed"
            return 1
        fi
    fi

    prune_dir "$BACKUP_DIR"
    prune_dir "$OFFSITE_DIR"

    printf '%s\n' "$dump_name" > "${BACKUP_DIR}/.last_backup_ok"
    if [ -n "${BACKUP_HEARTBEAT_URL:-}" ]; then
        http_get "$BACKUP_HEARTBEAT_URL"
    fi
    log "backup ok: ${dump_name} (synced to ${OFFSITE_DIR}, retention=${RETENTION_DAYS}d)"
}

main() {
    case "${1:-}" in
        --help | -h)
            sed -n '2,6p' "$0"
            return 0
            ;;
        --once)
            run_backup_once
            return $?
            ;;
        "")
            while true; do
                run_backup_once || log "backup cycle failed; sleeping ${INTERVAL_SECONDS}s before retry"
                sleep "$INTERVAL_SECONDS"
            done
            ;;
        *)
            die "unknown argument '$1'"
            ;;
    esac
}

main "$@"
