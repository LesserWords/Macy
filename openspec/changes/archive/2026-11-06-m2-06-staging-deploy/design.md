# Design: Staging on the VPS

## Topology
One VPS, Compose project per environment (`-p macy-staging`, later `macy-prod`), each with its own `.env`, database volume and subdomain (ADR-012). `compose.yaml` + `compose.prod.yaml` form the staging stack; `compose.override.yaml` (dev port publishing) is never used there, so the database port is not published.

Only one Caddy can bind 80/443 on a host. Ports are configurable (`CADDY_HTTP_PORT`, `CADDY_HTTPS_PORT`); the shared edge for staging plus prod is a decision deferred to M4-06.

## Deploy sequence (`scripts/deploy.sh`)
1. Preflight: `.env` exists with mode 600, required keys non-empty, no `CHANGE_ME` placeholders (override `ALLOW_PLACEHOLDER_SECRETS=1` for local rehearsal only).
2. Remember the currently running app image tag.
3. `pull` the new tag.
4. Start `db` (no-op if healthy), then run `migrate` as a one-shot **before** recreating `app`/`worker`. A failing migration aborts the deploy; the old containers keep running.
5. `up -d --no-build` for `app`, `worker`, `caddy`; wait for healthy.
6. Smoke test `/_stcore/health` through Caddy. On failure, redeploy the previous tag.

Migrations are forward-only (dbmate `up`); rollback restores the previous app image only. Migrations must stay backward compatible with the previous release (squawk enforces non-blocking DDL).

## Secrets
Never in the repo. The VPS holds `.env` (mode 600) and the Streamlit `secrets.toml`. GitHub holds SSH key and pinned host key as environment secrets. The VPS pulls from GHCR using the workflow's short-lived `GITHUB_TOKEN`, piped over SSH stdin, so no long-lived registry credential is stored on the host.

## Staging safety
`APP_ENV=staging`. The fake-identity login stays unavailable because `MACY_TEST_USER_EMAIL` is never passed to staging containers (asserted by a test); sign-in is Google OIDC only.

## Alternatives considered
- Baking migrations into a custom dbmate image: more moving parts; shipping `db/` in the deploy bundle is simpler and matches the dev flow.
- Long-lived GHCR PAT on the VPS: rejected, larger blast radius.
