# Design: CI Diff Coverage, Playwright E2E Hardening, Compose Dev Mounts, and Schema/Doc Sync (`m5-01c`)

## Context & Principles

This change closes verification, local-dev ergonomics, schema-snapshot, and documentation drift gaps identified after M5-01 (`3fe745f`) while honoring `docs/adr/README.md`: **historical ADRs keep their original decision rationale intact**—only stale action-item checkboxes, implementation notes (`PG 18 runtime / PG 19 GA target`, dropped `rsshub`, current CLI syntax `content-engine <cmd>`), and tooling notes are updated alongside active guides, `DATA-MODEL.md`, `db/schema.sql`, and `M5.md`.

## Decisions

### Decision 1: CI `diff-cover` Comparison on Both `pull_request` and `push`
- **Files:** `.github/workflows/ci.yml`, `tests/test_compose_and_docker.py` (or dedicated workflow assertion in `tests/test_hotspots.py` / `tests/test_live_e2e_harness.py`).
- Currently line 63 of `.github/workflows/ci.yml` executes `--compare-branch="origin/${{ github.base_ref || github.ref_name }}"`, which compares `origin/dev` against `origin/dev` (0 diff lines) on `push` events.
- Replace with explicit event-aware resolution in the CI `test` job:
  - On `pull_request`: `COMPARE_REF="origin/${{ github.base_ref }}"`.
  - On `push`: `COMPARE_REF="${{ github.event.before }}"`; if `$COMPARE_REF` is all-zeros (`0000000000000000000000000000000000000000`, initial push of a new branch) or unreachable, resolve the parent commit (`HEAD^` / `git merge-base origin/dev HEAD`) cleanly, falling back to `origin/dev` / `origin/main` only when distinct from `HEAD`.

### Decision 2: Live E2E Renaming (`test_live_e2e.py`), Selector Fix, and Assertion Hardening
- **Files:** Rename `tests/test_live_e2e_j0_j4.py` → `tests/test_live_e2e.py` (keep backwards-compatibility only if needed, or update all references in `.github/workflows/e2e-live.yml`, `tests/test_live_e2e_harness.py`, `docs/runbook.md`, `docs/guides/developer-guide.md`, and `docs/guides/environment-and-credentials.md`).
- **Selector alignment:** Replace `"Rascunhos e Revisão"` / `"Drafts & Review"` in J5 and J7 with `"Revisão de Rascunhos"` / `"Draft Review"` matching `app.py:67,1714` and `locales/pt_BR/LC_MESSAGES/messages.po:32-33`.
- **No silent false passes:** Replace conditional `if nav.count() > 0:` silent bypasses so that when authenticated shell navigation is present (or `PLAYWRIGHT_STORAGE_STATE` / `REQUIRE_LIVE_SERVER=true` is configured), Journeys J0–J8 assert visible navigation, page content, and Gate/cost markers directly (`expect(...).to_be_visible()` / non-empty assertion), failing loudly if UI selectors break instead of passing vacuously. Extract shared Playwright navigation helper to stay within `complexipy <= 15` and `ruff` limits.

### Decision 3: Local Dev Bind Mounts in `compose.override.yaml`
- **Files:** `compose.override.yaml`, `tests/test_compose_and_docker.py`, `README.md`, `docs/guides/developer-guide.md`.
- Add read-only mounts under `app.volumes`:
  - `./packs:/app/packs:ro`
  - `./prompts:/app/prompts:ro`
  - `./locales:/app/locales:ro`
  - `./fixtures:/app/fixtures:ro`
- Add matching mounts under `worker.volumes`:
  - `./packs:/app/packs:ro`
  - `./prompts:/app/prompts:ro`
  - `./fixtures:/app/fixtures:ro`
- Update `README.md` (`L62`) and `docs/guides/developer-guide.md` (`L42, L51`) so maintainers know edits to packs, prompts, locales, and fixtures reflect immediately without rebuilding the Docker image.

### Decision 4: Regenerate `db/schema.sql` & Sync `docs/architecture/DATA-MODEL.md`
- **Files:** `db/schema.sql`, `docs/architecture/DATA-MODEL.md`, `tests/test_schema_constraints.py`.
- Regenerate `db/schema.sql` cleanly against a live PostgreSQL 18 container with all 10 migrations applied (`20261103000000`–`20261109000003`) using `docker compose exec -T db pg_dump -U owner -d macy --schema-only` (filtering environment-specific noise if any, preserving clean deterministic output matching dbmate schema dump style).
- Add a structural schema-snapshot test in `tests/test_schema_constraints.py` asserting that `db/schema.sql` includes every table/function introduced across M0–M4 (`briefs`, `drafts`, `draft_claims`, `quality_reports`, `publications`, `keyword_cache`, `publications.error`, `publications_workspace_draft_fk`, and `anonymise_user_events`).
- Reconcile `docs/architecture/DATA-MODEL.md` against applied SQL migrations:
  - Align `bigint` integer types (`version`, `schema_version`, `articles_per_week`, `tier`, `key_version`, `attempts`, `last_http_status`, `input_tokens`, `output_tokens`, `cached_tokens`, `latency_ms`, `http_status`, `rank`, `revision`, `idx`).
  - Align `webhook_subscriptions.url CHECK (url ~ '^https?://')` (noting HTTPS enforcement in `safe_http` / production).
  - Add missing columns/constraints/cascades/defaults: `opportunities.created_at`, `drafts.decision_note`, `draft_claims` (`CHECK (idx >= 0)`, `ON DELETE CASCADE`), `quality_reports` (`score`, defaults `'{}'`/`'[]'`, `ON DELETE CASCADE`), `publications` (nullable `draft_id` and `url`, `title`, `error`, `UNIQUE (workspace_id, url)`), `anonymise_user_events(uuid)`, and `retention` grants (`usage_ledger` `SELECT`, `documents` `SELECT, UPDATE`).
  - Clarify PostgreSQL 18 current runtime (`PG 19` target post-GA `2026-11-19`) and `content-engine purge` CLI invocation (`§5`).

### Decision 5: Cross-Repo Documentation & Checklist Reconciliation
- **Files:** `docs/adr/ADR-{006,008,011,012,013,017,024}*.md`, `docs/architecture/TECH-RADAR.md`, `docs/features/F{01,04,05,08,18,20}*.md`, `docs/prd/MVP-PRD.md`, `docs/guides/developer-guide.md`, `docs/runbook.md`, `docs/plans/M5.md`.
- Update `docs/adr/ADR-006` (`vulture` `min_confidence = 60` matches `pyproject.toml`), `ADR-011` (`[x] M0-04`), `ADR-012` (note `rsshub` dropped in M1-11 alongside implementation), `ADR-013` (`[x] M1-01` + runtime creator-only invariant note), `ADR-017` (`[x] M2-04`), and note `content-engine <cmd>` as the CLI entrypoint where stale `cli.py <cmd>` commands remain in active docs/features.
- Sync `docs/architecture/TECH-RADAR.md` §3 (`RSSHub/GDELT` dropped note instead of implying live savings) and remove ambiguous `PG 19 GA` phrasing where PG 18 is active.
- Reconcile `docs/prd/MVP-PRD.md` active references cleanly (`scripts/check.sh` vs removed `poe check`, `mypy --strict`, dropped `rsshub`, external cron/`content-engine crontab` vs bundled `supercronic`, and PG 18 until 19 GA). Note `pyproject.toml` `[tool.pyrefly]` cleanup or document whether `[tool.pyrefly]` is experimental vs active `mypy` gate (explicitly leave `[tool.pyrefly]` removal decision or clear note cleanly).
- Update `docs/plans/M5.md` and `docs/runbook.md` so J0–J8 live E2E paths reference `tests/test_live_e2e.py` and M5-01 completion criteria align cleanly between local verified work and pending live VPS steps.

## Risks & Mitigations

- **Docker required to run `pg_dump --schema-only` during `m5-01c` implementation:** Note in `tasks.md` that Slice 4 must run against the Compose `db` + `migrate` stack (`docker compose up -d db && docker compose run --rm migrate up`) during `/opsx:apply` on a host/runner with Docker running.
- **Historical vs active docs:** Avoid rewriting historical ADR design reasoning or archived milestones (`M0`–`M4`) beyond status checkboxes and implementation accuracy notes so project history stays auditable.
