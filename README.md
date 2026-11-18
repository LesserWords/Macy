<p align="center">
  <img src="docs/assets/macy.svg" alt="Macy — Content Intelligence & SEO/GEO Audit Engine" width="280" />
</p>

# Macy — Content Intelligence & SEO/GEO Audit Engine

**Macy** is a modular, multi-workspace editorial intelligence, technical SEO/GEO auditing, and content compliance platform built for blogs, landing pages, and content websites.

Unlike single-prompt AI writers that fabricate statistics or lock you into a single CMS, Macy works across WordPress, Webflow, headless CMSs, and custom websites—combining a PostgreSQL-backed pipeline, deterministic quality checks, isolated rubric evaluation, human approval gates, and a dual-transport MCP server, bilingual out of the box (`pt-BR` and `en-US`) with strict multi-tenant isolation and hard USD spend caps.

---

## Core Workflows

Macy runs cleanly across three complementary modes:

### 1. Sourced Editorial Pipeline (Human-Gated)

```text
Git Packs (industry, format, prompts) + Postgres Workspace Config (brand, campaign, sources, encrypted integrations)
  │
  ▼
1. Research & Dedup     ──► Poll RSS feeds + Brave site search ─► Trafilatura extract ─► date verification
  │                         └─► Canonical URL + pg_trgm title dedup (+ imported blog sitemap inventory)
  ▼
2. Scoring & Report     ──► LLM rubric sub-scores + code-computed weighted total ─► Top 10 Weekly Report
  │                         └─► Emits `topics.awaiting_approval` webhook
  ▼
[GATE 1: Human Owner]   ──► Approve (up to articles/week) or Reject topic in Weekly Report UI
  │
  ▼
3. Keywords & Brief     ──► DataForSEO (30-day cache or labeled estimate) + data/management evidence ─► EditorialBrief
  │
  ▼
4. Write & Evaluate     ──► Draft with FK-enforced `draft_claims` ─► Free deterministic checks (lingua-py, SEO, brand)
  │                         └─► Isolated LLM evaluator ─► PASS, or REVISION (≤ 2 loops to brief/write), or REJECT
  ▼
[GATE 2: Human Owner]   ──► Inspect Markdown, Claims & Sources table, findings, and run cost ─► Approve / Decline
  │
  ▼
5. Publish & Notify     ──► `markdown_export` or signed `http_webhook` (n8n → any CMS) + `article.published` webhook
```

### 2. Agent-Driven Mode — External `.md`/PDF Compliance & MCP Agent Loop
- Bring externally researched or drafted `.md` (with YAML frontmatter) and `.pdf` files via CLI (`content-engine import-draft`), MCP (`macy_ingest_research_documents` + `macy_import_draft`), or the headless Strands Agents runner (`scripts/compliance_agent.py`).
- Enforce zero-LLM-cost deterministic compliance (`deterministic_only=True`) across format word counts (`blog-short`, `blog`, `blog-long`), brand phrase prohibitions, keyword density/placement, subheading density, and tiered bilingual Flesch readability (`en-US` and Martins et al. `pt-BR`).
- Generate deterministic JSON-LD structured data (`Article`, `FAQPage`, `Product`, `LocalBusiness`, `HowTo`) independently from optional LLM rewrite modes (`improve`, `simplify`, `seo_optimize`).

### 3. CMS-Agnostic SEO + GEO Audits, GSC Insights & Route Monitoring
- Score single public URLs (`0–100`, grade `A+..F`) across 7 weighted categories (`onpage`, `performance`, `usability`, `security`, `links`, `social`, and `ai`/GEO readiness) or run a bounded BFS site crawl (`20` free / `50` paid / `200` hard cap) detecting broken links, redirect chains, duplicate metadata, and orphan pages.
- Layer optional modular toggles (`BrowserAuditPort` Playwright/`axe-core` via `--extra audit-browser`, Google PageSpeed Insights `PageSpeedPort`, and paid-plan `RankTrackerPort`) without bloating the core 4 GB VPS footprint.
- Connect Google Search Console (`GSCAnalyticsSource` with hybrid `service_account` or `oauth2` auth + 12h cached URL Inspection), compute striking-distance/decay/cannibalization insights, and run scheduled URL/TLS route monitors that emit Standard Webhooks on score drops or expiring certificates.

---

## Key Capabilities

- **Source-grounded claims:** Every factual claim in a draft (`kind='fact'`) is stored in `draft_claims` with a PostgreSQL foreign key to a fetched `documents` row; unverified publication dates are never labeled as verified.
- **Two-layer quality assurance:** Deterministic checks (`lingua-py` language match, source freshness, cited URL status, keyword placement/density, subheading density, bilingual Flesch readability, FormatPack `word_count`, meta/slug format, brand regex prohibitions, CTA intensity) run before any LLM evaluation call.
- **Swappable integrations (`Protocol` ports):** Configure plugins, credentials, and per-stage LLM models in the Admin UI (`workspace_integrations`, Fernet-encrypted at rest). No vendor keys are baked into code or Compose files.
- **Bilingual (`pt-BR` / `en-US`):** Both the Streamlit Admin UI (`gettext` / Babel) and content/readability pipelines support Portuguese (Brazil) and English (US) independently.
- **Multi-workspace isolation & USD cost caps:** Every tenant table carries `workspace_id` with composite foreign keys, every LLM/API call records exact USD cost in `usage_ledger`, and per-run/monthly caps halt execution before overrun.
- **Outbound Standard Webhooks & MCP server:** Transactional outbox (`events` + `webhook_deliveries`) signs lifecycle and route-monitor alerts with HMAC-SHA256, alongside a fail-closed dual-transport MCP server (`17` tools, `3` resources, `1` prompt).

---

## Quickstart

### Option A — Pocket Zero-Config Stack (`infra/compose.pocket.yaml`, Path A)

Start Macy with auto-generated `/data/.macy_secrets.json`, Python startup migrations, an embedded worker, local PBKDF2 auth, and the 3-step First-Boot Setup Wizard (zero `.env` required):

```sh
docker compose -f infra/compose.pocket.yaml up -d
# Optional local Ollama service:
docker compose -f infra/compose.pocket.yaml --profile local-llm up -d
```

Open [http://localhost:8501](http://localhost:8501) to walk through the 3-step Setup Wizard (owner account, capability switchboard, integration preset `pocket_free`/`local_ollama`/`pro_cloud`, and website URL auto-seed), or initialize unattended:

```sh
docker compose -f infra/compose.pocket.yaml exec app \
  content-engine init --email admin@example.com --password '<secret>' \
  --slug my-ws --name "My Workspace" --preset pocket_free --non-interactive
```

### Option B — Zero-Docker Stateless CLI & MCP (Path B)

Install the slim core extra (`[core]`) and run single-URL SEO/GEO audits, bounded crawls, draft compliance checks, or a `stdio` MCP server with zero Docker and zero PostgreSQL (`--stateless` / `MACY_STATELESS=true`):

```sh
content-engine audit https://example.com --stateless --json
content-engine import-draft --file ./draft.md --format blog-short --stateless --json
content-engine mcp --transport stdio --stateless --check
```

### Option C — Full Multi-Container Local Development

Start the full local stack with Docker Compose (no host Python or PostgreSQL installation required):

```sh
docker compose -f infra/compose.yaml -f infra/compose.override.yaml up -d
```

This starts:
- **`db`**: PostgreSQL 18 (`pg_trgm` enabled) with persistent volume (`db_data`) and port `5432` exposed in dev (`infra/compose.override.yaml`).
- **`migrate`**: `dbmate` one-shot container applying all SQL migrations in `db/migrations/`.
- **`app`**: Streamlit Admin UI at [http://localhost:8501](http://localhost:8501) with `./engine`, `./app.py`, and `./.streamlit` bind-mounted.
- **`worker`**: Background container service (`python -m engine`) executing queued runs and delivering webhooks.

### Bootstrap a Local Workspace & Run an Offline Cycle

Database migrations create the schema and roles without seeding tenant rows. To set up your first local workspace and run an offline end-to-end test cycle (`$0` API spend using `Fake*` adapters):

1. **Create a workspace, add an owner, and seed brand + sources:**

   ```sh
   docker compose exec app content-engine create-workspace acme-demo --name "Acme Demo" --pack general --timezone America/Sao_Paulo
   docker compose exec app content-engine add-owner acme-demo editor@example.com
   docker compose exec app content-engine seed-brand acme-demo
   docker compose exec app content-engine seed-sources --pack general
   ```

2. **Sign in to the Admin UI:**
   - **Local dev/test (`APP_ENV=development` / `test`):** Set `MACY_TEST_USER_EMAIL=editor@example.com` and open [http://localhost:8501](http://localhost:8501) (**strictly refused in production**).
   - **Staging/production (`APP_ENV=staging` / `production`):** Uses **Google OIDC** (`st.login("google")`).

3. **Run an offline editorial cycle:**
   - Save a campaign under **Campaigns** (e.g., `pt-BR`, `BR`, `1` article/week, caps `25.00` / `150.00`).
   - Run research & scoring:

     ```sh
     docker compose exec app content-engine run-week acme-demo --offline --window-days 7
     ```

   - Review **Weekly Report** (Gate 1 approve), generate the brief + draft, inspect **Draft Review** (Gate 2 approve + publish), or test single-URL audits under **SEO & GEO Audit** (`content-engine audit https://example.com`).

> See the **[User & Editorial Guide](docs/guides/user-guide.md)** for all 10 Admin UI screens and **[Developer & CLI Guide](docs/guides/developer-guide.md)** for all 20 CLI commands, tests, and plugin authoring.

---

## Surfaces: Admin UI, CLI & MCP

### Admin UI (`app.py`)

| Sidebar Section | Purpose |
|---|---|
| **Dashboard** | Workspace overview, date, and monthly/run/article cost summary |
| **Weekly Report** | Top 10 scored opportunities, recommendations, Gate 1 Approve/Reject, brief panel, and **Write & Evaluate Draft** trigger |
| **Draft Review** | Gate 2 review: Markdown preview, FAQ/CTA, **Claims & Sources** provenance table, deterministic checks & LLM findings, revision history, Approve/Decline, and Publish |
| **Campaigns** | Locale, target market, articles/week (`1–20`), cron schedule, scoring weights (sum = 100), goal, spend caps, and live monthly cost estimate |
| **Brand Profile** | Versioned (`v1`, `v2`, ...) brand voice, positioning, capabilities, prohibited claims, regex prohibited phrases, and CTA rules |
| **Sources** | Shared and workspace-private trusted sources (`news`, `company`, `data`, `management`; tiers `1–3`; `feed` / `site_search`) |
| **Integrations** | Configure plugins per port (`llm` per-stage models, `feed_source`, `news_search`, `extractor`, `keyword_provider`, `publisher`, `analytics_source`), encrypted secrets, and **Test Connection** |
| **Webhooks** | Create Standard Webhooks subscriptions (one-time `whsec_...` secret), inspect Delivery Log, and **Re-send** |
| **Runs** | View run history, queue manual runs (`Run now`), jump to Weekly Report, and inspect **Cost & Spend Overview** |
| **SEO & GEO Audit** | Run 7-category single-URL SEO+GEO audits and bounded site crawls (`free`/`paid` tiers, `max_pages` up to `200`) |

### CLI (`content-engine`)

Run via `docker compose exec app content-engine <command>` or `uv run content-engine <command>`:

- **Bootstrap & Sources:** `init`, `create-workspace`, `add-owner`, `seed-brand`, `seed-sources`, `verify-sources`, `import-sitemap`
- **Capabilities & Compliance / SEO Audits:** `capabilities {list,enable,disable}`, `import-draft [--stateless]`, `audit [--stateless]`
- **Runs & Delivery:** `run-week`, `run-now`, `run-stage`, `crontab`, `webhooks-deliver`
- **Integrations, MCP & Ops:** `plugins list`, `integrations set`, `integrations preset {list,apply}`, `rotate-key`, `test-sentry`, `mcp [--stateless]`
- **Retention & LGPD:** `purge`, `anonymise-user`

### Model Context Protocol (`content-engine mcp`)

Macy exposes **17 MCP tools**, **3 JSON resources**, and **1 review prompt** over `stdio` (Antigravity, Claude Desktop, Claude Code, Cursor) and fail-closed Bearer HTTPS `streamable-http` (`/mcp` behind Caddy) — see **[MCP Server Guide](docs/MCP.md)**.

---

## Architecture & Plugins

### Ports & Registered Entry Points (`pyproject.toml`)

| Port (`engine.integrations.ports`) | Implemented Plugins | Purpose |
|---|---|---|
| `llm` (`LLMPort`) | `openrouter`, `openai_compat`, `ollama`, `fake` | Structured LLM calls via OpenRouter, any OpenAI-compatible API, or zero-key local Ollama + Instructor |
| `feed_source` (`FeedSourcePort`) | `rss`, `fake` | RSS/Atom polling via `feedparser` + `safe_http` |
| `news_search` (`NewsSearchPort`) | `brave`, `gnews_rss`, `fake` | Domain-scoped `site_search` and news queries via Brave Search API or zero-key Google News RSS |
| `extractor` (`ExtractorPort`) | `trafilatura`, `fake` | Main-text, publication date, author, and outbound link extraction honoring `robots.txt` |
| `keyword_provider` (`KeywordProviderPort`) | `dataforseo`, `autocomplete`, `fake` | Search volume & keyword metrics (`dataforseo` 30-day cache or zero-key `autocomplete` estimates) |
| `publisher` (`PublisherPort`) | `markdown_export`, `http_webhook`, `fake` | Export YAML front-matter Markdown files (with optional JSON-LD) or POST signed `PublishRequest` to n8n / any CMS |
| `analytics_source` (`AnalyticsSourcePort`) | `gsc`, `fake` | Google Search Console (`service_account` / `oauth2`) analytics + 12h-cached URL inspection |

Modular capability and optional audit/rank-tracking layers:
- **`CapabilityPlugin`** (`content_engine.capabilities`): `core-compliance`, `seo-audit`, `editorial-pipeline`, and `route-monitors` dynamically filter Admin UI sidebar, CLI, and MCP tools per workspace (`workspaces.config.enabled_capabilities`).
- **Package Extras (`pyproject.toml`):** `[core]`, `[editorial]`, `[ui]`, `[audit-browser]` (Playwright + `axe-core`), and `[full]`.
- **`BrowserAuditPort` / `PageSpeedPort` / `RankTrackerPort`:** optional accessibility/rendering, Google PSI, and paid-plan SERP tracking.

### Repository Layout

```text
macy/
├── app.py                  # Streamlit Admin UI (imports engine.services + engine.contracts only)
├── engine/                 # Modular monolith (audit, crawl, monitors, mcp, research, strategy, seo, production, quality, integrations)
├── packs/                  # Config packs (industries/{general,b2b-saas}, formats/{blog,blog-short,blog-long} + rubric)
├── prompts/                # Versioned LLM prompts (extract_research, score, brief, write_blog, rewrite_draft, evaluate)
├── fixtures/               # Seed fixtures (seed_example.yaml)
├── locales/                # Bilingual gettext/Babel UI catalogs (pt_BR, en_US)
├── db/                     # dbmate SQL migrations + committed db/schema.sql
├── evals/                  # Regression evaluation cases (cases 01–07) & pytest -m eval suite
├── tests/                  # Unit, contract, schema constraint, tenant scoping, offline & live E2E suites
├── openspec/               # Capability specs (specs/) and archived PR changes (changes/archive/)
├── docs/                   # PRD, ADRs, feature specs, architecture docs, guides, runbook, milestone plans
├── scripts/                # Quality gate runner (check.sh), compliance_agent.py, deploy.sh, backup.sh, check_translations.py, hotspots.py
└── compose*.yaml           # Base, dev override, offline E2E, and staging/prod Caddy + backup overlays
```

---

## Quality Gates & Testing

Run the full local CI verification suite (`ruff`, `mypy --strict`, `complexipy`, `vulture`, `lint-imports`, `jscpd`, `squawk-cli`, `pytest` with >= 90% branch coverage, `100%` `diff-cover`, `hotspots.py`, and `openspec validate`):

```sh
sh scripts/check.sh
```

See the **[Developer & CLI Guide](docs/guides/developer-guide.md)** for offline E2E (`compose.e2e.yaml`), live Playwright E2E (`pytest -m live_e2e`), and LLM regression evals (`pytest -m eval`).

---

## Documentation

| Document | Audience | Contents |
|---|---|---|
| **[Documentation Index](docs/README.md)** | Everyone | Master map of all specs, ADRs, architecture docs, and milestone status |
| **[User & Editorial Guide](docs/guides/user-guide.md)** | Editors / Workspace Owners | Step-by-step setup and workflows across all 10 Admin UI screens |
| **[Developer & CLI Guide](docs/guides/developer-guide.md)** | Engineers | All 20 CLI commands, quality gates, testing, plugins, packs, prompts, i18n, and migrations |
| **[MCP Server Guide](docs/MCP.md)** | Operators / Integrators | Client setup (`Antigravity`, `Claude Desktop`, `Claude Code`, `Cursor`), `stdio` & HTTPS endpoints, 17 MCP tools, Agent-Driven Mode, audits |
| **[Environment & External Credentials Guide](docs/guides/environment-and-credentials.md)** | Operators / Owners | Every `.env` variable, Google OIDC, OpenRouter, Brave, DataForSEO, Sentry, heartbeat, and CI secret |
| **[n8n Webhooks Guide](docs/guides/n8n-webhooks.md)** | Integrators | Receiving & verifying Standard Webhooks lifecycle events in n8n |
| **[n8n CMS Publishing Guide](docs/guides/n8n-publish.md)** | Integrators | Connecting the `http_webhook` publisher to any CMS via n8n |
| **[Operations Runbook](docs/runbook.md)** | Operators | VPS setup, staging/prod Compose deploys, rollback, opt-in MCP operations, backups/restore, retention purge, LGPD anonymisation |
| **[MVP PRD](docs/prd/MVP-PRD.md)** | Product / Engineering | Problem statement, MVP scope, pilot success criteria (§3), and stack rationale |
| **[Data Model](docs/architecture/DATA-MODEL.md)** | Engineers | PostgreSQL schema, composite FKs, state machines, and retention policy |
| **[ADRs](docs/adr/README.md)** & **[Features](docs/features/README.md)** | Product / Engineering | 32 Architecture Decision Records (`ADR-001`–`032`) and 23 feature specs (`F01`–`F23`) |
| **[Delivery Plan](docs/plans/README.md)** | Project Lead | PR workflow, milestone gates (`M0`–`M8`), and close-out evidence |
