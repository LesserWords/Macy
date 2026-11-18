#!/usr/bin/env sh
# Deploy the staging (or prod) Compose project on the VPS (ADR-012, ADR-019, F20 AC2).
#
# Usage (from the release directory that holds compose files, db/ and the env file):
#   APP_TAG=<git-sha> sh scripts/deploy.sh            # deploy / roll back to a tag
#   sh scripts/deploy.sh --rollback <git-sha>         # restore app/worker/mcp without migrating
#   sh scripts/deploy.sh --preflight-only             # validate configuration, touch nothing
#
# Sequence: preflight -> pull -> db up -> migrate once -> set app role password ->
#           recreate app/worker/caddy -> smoke test -> roll back to the previous tag on failure.
# A failing migration aborts before any running service is recreated.
#
# Local rehearsal only (never on a real host):
#   ALLOW_PLACEHOLDER_SECRETS=1  accept CHANGE_ME values
#   SKIP_ENV_MODE_CHECK=1        skip the mode-600 check (filesystems without POSIX modes)
#   SMOKE_INSECURE=1             accept Caddy's internal CA for https://localhost
#   SKIP_PULL=1                  use locally built images instead of pulling from the registry
set -eu

ENV_FILE="${ENV_FILE:-.env}"
COMPOSE_FILES="${COMPOSE_FILES:--f infra/compose.yaml -f infra/compose.prod.yaml}"
REQUIRED_VARS="APP_ENV COMPOSE_PROJECT_NAME DOMAIN APP_IMAGE APP_TAG POSTGRES_PASSWORD APP_DB_PASSWORD APP_ENCRYPTION_KEY AUTH_COOKIE_SECRET GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET"
SMOKE_RETRIES="${SMOKE_RETRIES:-10}"
SMOKE_DELAY="${SMOKE_DELAY:-3}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-180}"

log() { printf 'deploy: %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# Value of KEY: caller environment wins, then the env file (last assignment), quotes stripped.
get_var() {
    eval "_cur=\${$1:-}"
    if [ -n "$_cur" ]; then
        printf '%s' "$_cur"
        return 0
    fi
    sed -n "s/^$1=//p" "$ENV_FILE" | tr -d '\r' | tail -n 1 | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//"
}

file_mode() {
    stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"
}

mcp_enabled() {
    _mcp_flag="$(get_var MCP_ENABLED)"
    _profiles="$(get_var COMPOSE_PROFILES)"
    if [ "$_mcp_flag" = "true" ]; then
        return 0
    fi
    case ",${_profiles}," in
        *,mcp,*) return 0 ;;
        *) return 1 ;;
    esac
}

ensure_mcp_profile_exported() {
    _profiles="$(get_var COMPOSE_PROFILES)"
    case ",${_profiles}," in
        *,mcp,*) COMPOSE_PROFILES="$_profiles" ;;
        ,,) COMPOSE_PROFILES="mcp" ;;
        *) COMPOSE_PROFILES="${_profiles},mcp" ;;
    esac
    export COMPOSE_PROFILES
}

validate_mcp_preflight() {
    mcp_enabled || return 0
    ensure_mcp_profile_exported

    mcp_token="$(get_var MCP_AUTH_TOKEN)"
    [ -n "$mcp_token" ] || die "MCP_AUTH_TOKEN is required when MCP_ENABLED=true"
    case "$mcp_token" in
        *CHANGE_ME*)
            if [ "${ALLOW_PLACEHOLDER_SECRETS:-0}" = "1" ]; then
                log "WARNING: MCP_AUTH_TOKEN still has a placeholder (allowed by ALLOW_PLACEHOLDER_SECRETS)"
            else
                die "MCP_AUTH_TOKEN still has a CHANGE_ME placeholder"
            fi
            ;;
    esac

    mcp_actor="$(get_var MCP_ACTOR_EMAIL)"
    [ -n "$mcp_actor" ] || die "MCP_ACTOR_EMAIL is required when MCP_ENABLED=true"
    case "$mcp_actor" in
        *CHANGE_ME*)
            die "MCP_ACTOR_EMAIL still has a CHANGE_ME placeholder"
            ;;
    esac
}

preflight() {
    [ -f "$ENV_FILE" ] || die "env file '$ENV_FILE' not found (copy .env.staging.example and fill it in)"

    if [ "${SKIP_ENV_MODE_CHECK:-0}" != "1" ]; then
        mode="$(file_mode "$ENV_FILE")"
        case "$mode" in
            600 | 400) ;;
            *) die "env file '$ENV_FILE' has mode $mode; run: chmod 600 $ENV_FILE" ;;
        esac
    fi

    for key in $REQUIRED_VARS; do
        value="$(get_var "$key")"
        [ -n "$value" ] || die "required variable $key is empty or missing in $ENV_FILE"
        case "$value" in
            *CHANGE_ME*)
                if [ "${ALLOW_PLACEHOLDER_SECRETS:-0}" = "1" ]; then
                    log "WARNING: $key still has a placeholder (allowed by ALLOW_PLACEHOLDER_SECRETS)"
                else
                    die "$key still has a CHANGE_ME placeholder"
                fi
                ;;
        esac
    done

    if [ "$(get_var APP_DB_PASSWORD)" = "app_secret" ]; then
        die "APP_DB_PASSWORD must not be the development default"
    fi

    retention_pw="$(get_var RETENTION_DB_PASSWORD)"
    if [ -n "$retention_pw" ]; then
        case "$retention_pw" in
            *CHANGE_ME*)
                if [ "${ALLOW_PLACEHOLDER_SECRETS:-0}" = "1" ]; then
                    log "WARNING: RETENTION_DB_PASSWORD still has a placeholder (allowed by ALLOW_PLACEHOLDER_SECRETS)"
                else
                    die "RETENTION_DB_PASSWORD still has a CHANGE_ME placeholder"
                fi
                ;;
            retention_secret)
                die "RETENTION_DB_PASSWORD must not be the development default"
                ;;
        esac
    fi

    validate_mcp_preflight

    secrets_file="$(get_var STREAMLIT_SECRETS_FILE)"
    if [ -n "$secrets_file" ]; then
        [ -f "$secrets_file" ] || die "Streamlit secrets file '$secrets_file' not found"
        if grep -v '^[[:space:]]*#' "$secrets_file" | grep -q 'CHANGE_ME'; then
            if [ "${ALLOW_PLACEHOLDER_SECRETS:-0}" = "1" ]; then
                log "WARNING: $secrets_file still has placeholders (allowed by ALLOW_PLACEHOLDER_SECRETS)"
            else
                die "$secrets_file still has CHANGE_ME placeholders"
            fi
        fi
    fi

    command -v docker >/dev/null 2>&1 || die "docker is not installed"
    command -v curl >/dev/null 2>&1 || die "curl is not installed (needed for the smoke test)"
    log "preflight ok"
}

PROJECT=""

dc() {
    # shellcheck disable=SC2086
    docker compose -p "$PROJECT" --env-file "$ENV_FILE" $COMPOSE_FILES "$@"
}

pull_services() {
    if mcp_enabled; then
        printf '%s' "app worker caddy db migrate backup mcp"
    else
        printf '%s' "app worker caddy db migrate backup"
    fi
}

runtime_services() {
    if mcp_enabled; then
        printf '%s' "app worker caddy backup mcp"
    else
        printf '%s' "app worker caddy backup"
    fi
}

rollback_services() {
    if mcp_enabled; then
        printf '%s' "app worker mcp"
    else
        printf '%s' "app worker"
    fi
}

# Tag of the image the running app container uses; empty on first deploy.
current_tag() {
    cid="$(dc ps -q app 2>/dev/null | head -n 1 || true)"
    [ -n "$cid" ] || return 0
    ref="$(docker inspect -f '{{.Config.Image}}' "$cid" 2>/dev/null || true)"
    [ -n "$ref" ] || return 0
    printf '%s' "${ref##*:}"
}

smoke_mcp_401() {
    mcp_enabled || return 0
    _origin="$(printf '%s' "$1" | sed -n 's#^\(https\{0,1\}://[^/]*\).*#\1#p')"
    [ -n "$_origin" ] || return 0
    # Use -D - instead of -w '%{http_code}' so curl works consistently on Linux and Git Bash.
    # shellcheck disable=SC2086
    _hdrs="$(curl -s -D - -o /dev/null $2 --max-time 10 \
        -X POST -H 'Content-Type: application/json' \
        -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' \
        "${_origin}/mcp" 2>/dev/null | tr -d '\r' || true)"
    _code="$(printf '%s\n' "$_hdrs" | sed -n 's/^HTTP\/[^ ]* \([0-9][0-9][0-9]\).*/\1/p' | tail -n 1)"
    [ "$_code" = "401" ]
}

smoke() {
    url="$(get_var SMOKE_URL)"
    [ -n "$url" ] || url="https://$(get_var DOMAIN)/_stcore/health"
    insecure=""
    [ "${SMOKE_INSECURE:-0}" = "1" ] && insecure="-k"
    i=1
    while [ "$i" -le "$SMOKE_RETRIES" ]; do
        # shellcheck disable=SC2086
        if body="$(curl -fsS $insecure --max-time 10 "$url" 2>/dev/null)" && [ "$body" = "ok" ] && smoke_mcp_401 "$url" "$insecure"; then
            log "smoke ok ($url)"
            return 0
        fi
        log "smoke attempt $i/$SMOKE_RETRIES failed ($url)"
        i=$((i + 1))
        sleep "$SMOKE_DELAY"
    done
    return 1
}

apply_app_role_password() {
    APP_DB_PASSWORD="$(get_var APP_DB_PASSWORD)"
    export APP_DB_PASSWORD
    pg_user="$(get_var POSTGRES_USER)"
    pg_db="$(get_var POSTGRES_DB)"
    # The password travels through the container environment, never the command line.
    printf '%s\n' '\getenv pw APP_DB_PASSWORD' "ALTER ROLE app PASSWORD :'pw';" |
        dc exec -T -e APP_DB_PASSWORD db psql -v ON_ERROR_STOP=1 -q \
            -U "${pg_user:-owner}" -d "${pg_db:-macy}" >/dev/null

    RETENTION_DB_PASSWORD="$(get_var RETENTION_DB_PASSWORD)"
    if [ -n "$RETENTION_DB_PASSWORD" ]; then
        export RETENTION_DB_PASSWORD
        printf '%s\n' '\getenv pw RETENTION_DB_PASSWORD' "ALTER ROLE retention PASSWORD :'pw';" |
            dc exec -T -e RETENTION_DB_PASSWORD db psql -v ON_ERROR_STOP=1 -q \
                -U "${pg_user:-owner}" -d "${pg_db:-macy}" >/dev/null
    fi
}

rollback() {
    previous="$1"
    if [ -z "$previous" ]; then
        log "no previous version to restore (first deploy); stack left running for inspection"
        return 0
    fi
    _rb_svcs="$(rollback_services)"
    log "rolling back ${_rb_svcs} to tag '$previous'"
    # shellcheck disable=SC2086
    if APP_TAG="$previous" dc up -d --no-build --wait --wait-timeout "$WAIT_TIMEOUT" $_rb_svcs; then
        log "rollback complete"
        return 0
    else
        log "ERROR: rollback to '$previous' also failed; manual intervention required"
        return 1
    fi
}

main() {
    case "${1:-}" in
        --help | -h)
            sed -n '2,20p' "$0"
            return 0
            ;;
        --preflight-only)
            preflight
            return 0
            ;;
        --rollback)
            target_tag="${2:-}"
            [ -n "$target_tag" ] || die "usage: $0 --rollback <tag>"
            preflight
            PROJECT="$(get_var COMPOSE_PROJECT_NAME)"
            _rb_svcs="$(rollback_services)"
            log "manual rollback project=$PROJECT target_tag=$target_tag (skipping migrations)"
            if [ "${SKIP_PULL:-0}" != "1" ]; then
                # shellcheck disable=SC2086
                APP_TAG="$target_tag" dc pull $_rb_svcs
            fi
            rollback "$target_tag" || die "manual rollback to '$target_tag' failed"
            smoke || die "smoke test failed after rollback to '$target_tag'"
            log "rollback to $target_tag succeeded"
            return 0
            ;;
        "") ;;
        *) die "unknown argument '$1'" ;;
    esac

    preflight
    PROJECT="$(get_var COMPOSE_PROJECT_NAME)"
    new_tag="$(get_var APP_TAG)"
    previous_tag="$(current_tag)"
    log "project=$PROJECT new_tag=$new_tag previous_tag=${previous_tag:-none}"

    _pull_svcs="$(pull_services)"
    _run_svcs="$(runtime_services)"
    if [ "${SKIP_PULL:-0}" = "1" ]; then
        log "SKIP_PULL=1: using locally available images (rehearsal only)"
    else
        log "pulling images ($_pull_svcs)"
        # shellcheck disable=SC2086
        dc pull $_pull_svcs
    fi

    log "starting database"
    dc up -d --wait --wait-timeout "$WAIT_TIMEOUT" db

    log "running migrations (one-shot, before touching running services)"
    if ! dc run --rm migrate; then
        die "migration failed; running services were not modified"
    fi
    apply_app_role_password

    log "recreating services ($_run_svcs)"
    # shellcheck disable=SC2086
    if ! dc up -d --no-build --remove-orphans --wait --wait-timeout "$WAIT_TIMEOUT" \
        $_run_svcs; then
        log "services did not become healthy"
        rollback "$previous_tag" || true
        die "deploy failed"
    fi

    if ! smoke; then
        rollback "$previous_tag" || true
        die "smoke test failed"
    fi
    log "deploy of $new_tag succeeded"
}

main "$@"
