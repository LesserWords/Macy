# ADR-032: OSS Public-Readiness, Hybrid Context Overlay & Clean Public Release

**Status:** Accepted · **Date:** 2026-11-18 · **Deciders:** Project Maintainer
**Related:** features F01, F03, F04, F08, F09, F20, F21, F22, F23 · ADR-004, ADR-006, ADR-008, ADR-009, ADR-012, ADR-019, ADR-027, ADR-030, ADR-031

## Context
While the PostgreSQL schema (`db/`) and core engine architecture (`engine/`, `app.py`) are already multi-tenant and secret-free in git history, the repository previously tracked maintainer-specific business context and vertical coupling that blocked making it public as a raw open-source project:
1. Internal strategy and persona evaluations (`founder/` — 116 historical files, including `idea.md`, `cfo.md`, `numbers.json`, `customer.json`, and `v1-managed-service/**`).
2. Proprietary brand fixtures (`fixtures/seed_<private-brand>.yaml`), domain-specific vertical assets (`packs/industries/<private-vertical>/`), and their catalog in `docs/guides/brand-fixtures-guide.md` (which previously included local workstation file URIs).
3. Hardcoded single-folder defaults across `engine/config.py` (`packs/industries`), `engine/services.py` (private vertical and private default fixture path), `engine/cli.py`, and `engine/strategy/score.py`.
4. Hardcoded outbound HTTP `User-Agent` citing a private website URL in `engine/integrations/rss.py`, `engine/integrations/trafilatura_extractor.py`, `engine/sitemap.py`, and `spikes/trials/docling_plugin.py`.
5. Domain-specific vertical wording hardcoded inside `prompts/score.md`, whereas `prompts/brief.md`, `prompts/write_blog.md`, and `prompts/evaluate.md` were already domain-agnostic.
6. Personal and company identities in non-production runtime fixtures, Compose overlays, and archived specs (`engine/mcp/mock_store.py`, `engine/mcp/server.py`, `infra/compose.override.yaml`, `infra/compose.e2e.yaml`, and historical notes in `openspec/changes/archive/**`), plus local `.env.prod` and `.env.staging` files stored alongside the codebase.

Furthermore:
- Brand profiles (`brand_profiles.payload`) and sources (`sources`) **already live in PostgreSQL** after seeding, whereas `IndustryPack` (`profile.yaml` + `*.md`) and `FormatPack` (`format.yaml`, `rubric.yaml`) are read from disk at runtime (including in Path B `--stateless` CLI/MCP mode where no DB exists).
- Pocket users (`infra/compose.pocket.yaml`, Path A) need an effortless folder drop-in option (`./context` → `/data/context`) as well as UI-based import/export, while remote/diskless container deployments benefit from a workspace DB fallback snapshot (`workspaces.config["industry_pack_snapshot"]`).

## Options considered
| Option | Complexity | Cost | Fit |
|---|---|---|---|
| **A. Hybrid Layered Folder Search Path (`MACY_CONTEXT_DIR` → Pocket `/data/context` → Built-in OSS) + Workspace DB Snapshot & UI YAML Import/Export** (chosen) | Medium | `$0` | High — supports raw OSS defaults, Pocket drop-in folders, Path B `--stateless`, and diskless DB snapshot fallback with zero new SQL tables |
| B. Pure Database Storage for all packs and fixtures (`industry_packs` / `format_packs` tables) | High | `$0` | Poor — breaks Path B `--stateless` zero-DB CLI/MCP execution, still requires seed files on disk, and adds SQL migration/CRUD overhead |
| C. Single-Folder Replacement, Private Git Fork, or Private Git Submodule | Low–Medium | High maintenance | Poor — duplicates built-in `packs/formats/*`, creates continuous rebase/merge drift across engine updates, or leaks submodule URLs/SHAs |

## Decision
1. **Cascading Multi-Root Context Directory & Pocket `./context` Drop-In Folder (`engine/config.py`, `infra/compose.pocket.yaml`):**
   - Introduce environment-backed overlay settings in `engine/config.py`:
     - `MACY_CONTEXT_DIR` (unified external context root containing optional `packs/` and `fixtures/` subdirectories)
     - `MACY_PACKS_DIR` (optional explicit override for packs directory; defaults to `<MACY_CONTEXT_DIR>/packs` if present)
     - `MACY_FIXTURES_DIR` (optional explicit override for fixtures directory; defaults to `<MACY_CONTEXT_DIR>/fixtures` if present)
     - `MACY_DEFAULT_PACK` (default: `"general"`)
     - `MACY_DEFAULT_BRAND_FIXTURE` (default: resolved `"seed_example.yaml"`)
     - `MACY_USER_AGENT` (default: `"MacyContentEngine/1.0"`)
   - Resolve industry packs, format packs, and brand fixtures across a **3-layer cascading search path** (merged by slug via `list_available_industry_packs()` and `list_available_brand_fixtures()`, higher priority wins):
     1. Explicit environment overlay (`MACY_PACKS_DIR` / `MACY_FIXTURES_DIR` / `MACY_CONTEXT_DIR`)
     2. Pocket persistent drop-in directory (`${MACY_DATA_DIR:-/data}/context`, bind-mounted read-only in `infra/compose.pocket.yaml` from `${MACY_HOST_CONTEXT_DIR:-../context}`)
     3. Built-in OSS defaults (`<repo>/packs` and `<repo>/fixtures`)
   - Ship a git-ignored `./context/` drop-in directory scaffold (`context/README.md`, `!context/README.md` in `.gitignore`) so Pocket users can add custom packs or brand fixtures without modifying tracked files, while inheriting built-in `packs/formats/{blog,blog-short,blog-long}` automatically without copying them.
2. **Hybrid Workspace DB Industry Pack Snapshot & Admin UI YAML Import/Export (`engine/services.py`, `app.py`):**
   - Store a serialized `IndustryPack` snapshot (`industry_pack_snapshot`) inside `workspaces.config` (`JSONB`, zero new SQL tables or migrations) whenever a workspace is created, bootstrapped, or updated with a pack.
   - At runtime (`score`, `brief`, `write`, `evaluate`), resolve the industry pack from the cascading folder search path first, falling back seamlessly to `workspaces.config["industry_pack_snapshot"]` when the pack is absent on disk (e.g., diskless remote container deployment).
   - Expose dynamic pack/fixture selectors (populated from merged layered discovery) and **Brand Profile `.yaml` Import / Export** controls in `app.py` (Brand Profile screen and Setup Wizard Step 3) and CLI.
3. **Built-in OSS Starter Packs (`general` + `b2b-saas`) & Sample Fixture (`seed_example.yaml`):**
   - Ship two bilingual (`pt-BR` / `en-US`) domain-neutral industry packs in the OSS repo: `packs/industries/general/` (default) and `packs/industries/b2b-saas/`.
   - Ship one neutral `fixtures/seed_example.yaml` (`BrandProfile`) as the built-in default sample fixture.
4. **Domain-Agnostic Opportunity Scoring Prompt (`prompts/score.md`) & Neutralized Mock/Compose Identities:**
   - Remove hardcoded domain-vertical wording from `prompts/score.md` and evaluate research records dynamically against the supplied `industry` (`name`, `description`, `markdown_docs`) and `brand` JSON payload, matching `prompts/brief.md`, `prompts/write_blog.md`, and `prompts/evaluate.md`.
   - Refactor `engine/mcp/mock_store.py`, `engine/mcp/server.py`, `infra/compose.override.yaml`, `infra/compose.e2e.yaml`, and test cassettes/fixtures to use `DEMO_*` / `acme-demo` / `owner@example.com` / `admin@example.com` / `owner@example.test`.
5. **Apache-2.0 License, Sanitized `openspec/changes/archive/**` & Automated OSS Hygiene Gate (`scripts/check_oss_hygiene.py`):**
   - Adopt **Apache-2.0** in root `LICENSE`, alongside `CONTRIBUTING.md` and `SECURITY.md`.
   - Retain and sanitize all historical specs under `openspec/changes/archive/**` (replacing identifying personal/company references with neutral examples) so OSS contributors and evaluators retain full spec-driven engineering visibility without context leaks.
   - Enforce `scripts/check_oss_hygiene.py` inside `scripts/check.sh` to fail deterministically on forbidden proprietary tokens, local workstation `file://` filesystem URIs, forbidden context directories (`founder/`), or missing governance files.
6. **Dual-Repo Public Release to `https://github.com/LesserWords/open-macy.git` & Private Context Cutover:**
   - Maintain the application/project name as **`macy`** (`content-engine` / `Macy`).
   - Preserve the current private repository (`macy`) as the private full-history & business-context repository (`founder/`, private product brand fixtures, private vertical pack, `.env.prod`, `.env.staging`, and `compose.private.yaml`).
   - Publish the sanitized OSS codebase as a clean orphan initial release history to `https://github.com/LesserWords/open-macy.git`, verify `scripts/check_oss_hygiene.py --check-git-history` and `uv build` cleanliness, run the OSS `open-macy` engine locally/in production consuming private context via `MACY_CONTEXT_DIR` / `./context`, and swap GitHub repository names once cutover is verified.

## Consequences
- **Easier:** Anyone can clone, install, and run Macy raw with two neutral bilingual packs (`general`, `b2b-saas`), drop custom packs/fixtures into Pocket `./context/`, import/export `.yaml` profiles in the UI, or use Path B `--stateless` without PostgreSQL. The maintainer runs the untouched OSS engine (`open-macy`) over private data stored in the private repository with zero merge drift and clean public git history.
- **Harder:** Existing unit, MCP mock, and contract tests coupled to private demo slugs, private vertical packs, or personal handles, plus archived `openspec/changes/archive/**` files, had to be sanitized in lockstep, and the `prompts/score.md` SHA-256 hash updates when generalized.

## Revisit when
- Multi-pack community contributions justify a dedicated remote pack registry or package-based pack discovery (`entry_points`) beyond `MACY_CONTEXT_DIR` directories and DB snapshots.

## Action items
- [x] `M8-01` — Implement hybrid 3-layer context search path (`MACY_CONTEXT_DIR`, Pocket `/data/context`, built-in OSS), `workspaces.config.industry_pack_snapshot` DB fallback, UI `.yaml` import/export, configurable `MACY_USER_AGENT`, `general` + `b2b-saas` starter packs, `seed_example.yaml`, and `infra/compose.pocket.yaml` + `infra/compose.context.example.yaml`.
- [x] `M8-02` — Generalize `prompts/score.md` and neutralize `engine/mcp/mock_store.py`, Compose dev/E2E overlays, VCR cassettes, and tests.
- [x] `M8-03` — Configure private context layout (retaining `founder/`, proprietary fixtures, private vertical packs, `.env.*` in the private context folder/repo), add Apache-2.0 `LICENSE` / `CONTRIBUTING.md` / `SECURITY.md`, sanitize docs and `openspec/changes/archive/**`, and wire `scripts/check_oss_hygiene.py` into `scripts/check.sh`.
- [x] `M8-04` — Verify private overlay & DB snapshot E2E parity (`test_private_overlay_smoke.py`), wheel/sdist build hygiene (`test_package.py`), `--check-git-history` sanitization gate, and clean orphan release runbook for `https://github.com/LesserWords/open-macy.git`.
