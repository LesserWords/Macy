# Proposal: CI Diff Coverage, Playwright E2E Hardening, Compose Dev Mounts, and Schema/Doc Sync

**Plan Item:** M5-01c (post-M5-01 release audit follow-up) · **Features:** F20 · **ADRs:** ADR-006, ADR-011, ADR-012, ADR-018, ADR-019

## Why

Post-M5-01 repository inspection identified CI, test harness, local Compose developer experience, schema snapshot, and documentation consistency gaps that slow down new maintainers and undermine verification confidence:

1. **Broken `diff-cover` comparison on `push` in `.github/workflows/ci.yml`:** Using `--compare-branch="origin/${{ github.base_ref || github.ref_name }}"` works on `pull_request` (`github.base_ref`), but on `push` to `dev` or `main` it diffs `origin/<branch>` against `origin/<branch>` (0 changed lines — a silent false pass).
2. **Stale Playwright selectors, shallow assertions, and `J0–J4` naming in live E2E:** `tests/test_live_e2e_j0_j4.py` searches for `"Rascunhos e Revisão"` / `"Drafts & Review"`, whereas `app.py` and `locales/` render `"Draft Review"` / `"Revisão de Rascunhos"`. Several live journeys skip assertions under `if locator.count() > 0:`, and `openspec/specs/tooling/spec.md` + `docs/runbook.md` still describe `J0–J4` instead of `J0–J8`.
3. **Missing Compose dev bind mounts in `compose.override.yaml`:** Only `./engine`, `./app.py`, and `./.streamlit` are mounted into local dev containers, leaving `./packs`, `./prompts`, `./locales`, and `./fixtures` baked inside the image until rebuild.
4. **Stale `db/schema.sql` snapshot:** `db/schema.sql` stops at M2 migrations and lacks `publications`, `keyword_cache`, `briefs`, `drafts`, `draft_claims`, `quality_reports`, M4 `publications` alterations, and `anonymise_user_events(uuid)` (`20261106000000`–`20261109000003`).
5. **Architecture, ADR, PRD, and plan documentation drift:** `docs/architecture/DATA-MODEL.md` differs from actual migrations (`bigint` column types, `webhook_subscriptions.url ~ '^https?://'`, `drafts.decision_note`, `opportunities.created_at`, `ON DELETE CASCADE`, nullable `publications.draft_id`/`url`, `anonymise_user_events`), while `docs/adr/ADR-{006,011,012,013,017}`, `docs/architecture/TECH-RADAR.md`, `docs/prd/MVP-PRD.md`, `docs/features/`, `docs/guides/developer-guide.md`, and `docs/plans/M5.md` retain stale `cli.py <cmd>`, `poe check`, `pyrefly`, `rsshub`, `supercronic`, and unchecked action-item artifacts.

## What Changes

- Fix `.github/workflows/ci.yml` `diff-cover` base resolution for `push` events (`github.event.before`, handling all-zero initial branch SHA safely) while preserving `origin/${{ github.base_ref }}` on `pull_request`.
- Rename `tests/test_live_e2e_j0_j4.py` to `tests/test_live_e2e.py` (updating `.github/workflows/e2e-live.yml`, `tests/test_live_e2e_harness.py`, and docs), align Playwright locators to `"Draft Review"` / `"Revisão de Rascunhos"`, enforce non-silent assertions across J0–J8 when authenticated, and update `tooling` spec to `J0–J8`.
- Add read-only bind mounts for `./packs`, `./prompts`, `./locales` (`app`), and `./fixtures` in `compose.override.yaml`, updating `README.md` and `docs/guides/developer-guide.md`.
- Regenerate `db/schema.sql` from a clean PostgreSQL 18 database with all 10 migrations applied (`pg_dump --schema-only`) and add an automated schema/migration drift check.
- Synchronize `docs/architecture/DATA-MODEL.md` against actual migrations and update implementation statuses, completed ADR action checkboxes (without rewriting original decision rationale), `docs/architecture/TECH-RADAR.md`, `docs/prd/MVP-PRD.md`, `docs/features/`, `docs/runbook.md`, and `docs/plans/M5.md`.

## Capabilities

### Modified Capabilities
- `tooling`: Push-event `diff-cover` comparison fix, `J0–J8` Playwright E2E selector/assertion hardening, and architecture/ADR/plan documentation consistency.
- `operations`: Read-only `./packs`, `./prompts`, `./locales`, and `./fixtures` dev bind mounts in `compose.override.yaml`.

### Added Capabilities
- `access`: Committed `db/schema.sql` snapshot synchronized with all applied SQL migrations and `docs/architecture/DATA-MODEL.md` parity.

## Key Results: Delivered

All `D1`–`D5` must pass before `M5-01c` is considered delivered.

| KR | Result | Validation / Evidence |
|---|---|---|
| **D1** | `.github/workflows/ci.yml` `diff-cover` compares against `origin/${{ github.base_ref }}` on `pull_request` and `github.event.before` (with initial-SHA fallback) on `push` | `uv run pytest tests/test_compose_and_docker.py -k diff_cover --no-cov` exits `0` |
| **D2** | `tests/test_live_e2e.py` replaces `test_live_e2e_j0_j4.py`, uses `"Revisão de Rascunhos"` / `"Draft Review"` selectors, and enforces non-silent authenticated `J0–J8` assertions | `uv run pytest tests/test_live_e2e_harness.py --no-cov && uv run ruff check . && uv run mypy` exits `0` |
| **D3** | `compose.override.yaml` mounts `./packs`, `./prompts`, `./locales`, and `./fixtures` read-only (`:ro`) for local dev hot-reload | `uv run pytest tests/test_compose_and_docker.py -k override --no-cov` exits `0` |
| **D4** | `db/schema.sql` and `docs/architecture/DATA-MODEL.md` match all applied M0–M4 migrations (`publications`, `keyword_cache`, `briefs`, `drafts`, `draft_claims`, `quality_reports`, `anonymise_user_events`) | `uv run pytest tests/test_schema_constraints.py -k schema_snapshot --no-cov` exits `0` |
| **D5** | Active docs, ADRs, Tech Radar, PRD, and feature specs contain zero stale `cli.py <cmd>`, `poe check`, `Rascunhos e Revisão`, or active `rsshub` references | `uv run pytest tests/test_compose_and_docker.py --no-cov && sh scripts/check.sh` exits `0` |

## Out of Scope

- Cross-table key rotation, `RETENTION_DB_PASSWORD` enforcement, `.env` OIDC template cleanup, and production authenticated smoke/rollback hardening (`2026-11-10-m5-01a-ops-deploy-hardening`).
- Runtime integration credential validation, Compose DataForSEO forwarding, scheduler versioning, CLI `run-stage`/`run-now`, UI dev fallback/dashboard cap/translations, and n8n/user guides (`2026-11-10-m5-01b-runtime-ui-parity`).
- Altering historical ADR decisions or upgrading PostgreSQL 18 → 19 prior to PG 19 GA (`2026-11-19` per `ADR-011`).
