# Runbook: staging and prod on the VPS (and local Docker Compose parity)

Scope: deploy, rollback, logs, restart, backups, restore, retention, opt-in MCP service (`ADR-027`), and first-time VPS preparation. ADR-012, ADR-019, ADR-024, and ADR-027 explain the design; F20 and F21 hold the acceptance criteria. For day-to-day UI operations see [guides/user-guide.md](guides/user-guide.md); for CLI commands see [guides/developer-guide.md](guides/developer-guide.md); for AI client setup (`stdio` and HTTPS `streamable-http`) see [MCP.md](MCP.md); for where to obtain every env variable and external key see [guides/environment-and-credentials.md](guides/environment-and-credentials.md).

## Layout on the VPS (and local Compose parity)

```text
/opt/macy/staging/
  .env                         # mode 600, never in git (from .env.staging.example)
  infra/  db/  scripts/deploy.sh  scripts/backup.sh

/opt/macy/prod/
  .env                         # mode 600, never in git (from .env.prod.example)
  infra/  db/  scripts/deploy.sh  scripts/backup.sh
```

The Compose projects are `macy-staging` and `macy-prod` (`COMPOSE_PROJECT_NAME` in the env file). Shorthand used below:

```sh
# On the VPS (inside /opt/macy/staging or /opt/macy/prod, where the file is named .env):
cd /opt/macy/staging   # or /opt/macy/prod
dc()      { docker compose -p macy-staging --env-file .env -f infra/compose.yaml -f infra/compose.prod.yaml "$@"; }
dc_prod() { docker compose -p macy-prod    --env-file .env -f infra/compose.yaml -f infra/compose.prod.yaml "$@"; }

# From a local repo checkout (using .env.staging / .env.prod side by side):
dc()      { docker compose -p macy-staging --env-file .env.staging -f infra/compose.yaml -f infra/compose.prod.yaml "$@"; }
dc_prod() { docker compose -p macy-prod    --env-file .env.prod    -f infra/compose.yaml -f infra/compose.prod.yaml "$@"; }
```

## First-time VPS preparation

Minimum box: 2 vCPU, 4 GB RAM, 40 GB disk (ADR-012).

1. **Docker and a deploy user.** Install Docker Engine and the Compose plugin from Docker's apt repository. Create a `deploy` user in the `docker` group with key-only SSH; disable password login.
2. **Firewall.** Allow only ports `22` (or your SSH port), `80`, and `443`. The database port is never published by `compose.prod.yaml`; verify anyway (see [Verify exposure](#verify-exposure)).
3. **Unattended upgrades.** Run `apt install unattended-upgrades`, then `dpkg-reconfigure -plow unattended-upgrades`. Verify via `systemctl status unattended-upgrades` and `/var/log/unattended-upgrades/`.
4. **DNS.** Point `staging.<domain>` (and `<prod-domain>`) `A`/`AAAA` records at the VPS. Caddy obtains and renews TLS certificates automatically once `80`/`443` are reachable.
5. **Directory and secrets.** Create the target directory (`/opt/macy/staging` or `/opt/macy/prod`), copy the matching `.env.*.example` to `.env`, and restrict permissions:

   ```sh
   sudo install -d -o deploy -g deploy -m 750 /opt/macy/staging
   cd /opt/macy/staging
   # copy .env.staging.example -> .env (or .env.prod.example -> .env in /opt/macy/prod)
   chmod 600 .env
   ```

   Replace every `CHANGE_ME` placeholder before running `scripts/deploy.sh`:
   - `POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `RETENTION_DB_PASSWORD`, `AUTH_COOKIE_SECRET`: `openssl rand -hex 32` (distinct values per environment)
   - `APP_ENCRYPTION_KEY`: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   - `DOMAIN=staging.<domain>`, `SMOKE_URL=https://staging.<domain>/_stcore/health`, `APP_IMAGE=ghcr.io/<owner-lowercase>/macy`
   - If enabling the opt-in MCP HTTP server (`MCP_ENABLED=true`, `COMPOSE_PROFILES=mcp`): set `MCP_AUTH_TOKEN` (`openssl rand -hex 32`, minimum 32 chars) and `MCP_ACTOR_EMAIL` (pinned Macy owner email provisioned via `add-owner`)

6. **Google OAuth & Workspace Bootstrap.**
   - In Google Cloud Console, create a Web OAuth client with authorized redirect URI `https://<DOMAIN>[:<CADDY_HTTPS_PORT>]/oauth2callback`.
   - Put `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` into `.env`. No manual `secrets.toml` file is required: at container startup `python -m engine.auth` renders `/tmp/macy/secrets.toml` from `.env` (loaded via `.streamlit/config.toml` `[secrets]`), and derives `AUTH_REDIRECT_URI` automatically when left blank.
   - Bootstrap the workspace, owner, brand profile, and shared sources via CLI:

   ```sh
   dc run --rm --no-deps app content-engine create-workspace acme-demo \
     --name "Acme Demo" --pack general --timezone America/Sao_Paulo
   dc run --rm --no-deps app content-engine add-owner acme-demo <owner-google-email>
   dc run --rm --no-deps app content-engine seed-brand acme-demo
   dc run --rm --no-deps app content-engine seed-sources --pack general
   ```

   Optionally import an existing blog archive so weekly reports flag covered topics (`content-engine import-sitemap <sitemap-url> --workspace acme-demo`; see [guides/developer-guide.md](guides/developer-guide.md)).

7. **GitHub Environments & Variables.**

   | Environment | Repository Variable | Environment Secrets & Variables |
   |---|---|---|
   | `staging` | `STAGING_DEPLOY_ENABLED=true`, optional `MCP_ENABLED=true` | Secrets: `STAGING_SSH_HOST`, `STAGING_SSH_USER`, `STAGING_SSH_KEY`, `STAGING_SSH_KNOWN_HOSTS` (`ssh-keyscan -t ed25519 <host>`, verify fingerprint), optional `STAGING_MCP_AUTH_TOKEN`; Optional var: `STAGING_SSH_PORT` |
   | `production` (`M5-01`, `M5-06`) | `PROD_DEPLOY_ENABLED=true`, optional `MCP_ENABLED=true` | Secrets: `PROD_SSH_HOST`, `PROD_SSH_USER`, `PROD_SSH_KEY`, `PROD_SSH_KNOWN_HOSTS`, `PROD_PLAYWRIGHT_STORAGE_STATE` (optional saved OIDC JSON for authenticated J1 smoke), optional `PROD_MCP_AUTH_TOKEN` (`--check-mcp`); Var/Secret: `PROD_APP_URL` (`https://<prod-domain>`), optional `PROD_SSH_PORT` |

   For `production`, also populate monitoring keys in `/opt/macy/prod/.env` (`SENTRY_DSN`, `HEARTBEAT_URL`, `BACKUP_HEARTBEAT_URL`, `BACKUP_ALERT_URL`).

8. **Dry check on the box** (makes no changes):

   ```sh
   sh scripts/deploy.sh --preflight-only
   ```

## Deploy

- **Staging (automatic):** Merge to `main`. `.github/workflows/deploy.yml` builds `ghcr.io/<owner>/macy:<git-sha>` (plus `:staging`), ships the release bundle to `/opt/macy/staging`, and runs `scripts/deploy.sh` over SSH.
- **Production (tag-triggered, `M5-01` / `F20 AC3`):** Push a `v*` tag (e.g., `git tag v0.1.0 && git push origin v0.1.0`) or trigger `workflow_dispatch` with `target=production`.
  1. `build-prod` pushes `:<tag>`, `:<git-sha>`, and `:prod`.
  2. `deploy-prod` deploys `/opt/macy/prod` via `scripts/deploy.sh`, then runs `uv run python scripts/post_deploy_smoke.py --url "$PROD_APP_URL"` (`--require-auth` when `PROD_PLAYWRIGHT_STORAGE_STATE` is set) to verify `/_stcore/health` and read-only **Campaigns** (J1) rendering.
  3. If post-deploy smoke fails and a previous image tag was recorded, `deploy-prod` re-deploys that previous tag and marks the workflow run as failed.

`scripts/deploy.sh` executes in order: preflight (env mode `600`, required variables, no `CHANGE_ME`) → image pull → start `db` → **migrate once** → apply `app`/`retention` DB role passwords → recreate `app`, `worker`, `caddy`, `backup` → probe `/_stcore/health` through Caddy → roll back on failure.

Manual deploy of a specific tag from the VPS:

```sh
cd /opt/macy/staging   # or /opt/macy/prod
echo "$GHCR_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin   # read:packages token
APP_TAG=<git-sha> sh scripts/deploy.sh
docker logout ghcr.io
```

Exit status `0` means the new version is healthy; non-zero means failure (inspect the last `deploy:` lines).

## Failure behaviour

| Failure | Result |
|---|---|
| Preflight (missing file, wrong mode, placeholder) | Exits before touching containers |
| Image pull fails | Exits before touching containers |
| Migration fails | Exits; running `app`/`worker`/`caddy` stay on old version |
| New containers unhealthy, or smoke test fails | `app` and `worker` roll back to previous tag; exits non-zero |
| First deploy fails | Nothing to restore; stack remains up for inspection |

Migrations are forward-only. A rollback restores the previous application image, not the schema, so migrations must remain backward-compatible with the prior release (`squawk` enforces non-blocking DDL; avoid dropping or renaming columns in the same release that stops reading them).

## Rollback

Redeploy a known-good tag (already-applied migrations are a no-op), or use `--rollback` to restore `app`, `worker`, and `mcp` (when `MCP_ENABLED=true`) without re-running `migrate`:

```sh
dc images app                           # show current tag
docker image ls 'ghcr.io/<owner>/macy'  # inspect cached tags on host
APP_TAG=<previous-git-sha> sh scripts/deploy.sh          # full deploy path
sh scripts/deploy.sh --rollback <previous-git-sha>       # targeted rollback (skips migrate)
```

- **Live VPS rollback rehearsal:** _not yet rehearsed on the live VPS_.
- **Local `macy-prod` Compose rehearsal (`2026-11-10`):** Verified automatic failed-smoke rollback (`0.1.0` → `0.0.9-prev`), `scripts/post_deploy_smoke.py --url https://localhost:8443 --insecure`, workspace bootstrap CLI, scrubbed Sentry envelope capture, and `scripts/backup.sh` failure alert webhook (see [docs/plans/M5.md](plans/M5.md) and `M2-06` in [docs/plans/M2.md](plans/M2.md)).
- **Local `macy-prod` + MCP Compose rehearsal (`2026-11-11`):** Verified `MCP_ENABLED=true` preflight (`CHANGE_ME` rejection + positive validation), isolated `compose.yaml` + `compose.prod.yaml` deploy (`0.0.9-prev` -> `0.1.0-mcp`) exposing only Caddy host ports, seeded Postgres workspace RBAC over HTTPS `/mcp` (`post_deploy_smoke.py --insecure --check-mcp`), automatic failed-smoke rollback across `app`/`worker`/`mcp`, and migration-free `--rollback 0.0.9-prev`.

## Logs

```sh
dc ps                          # state and health of every service
dc logs -f --tail=200 app      # Streamlit UI
dc logs -f --tail=200 worker   # queue & webhook worker
dc logs --tail=200 caddy       # TLS and reverse proxy
dc logs --tail=200 db          # PostgreSQL logs
journalctl -u docker --since "1 hour ago"
```

## Restart

```sh
dc restart app            # UI only
dc restart worker
dc up -d --no-build       # reconcile containers to current compose files
dc down                   # stop containers while preserving DB and TLS volumes
```

Never run `dc down -v` on a host with data you need: it deletes database and Caddy certificate volumes.

## Verify exposure

From an external machine:

```sh
nmap -Pn -p 22,80,443,5432,8501,8765 staging.<domain>   # expect 5432, 8501, and 8765 closed/filtered
curl -sI https://staging.<domain>/_stcore/health        # expect HTTP 200 over HTTPS
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' \
  https://staging.<domain>/mcp                          # expect HTTP 401 when MCP_ENABLED=true
```

On the host: `dc ps --format '{{.Name}} {{.Ports}}'` must show published ports for `caddy` only.

## MCP Server (`M5-06`, opt-in)

The `mcp` service (`profiles: ["mcp"]`) runs `content-engine mcp --transport streamable-http --host 0.0.0.0 --port 8765` inside the internal Docker network, proxied at `/mcp*` by Caddy (`Caddyfile`). See [MCP.md](MCP.md) for AI client JSON snippets and error codes.

1. **Enable on staging or prod:**
   - Generate a bearer token (`openssl rand -hex 32`) and set `MCP_ENABLED=true`, `COMPOSE_PROFILES=mcp`, `MCP_AUTH_TOKEN=<token>`, and `MCP_ACTOR_EMAIL=<owner-email>` in `/opt/macy/<env>/.env` (`chmod 600 .env`).
   - Confirm `<owner-email>` exists in `users` with workspace membership (`dc exec -T app content-engine add-owner acme-demo <owner-email>`).
   - Set GitHub Actions variable `MCP_ENABLED=true` and environment secret `STAGING_MCP_AUTH_TOKEN` / `PROD_MCP_AUTH_TOKEN` so `deploy.yml` and `e2e-live.yml` execute `--check-mcp` and live MCP smoke tests automatically.
   - Deploy (`APP_TAG=<tag> sh scripts/deploy.sh`): preflight rejects empty/`CHANGE_ME` `MCP_AUTH_TOKEN` or missing `MCP_ACTOR_EMAIL` before touching containers, starts `mcp`, and checks unauthenticated `POST /mcp -> 401`.
2. **Smoke check & inspect logs:**
   ```sh
   MCP_AUTH_TOKEN=<token> uv run python scripts/post_deploy_smoke.py --url https://<DOMAIN> --check-mcp
   dc logs -f --tail=200 mcp
   ```
3. **Token rotation:**
   - Generate a new token (`openssl rand -hex 32`), update `MCP_AUTH_TOKEN` in `/opt/macy/<env>/.env`, and run:
     ```sh
     dc --profile mcp up -d --no-build mcp
     MCP_AUTH_TOKEN=<new-token> uv run python scripts/post_deploy_smoke.py --url https://<DOMAIN> --check-mcp
     ```
   - Update `STAGING_MCP_AUTH_TOKEN` / `PROD_MCP_AUTH_TOKEN` in GitHub Secrets and distribute the new token to authorized MCP clients ([MCP.md](MCP.md)).
4. **Migration-free rollback:**
   ```sh
   sh scripts/deploy.sh --rollback <previous-tag>
   ```
   When `MCP_ENABLED=true`, `--rollback` restores `app`, `worker`, and `mcp` to `<previous-tag>`, skips migrations, and re-runs the `/_stcore/health` + `/mcp -> 401` smoke check.

## Secrets and rotation

- `.env` (and any optional `.streamlit/secrets.toml` override) exist only on the host (mode `600`) and in your password manager.
- Google OIDC secrets live in `.env` (`AUTH_COOKIE_SECRET`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, optional `AUTH_REDIRECT_URI`) and render into container-local `/tmp/macy/secrets.toml` at `app` startup; `.streamlit/config.toml` (`[secrets]`) allows `.streamlit/secrets.toml` to override them if explicitly mounted.
- `APP_ENCRYPTION_KEY` accepts a comma-separated key list for zero-downtime rotation:
  1. Prepend the new key alongside the old one (`"<new>,<old>"`), deploy, and run:
     ```sh
     dc run --rm --no-deps app content-engine rotate-key --current-keys "<old>,<new>" --new-key "<new>"
     ```
  2. Drop `<old>` from `.env` and redeploy.
- The GHCR pull token in CI is the workflow's short-lived `GITHUB_TOKEN` piped over SSH stdin; no long-lived registry credentials are stored on the host.
- Staging and production never receive `MACY_TEST_USER_EMAIL`; authentication is Google OIDC only.

## Two projects on one host

Both `macy-staging` and `macy-prod` share `compose.yaml` + `compose.prod.yaml` and run cleanly side by side locally or on a single VPS:

- Each project uses its own env file (`COMPOSE_PROJECT_NAME=macy-staging` vs `macy-prod`), database (`macy` vs `macy_prod`), volumes (`macy-staging_db_data` vs `macy-prod_db_data`), and Google OIDC credentials.
- Neither project exposes PostgreSQL (`5432`) or raw Streamlit (`8501`).
- Assign distinct `CADDY_HTTP_PORT`/`CADDY_HTTPS_PORT` values when sharing one host (for example `80`/`443` for staging and `8080`/`8443` for local prod testing, or route both domains through an edge proxy).
- When running `scripts/deploy.sh` locally with a prebuilt image and Caddy's self-signed localhost certificate, pass `SKIP_PULL=1 SMOKE_INSECURE=1`.

## Scheduled weekly runs and nightly purge (cron wiring)

The `worker` container (`python -m engine`) polls PostgreSQL every `15 s` for queued manual runs (**Run now**), compiles reports, and delivers outbound webhooks, **but the container image does not bundle or start `supercronic`**. Generate schedule definitions with `content-engine crontab` and run them via host cron or an external Supercronic container:

```sh
# 1. Inspect generated cron entries (workspace-timezone campaign schedules + 02:00 UTC purge):
dc_prod exec -T app content-engine crontab

# 2. Example host crontab entries for /opt/macy/prod:
# Weekly Monday 09:00 workspace time:
0 9 * * 1 cd /opt/macy/prod && \
  docker compose -p macy-prod --env-file .env -f compose.yaml -f compose.prod.yaml \
  exec -T app content-engine run-week --workspace acme-demo

# Nightly 02:00 UTC retention purge (container already injects RETENTION_DATABASE_URL):
0 2 * * * cd /opt/macy/prod && \
  docker compose -p macy-prod --env-file .env -f compose.yaml -f compose.prod.yaml \
  exec -T app content-engine purge
```

## Backups and restore (M4-06, F20 AC4)

### Nightly `pg_dump` and offsite retention

- The `backup` service in `compose.prod.yaml` runs `scripts/backup.sh`, which executes `pg_dump -Fc` every 24 hours (`BACKUP_INTERVAL_SECONDS=86400`), verifies archives with `pg_restore --list`, copies dumps to `/offsite` (`OFFSITE_BACKUP_PATH` + optional `OFFSITE_SYNC_CMD`), and prunes files older than `BACKUP_RETENTION_DAYS` (14 days) in both `/backups` and `/offsite`.
- Trigger an immediate backup manually:

  ```sh
  dc exec backup sh /usr/local/bin/backup.sh --once
  ```

### Separate manual backup of `APP_ENCRYPTION_KEY`

- Database dumps store Fernet ciphertext in `workspace_integrations.secrets` and `webhook_subscriptions.secret`.
- **Mandatory manual step:** Store `APP_ENCRYPTION_KEY` in your password manager separately from the VPS and offsite backups whenever it is generated or rotated. Without it, a restored database cannot decrypt integration secrets or webhook signing keys.

### Restore into a scratch database

Recover or verify the latest offsite dump into `macy_restore_scratch`:

```sh
DUMP_FILE=$(dc exec -T backup sh -c 'ls -1t /offsite/macy_*.dump | head -n 1')
dc exec -T db psql -U owner -d postgres -c "DROP DATABASE IF EXISTS macy_restore_scratch;"
dc exec -T db psql -U owner -d postgres -c "CREATE DATABASE macy_restore_scratch OWNER owner;"
dc exec -T backup sh -c \
  "PGPASSWORD=\$POSTGRES_PASSWORD pg_restore -h db -U owner -d macy_restore_scratch --no-owner --no-privileges $DUMP_FILE"
```

Compare row counts between live and restored databases (replace `-d macy` with `-d macy_prod` in prod):

```sh
ROW_COUNT_SQL="SELECT 'workspaces' AS tbl, count(*) FROM workspaces
  UNION ALL SELECT 'users', count(*) FROM users
  UNION ALL SELECT 'campaigns', count(*) FROM campaigns
  UNION ALL SELECT 'documents', count(*) FROM documents
  UNION ALL SELECT 'events', count(*) FROM events
  ORDER BY tbl;"
dc exec -T db psql -U owner -d macy -c "$ROW_COUNT_SQL"
dc exec -T db psql -U owner -d macy_restore_scratch -c "$ROW_COUNT_SQL"
```

### Recorded restore rehearsal (M4-06)

- Rehearsed `2026-11-09` in Docker Compose (`postgres:18-alpine`): `scripts/backup.sh --once` wrote a verified dump to `/backups` and `/offsite`, pruned `>14`-day dumps, restored cleanly into `macy_restore_scratch`, and matched row counts 100% across all tables.

## Data retention and LGPD user anonymisation (M4-06, ADR-024, F20 AC7–AC8)

- **Nightly retention purge (`content-engine purge`):** Runs under the least-privilege `retention` database role (`RETENTION_DATABASE_URL`, pre-configured on `app` in `compose.prod.yaml`). Deletes `llm_payloads` `>90` days (retaining `usage_ledger`), nulls `documents.extracted_text` + stamps `text_purged_at` `>365` days (retaining metadata/hash), deletes `delivered`/`dead` `webhook_deliveries` `>30` days, and deletes `events` `>24` months:

  ```sh
  dc run --rm --no-deps app content-engine purge
  ```

- **LGPD user deletion (`content-engine anonymise-user <email>`):** Replaces `users.email` with `deleted-<user_id>@anonymised.invalid`, clears `users.name`, removes `memberships`, and scrubs personal email/name keys from `events.payload` while preserving opaque `actor_user_id`:

  ```sh
  dc run --rm --no-deps app content-engine anonymise-user user@example.com
  ```

## Live E2E testing (M2-07)

Live E2E tests execute Playwright journeys `J0–J4` (plus extended journeys when configured) against a live Macy instance:

- **Nightly CI:** `.github/workflows/e2e-live.yml` runs at `03:00 UTC` when repository variable `LIVE_E2E_ENABLED=true`, or manually via `workflow_dispatch`.
- **Auth:** Provide Google OIDC session JSON in secret `PLAYWRIGHT_STORAGE_STATE` (or `MACY_TEST_USER_EMAIL` in non-prod dev/test).
- **Budget & diagnostics:** `BudgetGuard` aborts on cap overrun; each journey retries once (`--reruns 1`) and saves failure traces/screenshots to `artifacts/playwright-results/`.
- **Run locally:**

  ```sh
  uv run playwright install chromium
  uv run pytest tests/test_live_e2e_j0_j4.py -m live_e2e --tracing retain-on-failure --screenshot only-on-failure
  ```

## Monitoring and failure alerts (`M5-01`, `ADR-023`, `F20 AC5–AC6`)

- **Sentry (`SENTRY_DSN`):** `app.py`, CLI, and worker initialize Sentry automatically when `SENTRY_DSN` is set, tagging events with `workspace_id`, `run_id`, and release (`APP_TAG` or package version) while scrubbing DSN passwords, `Bearer` tokens, query secrets, secret prefixes, and sensitive keys. Verify delivery after deploy:

  ```sh
  dc_prod exec app content-engine test-sentry
  ```

- **Weekly pipeline heartbeat (`HEARTBEAT_URL`):** `execute_run` sends `GET $HEARTBEAT_URL` on completion and `GET ${HEARTBEAT_URL%/}/fail` on failure or `blocked(integration_missing)` (HTTPS required in production).
- **Backup heartbeat and alerts (`BACKUP_HEARTBEAT_URL`, `BACKUP_ALERT_URL`):** `scripts/backup.sh` pings `GET $BACKUP_HEARTBEAT_URL` on success and, on any error, deletes `.last_backup_ok`, pings `GET ${BACKUP_HEARTBEAT_URL%/}/fail`, and POSTs a JSON error payload to `BACKUP_ALERT_URL`.

## Known limitations (tracked)

- **Scheduled cron daemon not bundled in `worker`:** `worker` (`python -m engine`) processes queued UI `Run now` runs and webhook deliveries; automatic `schedule_cron` and nightly `purge` require host cron or external Supercronic until a dedicated scheduler service is added.
- **Migration-default `readonly` password:** The `readonly` database role keeps its migration password until external BI reporting is enabled (port `5432` stays closed outside the Compose network).
- **Live VPS & pilot pending:** Live VPS rollout (`v0.1.0` + `PROD_DEPLOY_ENABLED=true`) and the 4-week editorial pilot (`M5-02`) remain to be executed with real host, OAuth, and vendor credentials.

## Pocket Runtime & Zero-DB Path B (`M7`, `ADR-029`–`ADR-031`)

### Path A — Pocket Single-Container Runtime (`infra/compose.pocket.yaml`)

1. Start the zero-`.env` stack (App + Embedded Worker + Postgres, plus optional `ollama` profile):

   ```sh
   docker compose -f infra/compose.pocket.yaml up -d
   # Optional local LLM profile:
   docker compose -f infra/compose.pocket.yaml --profile local-llm up -d
   ```

2. Open `http://localhost:8501` — on an empty database, the 3-step Setup Wizard creates the local PBKDF2 admin owner (`AUTH_MODE=auto` switches between local auth and Google OIDC when `GOOGLE_CLIENT_ID` is set), configures capabilities (`core-compliance`, `seo-audit`, `editorial-pipeline`, `route-monitors`), applies an integration stack preset (`pocket_free`, `local_ollama`, `pro_cloud`), and auto-seeds brand/source/sitemap metadata from a website URL.
3. For unattended provisioning, run:

   ```sh
   docker compose -f infra/compose.pocket.yaml exec app \
     content-engine init --email admin@example.com --password '<secret>' \
     --slug my-ws --name "My Workspace" --preset pocket_free --non-interactive
   ```

### Path B — Zero-Docker Stateless CLI & MCP

Install lightweight core extras (`uvx --from "content-engine[core]" content-engine ...` or local `.venv`) and run without `DATABASE_URL` or Docker:

```sh
content-engine audit https://example.com --stateless --json
content-engine import-draft --file ./draft.md --format blog-short --stateless --json
content-engine mcp --transport stdio --stateless --check
```

## Private Context Overlay & Clean OSS Release (`M8`, `ADR-032`, `F23`)

### Running `macy` with Private Context (`./context/`)

1. Keep proprietary industry packs (`packs/industries/<slug>/`), brand fixtures (`fixtures/seed_*.yaml`), private `.env.*` files, and founder docs inside git-ignored `./context/` (or set `MACY_CONTEXT_DIR` / `MACY_PACKS_DIR` / `MACY_FIXTURES_DIR`).
2. **Docker Pocket (`infra/compose.pocket.yaml`):** automatically bind-mounts `${MACY_HOST_CONTEXT_DIR:-../context}` read-only to `/data/context` (`MACY_CONTEXT_DIR=/data/context`).
3. **Standard Compose stack:** compose with `infra/compose.context.example.yaml` (or `context/compose.private.yaml`):

   ```sh
   docker compose -f infra/compose.yaml -f infra/compose.context.example.yaml up -d
   ```

4. Workspaces also snapshot resolved industry pack definitions in `workspaces.config["industry_pack_snapshot"]`, so runtime evaluation continues cleanly even if overlay files are later unmounted.

### Building & Verifying the Clean Orphan `macy` Release

1. Run the full verification & OSS hygiene suite (`sh scripts/check.sh`).
2. Build a single clean orphan commit on `oss-main` from the verified tree (`HEAD^{tree}`):

   ```sh
   TREE_COMMIT=$(git commit-tree HEAD^{tree} -m "feat: initial open-macy v0.2.0 open-source release (Apache-2.0)")
   git branch -f oss-main "$TREE_COMMIT"
   uv run python scripts/check_oss_hygiene.py --check-git-history --branch oss-main
   ```

3. Push `oss-main` to the public repository (maintaining app/CLI name `macy` / `content-engine`):

   ```sh
   git remote add open-macy https://github.com/LesserWords/Macy.git
   git push open-macy oss-main:main
   ```

4. Validate `macy` locally and in Pocket/Docker against `./context/` before swapping GitHub repository names, retaining the current private repo (`private-context-v0.2` / `dev`) as the full-history & private context backup.
