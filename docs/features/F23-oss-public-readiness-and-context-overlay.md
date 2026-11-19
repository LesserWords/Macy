# F23 — OSS Public-Readiness, Hybrid Private Context Overlay & Clean Public Release

**Status:** Done · **Milestone:** M8 · **Plan items:** M8-01, M8-02, M8-03, M8-04 · **ADRs:** ADR-032 · **OpenSpec capability:** `oss-context-overlay`

## User story
As an open-source adopter or the project maintainer, I want the public Macy repository (`macy`, maintaining the application name `macy`) to ship as a domain-agnostic engine under Apache-2.0 with neutral starter packs (`general`, `b2b-saas`), sample brand fixture (`seed_example.yaml`), neutral demo identities, sanitized OpenSpec history, and standard OSS governance files—while supporting a **hybrid context architecture** (3-layer folder search path `MACY_CONTEXT_DIR` → Pocket `./context` → built-in OSS, merged by slug, **plus** PostgreSQL `workspaces.config.industry_pack_snapshot` fallback and Admin UI `.yaml` import/export)—so that anyone can run Macy raw (via CLI, Pocket, or stateless MCP) and the maintainer can run the clean OSS engine against private business context kept in the private repository without code modifications, merge drift, or git history leaks.

## Scope
- In:
  - Cascading 3-layer context search path (`MACY_CONTEXT_DIR` / `MACY_PACKS_DIR` / `MACY_FIXTURES_DIR` → `${MACY_DATA_DIR:-/data}/context` → built-in `<repo>/{packs,fixtures}`) plus `MACY_DEFAULT_PACK="general"`, `MACY_DEFAULT_BRAND_FIXTURE`, and `MACY_USER_AGENT="MacyContentEngine/1.0"` across `engine/config.py`, `engine/services.py`, `engine/cli.py`, `engine/strategy/score.py`, and outbound HTTP adapters (`engine/integrations/rss.py`, `engine/integrations/trafilatura_extractor.py`, `engine/sitemap.py`, `spikes/trials/docling_plugin.py`).
  - Hybrid persistence & UI controls: `workspaces.config["industry_pack_snapshot"]` fallback in `engine/services.py` (zero new SQL tables), merged discovery (`list_available_industry_packs()`, `list_available_brand_fixtures()`), Admin UI Brand `.yaml` import/export + Setup Wizard pack/fixture selectors in `app.py`, and Pocket `./context` read-only mount in `infra/compose.pocket.yaml` + git-ignored `context/README.md`.
  - Built-in bilingual (`pt-BR` / `en-US`) OSS starter packs (`packs/industries/general/`, `packs/industries/b2b-saas/`), neutral fixture (`fixtures/seed_example.yaml`), and `infra/compose.context.example.yaml`.
  - Domain-agnostic `prompts/score.md` driven dynamically by the `industry` and `brand` JSON payload.
  - Neutralization of `engine/mcp/mock_store.py`, `engine/mcp/server.py`, `infra/compose.override.yaml`, `infra/compose.e2e.yaml`, test fixtures/cassettes, `README.md`, `docs/`, `openspec/specs/`, and retained `openspec/changes/archive/**`.
  - Separation of maintainer private context (`founder/`, proprietary brand fixtures, private vertical packs, `.env.prod`, `.env.staging`) into the private context archive/repo with a `compose.private.yaml` overlay, while pushing a clean orphan OSS release to `https://github.com/LesserWords/Macy.git`.
  - Apache-2.0 `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, automated CI hygiene gate (`scripts/check_oss_hygiene.py`) wired into `scripts/check.sh`, private overlay + DB snapshot parity smoke test, wheel/sdist build hygiene check, and `--check-git-history` audit.
- Out:
  - Rewriting PostgreSQL multi-tenant tables or adding new `industry_packs` / `format_packs` SQL tables (`ADR-009`, `ADR-010`).
  - Hosting a remote public registry/marketplace for community industry packs.

## Acceptance criteria
- **AC1 (Cascading 3-Layer Context Overlay & Neutral Starter Defaults):** WHEN `MACY_CONTEXT_DIR`, `MACY_PACKS_DIR`, `MACY_FIXTURES_DIR`, `MACY_DEFAULT_PACK`, `MACY_DEFAULT_BRAND_FIXTURE`, or `MACY_USER_AGENT` are unset, THEN Macy defaults to `packs/industries/general` (with `b2b-saas` also available), `fixtures/seed_example.yaml`, and `"MacyContentEngine/1.0"`; WHEN explicit overlay dirs or Pocket `/data/context` folders contain custom packs/fixtures, THEN `config`, `services`, `cli`, `strategy.score`, and HTTP clients resolve them in priority order (`env overlay > Pocket /data/context > built-in OSS`) merged by slug while inheriting built-in `packs/formats/*`.
- **AC2 (Hybrid DB Snapshot Fallback & UI YAML Import/Export):** WHEN a workspace is created or updated with an industry pack, THEN `workspaces.config["industry_pack_snapshot"]` persists the serialized pack so runtime stages (`score`, `brief`, `write`, `evaluate`) succeed even if the pack folder is absent on disk; AND Admin UI / Setup Wizard expose dynamic pack/fixture selectors and Brand `.yaml` import/export.
- **AC3 (Domain-Agnostic Opportunity Scoring Prompt):** WHEN `engine/strategy/score.py` evaluates research records using `prompts/score.md`, THEN the prompt contains zero hardcoded `"optical retail"` wording and instructs the LLM to score against the supplied `industry` (`name`, `description`, `markdown_docs`) and `brand` JSON payload.
- **AC4 (Neutralized MCP Mock Store & Dev/E2E Compose Identities):** WHEN `content-engine mcp --mock` or Docker Compose dev/E2E overlays run, THEN the seeded mock workspace uses `acme-demo` (`general` pack) and `@example.com` / `@example.test` identities with zero personal or company emails/domains.
- **AC5 (Sanitized OpenSpec Archive, Apache-2.0 Governance & Automated OSS Hygiene Gate):** WHEN `scripts/check_oss_hygiene.py` runs (standalone and inside `scripts/check.sh`), THEN it verifies that `founder/`, proprietary brand fixtures, proprietary company domains/emails (across code, docs, `openspec/specs/`, and retained `openspec/changes/archive/`), and local workstation `file://` URIs are absent from all tracked files, and that root `LICENSE` (Apache-2.0), `CONTRIBUTING.md`, and `SECURITY.md` exist and are non-empty.
- **AC6 (Private Overlay & DB Snapshot End-to-End Parity):** WHEN `macy` mounts an external private context folder (containing a custom vertical pack and custom brand fixture) via `MACY_CONTEXT_DIR` / Pocket `./context` OR falls back to `workspaces.config["industry_pack_snapshot"]`, THEN `create-workspace`, `seed-brand`, `seed-sources`, `verify-sources --offline`, and `run-week --offline` execute end-to-end with 100% functional parity.
- **AC7 (Clean Public Release to `macy`, Build Audit & Git History Sanitization):** WHEN `scripts/check_oss_hygiene.py --check-git-history` and `uv build` execute on the clean orphan public release branch targeting `https://github.com/LesserWords/Macy.git` (maintaining application name `macy`), THEN zero historical commits or wheel/sdist artifacts contain files from `founder/`, proprietary fixtures, `.env.prod`/`.env.staging`, or proprietary emails.

## Data
- Existing `workspaces.config` (`JSONB`): optionally stores `industry_pack_snapshot` (`dict[str, Any]`) alongside `enabled_capabilities` (zero SQL schema changes).
- Environment variables:
  - `MACY_CONTEXT_DIR` (optional unified root containing `packs/` and `fixtures/`)
  - `MACY_PACKS_DIR` (optional explicit packs dir override)
  - `MACY_FIXTURES_DIR` (optional explicit fixtures dir override)
  - `MACY_DEFAULT_PACK` (default `general`)
  - `MACY_DEFAULT_BRAND_FIXTURE` (default resolved `seed_example.yaml`)
  - `MACY_USER_AGENT` (default `MacyContentEngine/1.0`)
  - `MACY_HOST_CONTEXT_DIR` (optional host path bind-mounted into Pocket `/data/context:ro`, defaulting to `../context` or `./context`)

## Interfaces
- `engine/config.py`, `engine/services.py`, `engine/cli.py`, `engine/strategy/score.py`, `app.py`
- `engine/integrations/rss.py`, `engine/integrations/trafilatura_extractor.py`, `engine/sitemap.py`, `spikes/trials/docling_plugin.py`
- `engine/mcp/mock_store.py`, `engine/mcp/server.py`
- `prompts/score.md`, `packs/industries/{general,b2b-saas}/`, `fixtures/seed_example.yaml`, `context/README.md`
- `infra/compose.pocket.yaml`, `infra/compose.override.yaml`, `infra/compose.e2e.yaml`, `infra/compose.context.example.yaml`, `.env.example`, `.gitignore`
- `scripts/check_oss_hygiene.py`, `scripts/check.sh`, `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`

## Tests
| Seam | Test | E2E journey |
|---|---|---|
| 3-layer context search path, DB snapshot fallback, UI YAML import/export & starter packs | `tests/test_context_overlay.py`, `tests/test_compose_and_docker.py` | — |
| Domain-agnostic scoring prompt & neutralized MCP/Compose defaults | `tests/test_scoring.py`, `tests/test_mcp_*.py`, `tests/test_contract_suites.py` | `J-MCP` |
| OSS hygiene gate, Apache-2.0 governance, sanitized archive & git history audit | `tests/test_oss_hygiene.py` | — |
| Private overlay + DB snapshot parity smoke & wheel/sdist build artifact audit | `tests/test_private_overlay_smoke.py`, `tests/test_package.py` | overlay-smoke |

## Dependencies
`F01`, `F03`, `F04`, `F08`, `F09`, `F20`, `F21`, `F22`, and `M7-04`.
