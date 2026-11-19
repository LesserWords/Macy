# Tasks

## 1. CI `diff-cover` Push-Event Base Branch Fix (Delivery KR: D1)

- [ ] 1.1 Write failing test (workflow check in `tests/test_compose_and_docker.py`) asserting `.github/workflows/ci.yml` distinguishes `pull_request` (`origin/${{ github.base_ref }}`) from `push` (`github.event.before` with non-zero SHA fallback) and never compares `origin/${{ github.ref_name }}` against itself on push — verify: `uv run pytest tests/test_compose_and_docker.py -k diff_cover` fails before CI fix
- [ ] 1.2 Update `.github/workflows/ci.yml` `diff-cover` step to resolve `COMPARE_REF` cleanly per event (`pull_request` vs `push`) — verify D1: YAML parses cleanly and `uv run pytest tests/test_compose_and_docker.py -k diff_cover --no-cov` exits 0

## 2. Live E2E Rename, Bilingual Selector Fix, and Non-Silent Assertions (`J0–J8`) (Delivery KR: D2)

- [ ] 2.1 Write failing test assertions in `tests/test_live_e2e_harness.py` verifying that `tests/test_live_e2e.py` replaces `test_live_e2e_j0_j4.py`, searches `"Revisão de Rascunhos"` / `"Draft Review"` (never `"Rascunhos e Revisão"` / `"Drafts & Review"`), and does not guard UI assertions behind silent `if count() > 0:` skips when authenticated — verify: `uv run pytest tests/test_live_e2e_harness.py` fails before implementation
- [ ] 2.2 Rename `tests/test_live_e2e_j0_j4.py` → `tests/test_live_e2e.py`, fix J5/J7 selectors to `"Revisão de Rascunhos"` / `"Draft Review"`, harden authenticated UI assertions across J0–J8 cleanly within `complexipy <= 15`, and update `.github/workflows/e2e-live.yml`, `tests/test_live_e2e_harness.py`, `docs/runbook.md`, `docs/guides/developer-guide.md`, and `docs/guides/environment-and-credentials.md` — verify D2: `uv run pytest tests/test_live_e2e_harness.py --no-cov`, `uv run ruff check .`, and `uv run mypy` exit 0

## 3. Compose Dev Bind Mounts (`compose.override.yaml`) (Delivery KR: D3)

- [ ] 3.1 Write failing test in `tests/test_compose_and_docker.py` asserting `compose.override.yaml` mounts `./packs`, `./prompts`, `./locales` (`app`), and `./fixtures` as `:ro` volumes for hot reloading in local development — verify: `uv run pytest tests/test_compose_and_docker.py -k override` fails before edit
- [ ] 3.2 Add read-only bind mounts (`./packs`, `./prompts`, `./locales`, `./fixtures`) to `compose.override.yaml` and update `README.md` and `docs/guides/developer-guide.md` — verify D3: `docker compose -f compose.yaml -f compose.override.yaml config` (when Docker available) and `uv run pytest tests/test_compose_and_docker.py -k override --no-cov` exit 0

## 4. Regenerate Committed `db/schema.sql` & Reconcile `DATA-MODEL.md` (Delivery KR: D4)

- [ ] 4.1 Write failing test (schema-parity check in `tests/test_schema_constraints.py`) verifying `db/schema.sql` contains all M0–M4 objects (`publications`, `keyword_cache`, `briefs`, `drafts`, `draft_claims`, `quality_reports`, `publications.error`, `publications_workspace_draft_fk`, and `anonymise_user_events`) and `docs/architecture/DATA-MODEL.md` matches migration column types/constraints (`^https?://`, `drafts.decision_note`, `opportunities.created_at`) — verify: `uv run pytest tests/test_schema_constraints.py -k schema_snapshot` fails before regeneration
- [ ] 4.2 Run all migrations on clean Compose `db`, regenerate `db/schema.sql` via `pg_dump --schema-only`, and update `docs/architecture/DATA-MODEL.md` tables, constraints, cascade rules, `anonymise_user_events`, and `retention` grants — verify D4: `uv run pytest tests/test_schema_constraints.py -k schema_snapshot --no-cov`, `uv run pytest tests/test_db.py tests/test_tenant_scoping.py --no-cov` exit 0

## 5. Architecture, ADR Checklists, PRD & Plan Documentation Sync (Delivery KR: D5)

- [ ] 5.1 Write failing test (doc-consistency check in `tests/test_compose_and_docker.py`) catching stale CLI invocation syntax (`cli.py <cmd>` in active operator/feature docs where `macy <cmd>` applies), `Rascunhos e Revisão` / `J0–J4` live-E2E drift, and inconsistent tool references (`poe check`, active `rsshub`) — verify: `uv run pytest tests/test_compose_and_docker.py` fails before doc updates
- [ ] 5.2 Synchronize `docs/adr/ADR-{006,008,011,012,013,017,024}*.md` (updating action checkboxes and current implementation notes without altering historical decision rationale), `docs/architecture/TECH-RADAR.md`, `docs/prd/MVP-PRD.md`, `docs/features/F{01,04,05,08,18,20}*.md`, `docs/guides/developer-guide.md`, and `docs/plans/M5.md` — verify D5: `uv run pytest tests/test_compose_and_docker.py --no-cov`, `npx --yes @fission-ai/openspec@1.16.0 validate --all --strict --no-interactive`, and `sh scripts/check.sh` exit 0
