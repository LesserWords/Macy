# Content Intelligence Engine — MVP PRD

**Status:** Proposed · **Date:** 2026-11-03 · **Source:** `modular_content_intelligence_engine_blueprint.docx`
**Size:** L (multi-week, new system) · **Pilot:** industry `general`, brand `acme-demo`, campaign `organic-growth`, format `blog`

**Docs map:** [docs index](../README.md) · [features](../features/README.md) · [ADRs](../adr/README.md) · [plans](../plans/README.md) · [data model](../architecture/DATA-MODEL.md) · [tech radar](../architecture/TECH-RADAR.md)

---

## 1. Problem

Acme needs a steady flow of SEO content that attracts general decision-makers (store owners, managers, buyers), grounded in real, verified industry news. Doing this by hand is slow and inconsistent; doing it with one big "AI writer" produces unverified, generic, salesy content.

The blueprint defines the target system (6 modules, 4 config packs, independent QA, feedback loop). This PRD defines the **smallest slice that proves the pipeline works**, and the tech to build it so later phases are additions, not rewrites.

## 2. MVP scope

The blueprint's own final recommendation: *research → editorial brief → article → quality review; expand only after this is reliable.* That is the MVP.

### In scope (MVP)

Feature specs with acceptance criteria: **[../features/](../features/README.md)** (F01–F20).

| # | Capability | Blueprint ref |
|---|-----------|---------------|
| 1 | Config packs loaded from files: 1 industry, 1 brand, 1 campaign, 1 format (blog) | §4 |
| 1a | **User-chosen run settings** (admin UI): language + market (locale), articles per week, trusted-source list (add / edit / disable) — see §5.7 | §4, §13 |
| 2 | Research engine: trusted-source feeds + news search API → extract → date-verify → dedup → store | §3A |
| 3 | Topic scoring with the §7 rubric (LLM gives evidence-backed sub-scores, code computes weighted total) | §7 |
| 4 | Weekly editorial report (top 10 + top 3 recommendations) | §7 |
| 5 | Human approval of topics (gate 1) | §13 |
| 6 | Editorial brief + keyword/search-intent data for the approved topic | §3C, §3E, §6 |
| 7 | Blog article writer with claim → source provenance | §3D, §8 |
| 8 | Independent evaluator: deterministic checks + LLM rubric → PASS / REVISION / REJECT, routed to responsible module, max 2 revision loops | §3F, §8 |
| 9 | Human approval of final article (gate 2); publish through the generic `Publisher` port — MVP plugin exports Markdown + SEO metadata; CMS plugin added later against the same contract | §13 |
| 10 | Regression test suite with the 7 cases from §8 | §8 |
| 11 | Every published article stores `brief_id ↔ published_url` so analytics can be joined later | §10 |

### Out of scope (later phases — explicitly deferred)

| Deferred | Phase | Add when |
|----------|-------|----------|
| Concrete CMS plugin (WordPress, Webflow, …) — CMS not chosen yet; `Publisher` port + contracts ship in MVP | 3 | CMS decided; plugin must pass the Publisher contract suite |
| Image generation + ALT text | 2 | Text pipeline passes pilot criteria |
| Newsletter / LinkedIn / social formats | 3 | Blog format stable; each = new format pack + rubric |
| Search Console / GA4 feedback loop | 4 | ≥ 8 weeks of published articles exist (no data before that) |
| Semantic dedup with embeddings (pgvector) | 2+ | Trigram dedup measurably misses duplicates |
| Second workspace (customer) / second industry pack | 4 | Pilot criteria met; schema is already multi-workspace (§5.9), so this is onboarding, not a rewrite |
| Autonomous agents / multi-agent | — | A step needs open-ended exploration that a fixed workflow can't express |
| Durable workflow engine (Hatchet/Temporal) | — | See ADR-001 triggers |

## 3. Success criteria (pilot = 4 consecutive weekly runs)

- [ ] Weekly run completes unattended from cron to report in < 30 min, ≥ 4/4 weeks.
- [ ] Report contains 10 stories, each with original URL, publication date, summary, angle, funnel stage, score, confidence. 0 stories with missing/unverified date shown as "verified".
- [ ] Editor approves ≥ 1 of the top 3 recommendations in ≥ 3/4 weeks (proxy for scoring quality).
- [ ] 100 % of factual claims in final drafts map to a source in the evidence package (deterministic check).
- [ ] 0 invented statistics / search volumes reach gate 2 (spot-checked by editor; volumes without API data are labelled "estimate").
- [ ] ≥ 70 % of drafts reach PASS within 2 revision loops.
- [ ] Editor rates ≥ 3/4 approved articles "publishable with light edits" (< 20 min editing).
- [ ] Regression suite: all 7 §8 cases produce expected behaviour; runs in CI on every prompt/pack change.
- [ ] LLM + API cost per published article logged; target < US$5.
- [ ] All §9 quality gates enforced in CI from M0; 0 threshold overrides merged without an OpenSpec change.
- [ ] Swapping the news-search vendor (brave ↔ exa) or the LLM model for a stage is done by the user in the Integrations screen: no code, env or Compose change.
- [ ] A user connects n8n (webhook subscription + `http_webhook` publisher) without developer help, and receives signed events.
- [ ] Every environment (dev, CI, staging, prod) runs from the same Docker image via Compose.
- [ ] Changing locale, articles/week or the source list is done in the admin UI with no code or file change, and takes effect on the next run.
- [ ] Live E2E suite (§9.6) passes the full user flow against real APIs before every release and on the nightly run.

## 4. Users

| User | Needs |
|------|-------|
| Editor / marketer (1–2 people) | Choose language + market and articles/week; manage trusted sources; weekly report, approve/reject topics, review drafts with visible sources and evaluator findings, publish/export article |
| Operator / developer (1) | Edit packs and prompts in git, rerun a stage, read call logs, run regression suite |

No external users. Login: **Google sign-in** (OIDC) via Streamlit's built-in auth (`st.login("google")`, `st.user.email`); access = the signed-in email matches a `users` row with a `memberships` row in the workspace (replaces a flat allowlist; first owner seeded by CLI). `users.id` is the "who" in `events` and `decided_by`.

Approver (MVP): the **campaign creator** approves gates 1 and 2 for that campaign (`campaigns.created_by`), and must hold role `owner` in the workspace. Only role in MVP: `owner`. Deliberate simplification: `editor`/`approver`/`viewer` roles and delegation added when a workspace has more than one person.

## 5. Architecture

### 5.1 Shape: modular monolith + DB state machine

One Python service. Modules are packages with typed input/output contracts (blueprint §6). Each stage reads rows in state *X*, writes its output and moves them to state *Y*. Postgres is the single source of truth for task state, evidence and history. A failed stage is re-run from its input row — that gives retries and resumability without a workflow engine.

```
   ┌── packs/ (git, product-owned) ──┐   ┌── Postgres (per workspace, customer-owned) ──┐
   │ industry · format(+rubric) ·    │   │ brand profile · campaign settings · sources · │
   │ prompts                         │   │ integrations + secrets (encrypted) + webhooks │
   └───────────────┬─────────────────┘   └───────────────────────┬──────────────────────┘
                   └──────────── loaded + validated (Pydantic) per run, scoped by workspace_id
cron (weekly) ─► pipeline.py ─► research ─► scoring ─► REPORT ─► [gate 1: human approves topic]
                                                                        │
              [gate 2: human approves] ◄─ evaluate ◄─ write ◄─ brief+keywords
                       │                    │ REVISION → route to brief|write (max 2)
                       ▼                    └ REJECT → back to topic pool
                 export Markdown + meta  (published_url recorded → future analytics join)

  Postgres: runs · documents · research_records · opportunities · briefs · drafts · draft_claims · quality_reports · publications · events · usage_ledger (../architecture/DATA-MODEL.md)
  Admin UI (Streamlit) + CLI ─► engine/services.py (the only entry point; workspace-scoped, checks membership)
```

### 5.2 Mapping blueprint concepts to code (no over-build)

| Blueprint concept | MVP implementation |
|---|---|
| Tool | Plain Python function inside the core (`dedup()`, `check_claims()`); anything that talks to an external service is an **integration plugin** (§5.6) |
| Skill | Versioned prompt file in `prompts/` (Markdown), loaded with the relevant pack sections |
| Agent | **None in MVP.** Every LLM step is one structured call inside a code-controlled workflow |
| Workflow | `pipeline.py` + status columns in Postgres |
| Configuration | Product-owned (industry, format, prompts) in `packs/` git; customer-owned (brand, campaign, sources) in Postgres per workspace — both validated by the same Pydantic models |
| Evaluator | Separate module, separate prompt, never sees the writer's prompt or reasoning |
| Contracts | Pydantic v2 models in `contracts.py` (exportable to JSON Schema) |

### 5.3 Contracts (Pydantic, from blueprint §6)

| Module | Input | Output |
|---|---|---|
| research | `IndustryPack`, date range | `ResearchPackage` = list of `ResearchRecord` (headline, source, url, canonical_url, published_at, event_date, summary, facts[], entities[], source_type, verification_status) |
| strategy.score | `ResearchRecord` + audience + campaign | `Opportunity` (sub-scores each with `evidence` + `is_estimate`, weighted total computed in code, funnel_stage, angle, confidence) |
| strategy.brief | approved `Opportunity` + `KeywordData` + brand + format | `EditorialBrief` (exact §6 example fields + `required_facts[]` each with `source_id`) |
| production.write | `EditorialBrief` + evidence + brand + format | `ContentDraft` (title, meta, slug, body_md, faq[], cta, `claims[]` = {text, source_id, kind: fact\|analysis\|recommendation}) |
| quality.evaluate | `ContentDraft` + brief + evidence + rubric | `QualityReport` (verdict PASS\|REVISION\|REJECT, findings[] = {criterion, severity, detail, responsible_module}) |
| publish (port) | `PublishRequest` = approved `ContentDraft` + `locale` + `destination` + `mode` (draft\|scheduled\|live) + `scheduled_at?` | `PublishedContent` (destination, external_id, url?, status, published_at?, raw?) |

All contracts carry `locale` (BCP-47; MVP supported: `pt-BR`, `en-US`) and `market` (ISO-3166 country) so every module, prompt and plugin is locale-aware.

Rule: modules import contracts only, never each other.

### 5.4 Quality system (blueprint §8)

Two layers, cheap first:

1. **Deterministic checks (code, free, run first):** draft language matches campaign locale (**lingua-py**, restricted to pt/en); every `claims[].source_id` exists in the evidence package; source dates inside freshness window for format; every cited URL was fetched successfully; primary keyword in title/H1/meta; meta length; slug format; brand-profile prohibited claims / phrases (regex list); TOFU drafts with CTA intensity above campaign limit.
2. **LLM evaluator:** rubric from the format pack (§8 format-specific profile table → weights per criterion). Returns per-criterion findings with the responsible module. Fails on any *critical* criterion → REVISION or REJECT.

Revision routing: findings tagged `brief` regenerate the brief; `write` regenerates the draft with findings attached; `research` → REJECT to topic pool. Max 2 loops, then surfaced to editor as-is with findings.

**Regression suite:** `evals/cases/*.yaml` (the 7 §8 cases as fixed fixtures: input record → expected priority band / verdict) run by `pytest`. Assertions are on bands and verdicts, not exact text. Runs on every change to `prompts/` or `packs/`.

### 5.5 Project structure

```
macy/
├── openspec/                 # specs/ (source of truth per capability) + changes/ (proposals, tasks, deltas)
├── engine/
│   ├── contracts.py          # all Pydantic I/O models
│   ├── config.py             # load + validate packs; plugin_config(workspace_id, port) over workspace_integrations
│   ├── services.py           # application actions (approve_topic, save_settings, run_now, …); sole entry for UI/CLI/API
│   ├── db.py                 # psycopg, queries; every function takes workspace_id
│   ├── llm.py                # structured-call helper over the LLM port: schema in, Pydantic-validated out, logs to usage_ledger
│   ├── events.py             # write to outbox; webhook delivery (signing, retries) run by the worker
│   ├── safe_http.py          # the only HTTP client for user-supplied URLs (SSRF guard, limits)
│   ├── integrations/         # ports.py (Protocols), registry.py (entry-point discovery), fakes.py (test doubles)
│   ├── research/             # collect.py (uses Feed/Search/Extractor ports), dedup.py
│   ├── strategy/             # score.py, brief.py, report.py
│   ├── seo/                  # keywords.py (uses KeywordProvider port)
│   ├── production/           # write.py
│   ├── quality/              # checks.py (deterministic), evaluate.py (LLM)
│   ├── pipeline.py           # stage runner / state transitions
│   └── cli.py                # run-week, run-stage <id>, export <draft_id>, plugins list
├── plugins/                  # first-party adapters, each registered via entry point
│   ├── openrouter/  rss/  brave/  exa/  trafilatura/  dataforseo/  markdown_export/  http_webhook/
├── packs/
│   ├── industries/general/  profile.yaml, sources.yaml, audience.md, terminology.md, challenges.md, exclusions.md
│   └── formats/blog/               format.yaml, rubric.yaml, examples/   # reference library (§8)
│   # brand + campaign are NOT here: per-workspace rows in Postgres (§5.9). Acme Demo brand seeded via fixtures/seed_example.yaml
├── prompts/                  # score.md, brief.md, write_blog.md, evaluate.md
├── tests/                    # unit/ (fast, offline), contract/ (every plugin vs its port), e2e/
├── evals/cases/ + test_regression.py   # live-LLM regression suite (§8 cases)
├── db/migrations/ + db/schema.sql   # dbmate plain-SQL migrations (linted by squawk) + committed schema dump
├── app.py                    # Streamlit admin — imports engine.services + engine.contracts only
├── pyproject.toml            # deps (uv), ruff/mypy/coverage/complexipy/importlinter config, plugin entry points
├── .pre-commit-config.yaml
├── Dockerfile                # one image: app + worker
└── compose.yaml, compose.override.yaml, compose.e2e.yaml, compose.prod.yaml   # §6
```

Simplified from blueprint §5: `skills/` collapses into `prompts/`; `tools/` splits into core functions (`engine/`) and external integrations (`plugins/`).

### 5.6 Integrations as plugins (nothing hard-coded)

Every external service sits behind a **port** (a `typing.Protocol` that speaks only contract types). Core modules depend on ports, never on vendors. Concrete adapters are **plugins** discovered at runtime through Python entry points (`importlib.metadata`, stdlib). **Which plugin fills each port, and all of its settings and credentials, are chosen by the user in the admin UI per workspace (ADR-008)** — nothing about an external service is fixed in code or deploy files.

| Port | MVP plugins | Later plugins |
|---|---|---|
| `LLM` | `openrouter` (ADR-007) | `anthropic_direct`, other gateways |
| `FeedSource` | `rss` (feedparser) | sitemap, newsletter inbox |
| `NewsSearch` | `brave` (S4: one plugin in M1); `gdelt` (free, trial M1) | exa, tavily |
| `Extractor` | `trafilatura` | firecrawl (JS-heavy sites) |
| `KeywordProvider` | `dataforseo` | semrush, gsc_queries |
| `Publisher` | `markdown_export`, **`http_webhook`** (POSTs `PublishRequest` to a user URL — e.g. an n8n workflow that writes to any CMS — and reads `PublishedContent` back) | wordpress, webflow, newsletter, linkedin |
| `AnalyticsSource` | — | search_console, ga4 (Phase 4) |

**Outbound events (webhooks):** separate from ports. The core writes domain events to an outbox table; the worker delivers them to user-configured webhook subscriptions (ADR-008). This is how n8n, Zapier, Slack bots or any other tool reacts to the engine without code changes.

| Event | When |
|---|---|
| `run.completed` / `run.failed` | Weekly or ad-hoc run ends |
| `topics.awaiting_approval` | Report ready (gate 1 pending) |
| `draft.awaiting_approval` | Draft passed evaluation (gate 2 pending) |
| `topic.decided` / `draft.decided` | Approve / reject at a gate |
| `article.published` | `Publisher` returned `PublishedContent` |
| `budget.cap_reached` | Per-run or monthly cap hit |

Payload: `{event, event_id, workspace_id, occurred_at, data}` where `data` is the relevant contract (e.g. `PublishedContent`). Signed per the Standard Webhooks spec (HMAC-SHA256 over id.timestamp.body; per-subscription secret); receivers dedupe by `webhook-id` (= event id).

```toml
# pyproject.toml — first-party plugins register exactly like third-party ones
[project.entry-points."macy.news_search"]
brave = "plugins.brave:BraveSearch"
exa   = "plugins.exa:ExaSearch"
```

```python
# plugins/brave/__init__.py — the Settings model drives the UI form, validation and secret handling
class Settings(BaseModel):
    api_key: SecretStr                      # rendered as a password field, stored encrypted, never shown back
    max_results: int = Field(20, ge=1, le=50)

class BraveSearch:                          # implements NewsSearch
    def __init__(self, settings: Settings) -> None: ...
    def healthcheck(self) -> None: ...      # "Test connection" button
    def search(self, query: str, locale: str, market: str, since: date) -> list[ResearchRecord]: ...
```

No locale in plugin settings: locale/market come from run settings (§5.7) and are passed on every call.

Each plugin maps the generic `locale`/`market` to its vendor codes internally (e.g. DataForSEO location/language codes) and raises `UnsupportedLocale` if it can't serve it — surfaced in the UI when the user picks a locale.

**CMS (undecided):** the `Publisher` port and `PublishRequest`/`PublishedContent` contracts are final in MVP, with a shared contract suite (idempotent re-publish by `draft_id`, draft mode never goes live, returned `url` recorded in `publications`). The MVP plugins are `markdown_export` and `http_webhook`. With `http_webhook` a user can connect any CMS today through n8n (or any HTTP endpoint) without us writing a CMS plugin; a native CMS plugin later passes the same suite — no core change.

Rules:
- Each plugin declares a Pydantic `Settings` model. The admin UI renders the form from its JSON Schema (`SecretStr` → password field), validates on save, and offers **Test connection** (`healthcheck()`).
- Credentials are entered by the user in the UI and stored encrypted in `workspace_integrations` (ADR-008). Nothing vendor-specific in env vars, YAML or Compose files. The only env secrets are platform-level: `DATABASE_URL`, `APP_ENCRYPTION_KEY`, OIDC client, Sentry DSN.
- Every module gets plugin settings through one function, `plugin_config(workspace_id, port)`, which reads and decrypts the workspace's row. A port with no configured plugin → the stage that needs it is blocked with a clear "configure X in Integrations" message (UI + `run.failed` event), never a crash.
- Plugins return contract types only (`ResearchRecord`, `KeywordData`, `PublishedContent`); vendor payloads stay inside the plugin (raw response optionally kept in `payload.raw` for debugging).
- Adding a vendor = new folder + entry point + passing the shared contract test suite. Zero edits to `engine/`.
- `engine/integrations/fakes.py` provides in-memory implementations of every port — used by unit tests and by `cli.py run-week --offline`.
- The LLM is a port like any other (`LLM`, ADR-007); the user picks the gateway, API key and model per stage. Tests inject a fake.
- Outbound HTTP to any user-supplied URL (webhooks, `http_webhook` publisher, custom endpoints) goes through one `safe_http` helper: HTTPS only in prod, DNS resolved and private/loopback/link-local/metadata ranges refused (SSRF), 10 s timeout, 1 MB response cap, no redirects to other hosts.

### 5.7 User-chosen run settings

Things that vary per business decision live in the database and are edited in the admin UI; things that vary per engineering decision live in files.

| Setting | Where set | Stored | Effect |
|---|---|---|---|
| Language + market (locale) | Admin UI → Settings; one active locale per campaign (several campaigns = several locales) | `campaigns` table | Passed to every plugin call, prompt (`write in {locale}`), keyword lookup, dedup scope, report language |
| Articles per week | Admin UI → Settings (1–N) | `campaigns` | Report recommends top N (min 3); gate 1 allows approving up to N; brief/write runs for each approved topic |
| Trusted sources | Admin UI → Sources: add / edit / disable; fields: name, URL, feed URL (optional), fetch mode (`feed` \| `site_search`), tier (1 primary/2 trade/3 general), role (`news` \| `company` \| `data` \| `management`), locale(s), topics | `sources` table, seeded once from the industry pack's `sources.yaml` (§5.8) | `feed` sources polled via RSS/sitemap; `site_search` sources queried through the `NewsSearch` port scoped to the domain; tier feeds credibility score and original-source lookup; role decides use (below) |
| Run schedule | Admin UI (weekday + time) | `campaigns` | Cron reads it; "Run now" button for ad-hoc runs |
| Integrations | Admin UI → Integrations: per port pick an installed plugin, fill its settings + credentials, Test connection; LLM model per stage (research/score, brief, write, evaluate) | `workspace_integrations` (secrets encrypted) | `plugin_config()` reads it at the start of each stage; change takes effect next stage |
| Webhooks | Admin UI → Webhooks: URL, events to send, secret (generated, shown once), enabled; delivery log with retry button | `webhook_subscriptions`, `webhook_deliveries` | Outbox events delivered by worker (ADR-008) |
| Campaign strategy | Admin UI → Settings: goal, scoring weights (default 30/20/20/15/10/5), CTA intensity limit per funnel stage | `campaigns.config` jsonb (`CampaignConfig` model; defaults in the model) | Scoring total and CTA checks read it |
| Brand profile | Admin UI → Brand: voice, capabilities, positioning, prohibited claims/phrases, CTA rules | `brand_profiles` jsonb (`BrandProfile` model) | Loaded into brief/write/evaluate prompts and deterministic checks; versioned (`version` column) so drafts record which brand version they used |
| Spend limits | Admin UI → Settings: monthly cap (USD) and per-run cap (USD); required, no default | `campaigns` | Budget guard sums `usage_ledger.cost_usd` + metered API costs; per-run cap aborts the current run at the next stage boundary (state kept, resumable); monthly cap blocks new runs until next month or a raised cap; Settings shows spend-to-date vs caps and estimated monthly cost |

Validation on save: locale must be supported by every active plugin; a new `feed` source's feed URL is fetched once and must parse; a `site_search` source needs a domain; tier and role required; spend caps > 0. All changes logged in `events` (who, what, when).

Source roles: `news` and `company` sources are discovery inputs (become research records and topic candidates); `data` and `management` sources are evidence inputs — searched at brief time for statistics and practical context to support an approved topic, never recommended as topics on their own.

Deliberate simplification: no per-user roles in MVP — any workspace member (`owner`) can edit settings; the campaign creator approves. Add roles when a workspace has more than one person.

### 5.8 Seed source list (optical retail, Acme Demo)

Seeded into `sources.yaml` at M1; users edit in the Sources screen afterwards. Tier from stated priority (very high → 1, high → 2, medium → 3). URLs and feed availability verified during M1 (contract test: each seed source returns ≥ 1 dated record in the last 30 days, or is downgraded to `site_search` / disabled with a note).

| Source | Domain (verify M1) | Locale | Role | Tier | Fetch mode (expected) |
|---|---|---|---|---|---|
| Abióptica | abioptica.com.br | pt-BR | news | 1 | feed if available, else site_search |
| Opticanet | opticanet.com.br | pt-BR | news | 1 | feed |
| Fenóptica | to confirm | pt-BR | news | 2 | site_search |
| Fique de Olho | to confirm | pt-BR | news | 3 | site_search |
| Eyewear Intelligence | eyewearintelligence.com | en-US | news | 1 | feed |
| The Vision Council | thevisioncouncil.org | en-US | data | 2 | site_search |
| MIDO | mido.com | en-US | news | 2 | site_search |
| Silmo | silmoparis.com | en-US | news | 2 | site_search |
| EssilorLuxottica | essilorluxottica.com | en-US | company | 1 | site_search (press releases) |
| ZEISS | zeiss.com | en-US | company | 2 | site_search |
| HOYA Vision Care | hoyavision.com | en-US | company | 2 | site_search |
| Kering Eyewear | keringeyewear.com | en-US | company | 2 | site_search |
| Bausch + Lomb | bausch.com | en-US | company | 3 | site_search |
| CooperVision | coopervision.com | en-US | company | 3 | site_search |
| Euromonitor International | euromonitor.com | en-US | data | 1 | site_search (public summaries only; paid reports out of scope) |
| IBGE | ibge.gov.br | pt-BR | data | 2 | site_search |
| Sebrae | sebrae.com.br | pt-BR | management | 2 | site_search |
| ICVA / Cielo | cielo.com.br | pt-BR | data | 2 | site_search |

International (en-US) sources also feed the pt-BR campaign as discovery for global trends; the writer produces in the campaign locale and cites the original-language source.

### 5.9 Product-ready foundations (multi-workspace from day one)

MVP serves one customer (Acme Demo), but the schema and boundaries assume many, because these are expensive to retrofit. Rationale and what is deferred: [MVP-Product-Analysis.md](../architecture/MVP-Product-Analysis.md).

| # | Rule | Enforced by |
|---|---|---|
| 1 | Every business table has `workspace_id NOT NULL`; every `db.py` function takes `workspace_id` as a required argument; no SQL outside `db.py` | Schema + import-linter + a unit test that every query string in `db.py` contains `workspace_id` |
| 2 | Customer-owned config (brand, campaign, sources) lives in Postgres per workspace; product-owned config (industry, format, prompts) stays in git | §5.5 layout; no `packs/brands` / `packs/campaigns` |
| 3 | One source of truth for campaign settings: `campaigns` | — |
| 4 | Plugin choice, settings and credentials are per workspace, user-entered, resolved only via `plugin_config(workspace_id, port)` | import-linter: modules can't import the integrations store directly; test: no vendor names in env/Compose files |
| 5 | Access via `users` + `memberships(workspace_id, user_id, role)`; MVP role `owner` only | `services.py` checks membership on every action |
| 6 | UI, CLI and any future API call only `engine/services.py` | import-linter contract (§9.4) |

Also included: `workspace_id` on `usage_ledger` and API cost rows (cost per customer from day one); `sources.workspace_id` nullable — `NULL` = shared pack source, set = customer-private (keeps future shared research across workspaces possible).

Not in MVP (triggers in the analysis doc): inbound REST API + new front end, job queue, Postgres row-level security, extra roles, billing. (Per-workspace secrets and outbound notifications are now in MVP via ADR-008.)

## 6. Tech stack

| Concern | Choice | Why | Rejected / later |
|---|---|---|---|
| Language | **Python 3.12+** | Best extraction/SEO/data libraries; Pydantic contracts | TypeScript (weaker extraction ecosystem) |
| Contracts & config validation | **Pydantic v2** | Typed contracts, JSON Schema export, used directly by the LLM structured-output helper and the integrations UI forms | Hand-written JSON Schema |
| LLM gateway | **OpenRouter** via its OpenAI-compatible Chat Completions API (`openai` Python SDK with `base_url=https://openrouter.ai/api/v1`), behind the `LLM` port (ADR-007). Structured outputs via **Instructor** (Pydantic response model, OpenRouter integration, `require_parameters: true`, validation retries with the error fed back, JSON-mode fallback) | One key, any model; user switches models per stage in the UI; per-request cost returned in `usage.cost` | Anthropic SDK direct (kept as a possible later plugin); agent frameworks (nothing in MVP needs them) |
| Models (defaults, user-changeable) | **Claude Opus 5.5** via OpenRouter for brief, write, evaluate. **Claude Haiku 5.5** candidate for bulk extraction/scoring — adopt only if the regression suite holds. Exact OpenRouter model ids verified in M0 from the catalog | Quality where it matters; cheap bulk work. The regression suite is the gate for any model change | — |
| Feeds | **feedparser** over `sources.yaml` RSS/Atom + sitemaps | Trusted sources are the backbone; deterministic, free | — |
| News search | **One** search API behind `search_news()`: Brave Search API (~$5/1k, monthly free credit) or Exa (~$7/1k, full text included) | Discovery beyond the source list. Swap = one function | Claude server-side `web_search` tool: viable alternative, but results land inside a model turn — harder to store/dedup/reproduce |
| Extraction | **trafilatura 2.x** (Apache-2.0) | Best open-source main-text + metadata (date, author) extractor | Firecrawl (paid; add only for JS-heavy sites) |
| Migrations | **dbmate** (plain SQL up/down, runs as the Compose `migrate` one-shot) + committed `db/schema.sql` + **squawk** lint; expand → migrate → contract for breaking changes | Repeatable, reviewable, unsafe DDL blocked before prod | Alembic (needs SQLAlchemy models we don't have) |
| Language detection | **lingua-py** | Accurate on short/mixed text, offline | fastText/langdetect |
| Webhook format | **Standard Webhooks** spec + `standardwebhooks` lib | Receivers (incl. n8n users) verify with existing libraries; secret rotation built in | Custom HMAC header |
| Dedup | Canonical URL unique index + **Postgres `pg_trgm`** title similarity | Native, zero extra infra; also covers "similar to previously published" (existing blog imported via `cli.py import-sitemap`) | pgvector + embeddings (later, if trigram misses) |
| SEO data | **DataForSEO** pay-as-you-go (Keywords Data / Google Ads volume, intent) | Real volumes per country/language; no subscription; credits don't expire | Semrush/Ahrefs API (expensive subscriptions) |
| Storage | **PostgreSQL, latest stable major: 19** (GA scheduled 2026-11-19; RC1 out since 2026-10-15). Uses native `uuidv7()` keys, `UNIQUE NULLS NOT DISTINCT`, partial unique indexes (../architecture/DATA-MODEL.md). Official `postgres` Docker image pinned to an exact minor tag; `pg_trgm` from contrib (included). If M0 starts before GA: develop on `postgres:18.x`, switch to 19 before M1 data exists (dump/restore, trivial at that size). Minor bumps via Dependabot PRs | State machine + evidence + history + dedup + outbox in one place | Separate queue/broker (not needed) |
| Orchestration | **DB state machine + cron** (see ADR-001) | Weekly batch with human gates; resumable per row | Hatchet / Temporal / n8n — see ADR-001 |
| Admin UI | **Streamlit** | Internal, 1–2 users; approval + review screens in a few hundred lines | FastAPI+React (later if external users) |
| Observability | `usage_ledger` table: workspace, prompt version, model, input/output, token usage, cost, latency, stage, entity id; **Sentry** (free tier) for exceptions; heartbeat ping (e.g. Healthchecks.io) at end of each run → alert on missed run | Enough to debug, cost-track per customer, and know when a run fails | Langfuse self-hosted (MIT core; add when prompt iteration volume grows) |
| Spec workflow | **OpenSpec** (Fission-AI, MIT, CLI via npm, Node ≥ 20.19) | Specs live in repo; every change = proposal → tasks → apply → archive; scenarios feed TDD (§9) | — |
| Integrations | **Ports (Protocol) + entry-point plugins, configured per workspace in the UI**; credentials encrypted with **Fernet** (`cryptography`) under `APP_ENCRYPTION_KEY`; outbound **webhooks** via Postgres outbox + worker (ADR-008) | Vendors swappable by the user; n8n/any tool connects via webhooks or the `http_webhook` publisher | pluggy (not needed: events go out as webhooks); embedding n8n (user runs their own) |
| Env / deps | **uv** + `pyproject.toml` + lockfile | Fast, reproducible; one tool for venv + deps + scripts | Poetry |
| Tests | **pytest** + pytest-cov + diff-cover; **Hypothesis** property tests for pure logic (scoring maths, deterministic checks, URL canonicalisation, SSRF guard); in-memory port fakes; regression evals as a separate marked suite | TDD for all code (§9) | promptfoo (add if non-devs need to edit eval cases); mutmut (mutation testing, later) |
| Static quality | **ruff** (lint/format + C901/PLR complexity), **mypy --strict**, **complexipy** (cognitive), **vulture** (dead code), **jscpd** (duplication), **import-linter** (module boundaries + cycles), **squawk** (Postgres migration safety) | Python equivalents of what fallow does for TS (ADR-006) | fallow (TS/JS only) |
| CI | **pre-commit** locally + **GitHub Actions** running the same `uv run` commands | One gate definition, two places | — |
| Deploy & runtime | **Docker + Docker Compose everywhere** — local dev, CI (offline E2E), staging, prod. One image for app + worker; Compose files below | Same artefact and topology in every environment; no host-installed Python/Postgres | Kubernetes, PaaS, serverless (not needed) |
| Hosting | **Existing VPS** running the Compose stacks behind **Caddy** (automatic HTTPS); deploy from GitHub Actions over SSH (pull image from GHCR, `docker compose up -d`; migrations run as a one-shot `migrate` service) | Uses what you already have; whole stack fits one small box | — |
| UI i18n | **gettext + Babel** (`pybabel extract/update/compile`); `locales/{pt_BR,en_US}/LC_MESSAGES/*.po` in git; CI fails on untranslated or fuzzy strings in either catalogue | Standard, no runtime dependency beyond stdlib `gettext` | Streamlit has no built-in i18n; JSON dict files (no tooling) |
| Auth | **Streamlit native OIDC** (`st.login`) with Google; access = membership in workspace | Built in, no extra service | Auth proxy (oauth2-proxy) if more apps need the same login |
| E2E tests | **pytest + Playwright for Python** (`pytest-playwright`) driving the Streamlit UI | Same language/runner as the rest; real browser = real user flow | Selenium, Cypress (JS) |
| Secrets | Integration credentials: user-entered, encrypted in Postgres. Platform secrets (`DATABASE_URL`, `APP_ENCRYPTION_KEY`, OIDC, Sentry): `.env` (gitignored) locally, `.env` mode 600 on the VPS; GitHub Actions secrets for deploy key + E2E | — | KMS-held encryption key; SOPS / Vault (later) |

### Estimated running cost (MVP, 1 article/week)

| Item | Estimate / month |
|---|---|
| Research extraction + scoring (~200 records/week, Haiku 5.5; no batch discount via OpenRouter) | < $2 – $30 (upper end only if Opus is used for bulk) |
| Brief + write + evaluate + up to 2 revisions (Opus 5.5) | ~$2–8 |
| OpenRouter platform fee (~5.5 % on credit purchases) | < $3 |
| Search API | $0–10 |
| DataForSEO | < $5 |
| VPS (existing) + offsite backup storage | $0–5 incremental |
| Live E2E runs (nightly, capped) | ~$5–15 |
| **Total** | **~$10–75** |

Figures are order-of-magnitude, from list prices, at 1 article/week. OpenRouter passes model list prices through and adds its fee on credit top-ups; it has no confirmed equivalent of Anthropic's 50 % Batch discount, so bulk scoring runs at full price — this is why Haiku for bulk matters more now. Prompt caching does work through OpenRouter for Claude (`cache_control`, reads at 0.1×). Actual spend is bounded by the user-set caps (§5.7). Brief/write/evaluate cost scales linearly with the user-chosen articles/week (~$0.5–2 per article); research cost is fixed per run. The `usage_ledger` table produces the real number from week 1, and the Settings screen shows estimated monthly cost for the chosen volume.

### Hosting on the existing VPS

Minimum box: 2 vCPU, 4 GB RAM, 40 GB disk (Streamlit + worker + Postgres + Playwright not on prod). Layout:

Compose files (repo root):

| File | Services / purpose |
|---|---|
| `compose.yaml` | `db` (postgres, healthcheck, named volume), `migrate` (one-shot, `depends_on: db healthy`), `app` (Streamlit), `worker` (supercronic + pipeline + webhook delivery) — base for every environment |
| `compose.override.yaml` | Local dev only (auto-loaded): bind-mount source, hot reload, exposed DB port |
| `compose.e2e.yaml` | CI offline E2E: fake integrations, `webhook-receiver` test container that records deliveries, fake OIDC identity |
| profile `rsshub` (in `compose.yaml`) | Optional RSSHub container (no Chromium) generating feeds for sources without RSS; enabled per environment if the M1 trial passes |
| `compose.prod.yaml` | `caddy`, `backup` (nightly `pg_dump` → offsite), pinned image tags, restart `unless-stopped`; used for both prod and staging with different `.env` and project name |

Rules: every service has a healthcheck; images pinned by exact tag; no service publishes the DB port outside dev; `docker compose config` validated in CI.

- Two Compose projects on the same VPS: `prod` and `staging`, each with its own Postgres volume, `.env`, API keys and subdomain (e.g. `content.example.com`, `staging.content.example.com`). Caddy routes both.
- Worker schedule: container cron (`supercronic`) reading `campaigns.schedule`; no host crontab edits.
- Backups: nightly `pg_dump` of prod to offsite object storage, 14-day retention; restore tested once in M4.
- You own OS updates: unattended security upgrades + Docker restart policy `unless-stopped`.
- Secrets: `.env` files on the VPS (mode 600, never in git); GitHub Actions holds only the SSH deploy key and registry token.

Revisit if the VPS can't hold both stacks or runs need > 1 worker — then split staging off or move Postgres to a managed DB.

## 7. Architecture decisions

Every decision has its own ADR in [../adr/](../adr/README.md), which is the single source of truth. This section is only an index.

| ADR | Decision |
|---|---|
| [001](../adr/ADR-001-orchestration.md) | DB state machine + cron; DBOS as the upgrade path (M0 spike) |
| [002](../adr/ADR-002-llm-call-pattern.md) | One structured LLM call per step, no agents |
| [003](../adr/ADR-003-research-sources.md) | Curated sources first, Brave search; RSSHub/GDELT/Docling trials |
| [004](../adr/ADR-004-scoring.md) | LLM scores each criterion, code computes the total |
| [005](../adr/ADR-005-ports-and-plugins.md) | Protocol ports + entry-point plugins |
| [006](../adr/ADR-006-python-and-quality-gates.md) | Python + fallow-equivalent gates |
| [007](../adr/ADR-007-llm-gateway-openrouter.md) | OpenRouter + Instructor |
| [008](../adr/ADR-008-user-configured-integrations.md) | All integrations user-configured; encrypted secrets; `safe_http` |
| [009](../adr/ADR-009-multi-workspace-foundations.md) | Multi-workspace foundations |
| [010](../adr/ADR-010-data-model-principles.md) | Data model principles |
| [011](../adr/ADR-011-postgres-and-migrations.md) | PostgreSQL 19 + dbmate + squawk |
| [012](../adr/ADR-012-docker-compose-and-hosting.md) | Compose everywhere; VPS + Caddy |
| [013](../adr/ADR-013-auth-and-approver.md) | Google OIDC + memberships; approver = campaign creator |
| [014](../adr/ADR-014-admin-ui-and-services.md) | Streamlit over services; pt-BR/en-US UI |
| [015](../adr/ADR-015-locale-runtime-setting.md) | Locale as runtime setting |
| [016](../adr/ADR-016-spend-control.md) | User-set spend caps |
| [017](../adr/ADR-017-webhooks.md) | Standard Webhooks + outbox |
| [018](../adr/ADR-018-testing-strategy.md) | TDD at seams; offline + live E2E |
| [019](../adr/ADR-019-git-ci-pr-workflow.md) | GitHub, Actions, small PRs |
| [020](../adr/ADR-020-openspec-workflow.md) | OpenSpec workflow |
| [021](../adr/ADR-021-publisher-port.md) | CMS-neutral Publisher |
| [022](../adr/ADR-022-source-model.md) | Source roles, tiers, fetch modes |
| [023](../adr/ADR-023-observability.md) | Usage ledger, Sentry, heartbeat |
| [024](../adr/ADR-024-retention-and-privacy.md) | Retention, LGPD, copyright |
| [025](../adr/ADR-025-supporting-libraries.md) | lingua-py, Hypothesis |

## 8. Data model

Full schema (DDL, constraints, indexes, state machines, retention, operations): **[DATA-MODEL.md](../architecture/DATA-MODEL.md)**.

| Group | Tables |
|---|---|
| Tenancy & access | `workspaces`, `users`, `memberships` |
| Customer config | `brand_profiles` (versioned, one current), `campaigns` (locale, volume, schedule, spend caps, strategy), `sources` (shared pack + private), `workspace_integrations` (encrypted secrets), `webhook_subscriptions` |
| Pipeline | `runs` → `documents` (fetched evidence) → `research_records` → `opportunities` → `briefs` → `drafts` → `draft_claims` (FK to evidence) · `quality_reports` · `publications` |
| Shared market data | `keyword_cache` (no customer data) |
| Events & usage | `events` (append-only audit + webhook outbox), `webhook_deliveries`, `usage_ledger` (all LLM + API costs), `llm_payloads` (purged after 90 days) |

Guarantees enforced by the database itself:
- Tenant isolation through composite FKs `(workspace_id, id)`.
- One active run per campaign.
- A `fact` claim can't be stored without a source document.
- Idempotent publish per (draft, destination) and webhook delivery per (subscription, event).
- Exact money (`numeric`).
- Guarded status transitions.

## 9. Engineering process & quality gates

### 9.1 Spec-driven: OpenSpec

- `openspec init` in M0. This PRD seeds one spec per capability: `research`, `scoring`, `briefing`, `writing`, `quality`, `integrations`, `config-packs`, `admin-ui`.
- Every requirement is written as **WHEN / THEN scenarios**. Each scenario becomes ≥ 1 test (§9.2) — the spec is the test plan.
- Every change (feature, new plugin, prompt change, threshold change) goes through `propose → apply → archive`. `tasks.md` lists tasks as vertical slices, each starting with "write failing test".
- PR rule: a PR touching `engine/`, `plugins/`, `prompts/` or `packs/` references an OpenSpec change id. `openspec validate` runs in CI.
- Milestones in §10 map to OpenSpec changes (one per milestone deliverable).

### 9.2 Test-driven implementation (all code)

Cycle per task: **red → green → refactor**, one vertical slice at a time (one test, minimal code, repeat — no bulk test-writing up front).

| Layer | Seam (public interface tested) | Test doubles | Runs |
|---|---|---|---|
| Contracts & config | `contracts.py` models, `config.load()` | none | every commit |
| Core logic (dedup, scoring math, checks, state transitions) | module public functions | port fakes, test Postgres | every commit |
| LLM steps (score/brief/write/evaluate) | step function: contract in → contract out | `FakeLLMClient` returning recorded structured outputs | every commit |
| Plugins | port Protocol | recorded HTTP fixtures (pytest-recording/vcrpy); shared **contract suite** parametrized over all registered plugins + the fake (incl. `openrouter` against the `LLM` port and `http_webhook` against `Publisher`) | every commit (recorded), nightly (live, optional) |
| Integrations & webhooks | `services` (save/test integration, subscriptions) + `events` (outbox → delivery) | real Postgres in Compose; local test HTTP receiver | every commit — asserts: secrets never returned or logged, encrypted at rest; event written in same transaction as state change; HMAC verifies; retries/backoff; private-IP and non-HTTPS URLs refused |
| Pipeline | `pipeline.run_week()` | all fakes, offline | every commit |
| Prompt quality | regression cases (§5.4) | **real** LLM via OpenRouter | on change to `prompts/` or `packs/` + nightly; costs money, marked `@pytest.mark.eval` |
| User flow, offline | Streamlit UI via Playwright | all fakes, test Postgres | every PR |
| User flow, **live** | Streamlit UI via Playwright on staging | **none** — real Claude, search, extraction, DataForSEO, real feeds | nightly + before every release (§9.6) |

Rules:
- Bug fix = failing repro test first.
- Prompt change = regression case that captures the desired behaviour first (red), then the prompt edit (green).
- No mocking of internals; tests hit seams only. Expected values are literals or fixtures, never recomputed with the code under test.
- Seams above are proposed; confirm before M0 tests are written.

### 9.3 Coverage gates

| Gate | Tool | Threshold |
|---|---|---|
| Changed lines covered | diff-cover over pytest-cov | **100 %** of changed lines in `engine/` + `plugins/` (evidence of TDD per PR) |
| Overall branch coverage | pytest-cov `--cov-branch --cov-fail-under` | **≥ 90 %** (`app.py` UI excluded) |

### 9.4 Complexity thresholds & analysis (fallow-equivalent)

| Check (fallow equivalent) | Tool | Threshold | Mode |
|---|---|---|---|
| Cyclomatic complexity / function | ruff `C901` (`max-complexity`) | **≤ 10** | block |
| Cognitive complexity / function | complexipy | **≤ 15** (tool default); `--diff main --ratchet` so existing code can only improve | block |
| Branches / statements / args | ruff `PLR0912` / `PLR0915` / `PLR0913` | ≤ 12 / ≤ 50 / ≤ 5 | block |
| Dead code (unused functions, classes, vars) | vulture `--min-confidence 80` + ruff `F401/F841` | 0 findings (whitelist file for entry points/plugins) | block |
| UI translations complete | `pybabel compile --statistics` + check script on `pt_BR` and `en_US` | 0 untranslated / fuzzy | block |
| Migration safety | squawk on `db/migrations/*.sql` (pre-commit + CI) | 0 violations (documented `-- squawk-ignore` only with reason) | block |
| Duplication | jscpd (Python + YAML + Markdown prompts) | ≤ 3 % total, min 50 tokens; 0 new clones | block |
| Circular imports & module boundaries | import-linter contracts: `engine.*` modules may import `engine.contracts` + `engine.integrations.ports` only; `engine` never imports `plugins`; `app` and `engine.cli` import only `engine.services` + `engine.contracts`; only `engine.db` runs SQL; no cycles | 0 violations | block |
| Tenant scoping | unit test: every query in `engine/db.py` filters by `workspace_id`; services test: a user without membership gets `Forbidden` for every action | 0 failures | block |
| Types | mypy `--strict` on `engine/` + `plugins/` | 0 errors | block |
| CRAP-style hotspot (complexity × low coverage) | complexipy JSON + coverage JSON joined by a ~30-line script | report top 10 | report only |
| Trend | complexipy + jscpd + coverage JSON uploaded as CI artifacts per run | — | report only |

All gates: one command (`uv run poe check` or a `scripts/check.sh`) used by pre-commit and CI. Thresholds live in `pyproject.toml`; changing one requires an OpenSpec change with rationale.

### 9.5 Definition of done (per OpenSpec task)

- [ ] Scenario(s) covered by tests written first
- [ ] All §9.3 / §9.4 gates green
- [ ] Regression evals green if `prompts/` or `packs/` changed
- [ ] Offline E2E green; user-facing change adds/updates an E2E journey step
- [ ] OpenSpec change archived; specs updated

### 9.6 Live end-to-end tests (user flow guarantee)

Purpose: prove the **whole user flow works against the real world** — real APIs, real feeds, real browser — not just that units pass on fakes. Each journey is written test-first from the `admin-ui` + pipeline OpenSpec scenarios.

**Journeys (Playwright, `@pytest.mark.e2e_live`):**

| # | Journey | Asserts (structure and contracts, never exact LLM text) |
|---|---|---|
| J0 | Google sign-in with a test account that is a workspace member; a non-member account refused; member of workspace B sees none of workspace A's data | Session shows email; refused account sees no data; no cross-workspace rows |
| J1 | Settings: pick locale (`pt-BR`, then `en-US`), set articles/week = 1, set spend caps, save; switch UI language pt-BR ↔ en-US | Saved + audited; labels change language and the choice persists after reload; unsupported locale shows plugin error; missing caps rejected |
| J2 | Sources: add a real trusted source with feed URL; disable another | Feed validated on save; disabled source not polled in J3 |
| J3 | "Run now" → research + scoring complete | Run finishes < 30 min; ≥ 1 record per enabled feed with parsed date; report shows ≤ N recommendations with URL, date, score, funnel, confidence |
| J4 | Gate 1: approve top topic | Opportunity `approved`; brief created and validates against `EditorialBrief`; keyword data present or flagged estimate |
| J5 | Draft produced and evaluated | Draft in chosen locale (lingua-py check); every claim has a source in evidence; `QualityReport` has a verdict; revision loop ≤ 2 |
| J6 | Gate 2: approve → publish via active `Publisher` plugin | `publications` row with destination + external_id; exported file has title/meta/slug/body |
| J7 | Reject path: reject a topic and a draft | States correct; rejected topic not re-recommended next run |
| J8 | Integrations: configure OpenRouter key + model per stage, a search plugin and the `http_webhook` publisher; Test connection; add a webhook subscription | Secret masked after save; Test connection green; `topics.awaiting_approval`, `draft.awaiting_approval` and `article.published` arrive at the test receiver with valid signatures; a private-IP URL is refused |

**Environment & guardrails:**
- Runs on the **staging** Compose project on the VPS (same Docker image as prod, separate Postgres, separate API keys with provider-side spend caps). Playwright runs in the GitHub Actions runner, not on the VPS.
- Google sign-in in E2E: a dedicated test Google account with a staging workspace membership; its session state is created once and stored as an encrypted GitHub secret (Playwright `storage_state`), refreshed manually when expired. Offline E2E bypasses OIDC with a fake identity (test-only setting, refused when `ENV=prod`).
- Triggered by GitHub Actions: nightly cron, manual `workflow_dispatch`, and required before tagging a release. Not on every PR (cost + flakiness); PRs run the offline E2E with fakes.
- Budget guard: same per-run cap mechanism as production (§5.7), set in the staging campaign's settings by J1; a pytest fixture also fails the suite if total cost exceeds it.
- Publisher in staging always uses `mode: draft` (or `markdown_export`) — live E2E never publishes publicly.
- Failure artefacts: Playwright trace + screenshots + run_id + `usage_ledger` rows uploaded to the Actions run.
- Flake policy: 1 automatic retry per journey; a journey that needs the retry twice in 7 days gets an OpenSpec fix change.
- Post-deploy smoke: J1 (read-only) + health check run after every production deploy.
- Webhook target in staging: a `webhook-receiver` container on the staging Compose project (records requests, exposed only to the E2E runner).

## 10. Milestones (1 developer, ~11 weeks + 4-week pilot)

The summary is below. The actionable plan, with 40 PRs, a verification step for each step, universal PR checks and milestone exit gates, is in **[../plans/](../plans/README.md)**.

| M | Weeks | Deliverable | Verify |
|---|---|---|---|
| M0 | 1–2 | Repo (uv), OpenSpec init + capability specs from this PRD, all §9 gates in pre-commit + CI, Dockerfile + all four Compose files (dev runs only via `docker compose up`), Postgres 19 (or 18 until GA), migrations incl. `workspace_integrations` + Fernet encryption + `cli.py integrations set`, `workspaces`/`users`/`memberships`/`brand_profiles` and `workspace_id` everywhere, `contracts.py` (incl. `BrandProfile`, `CampaignConfig`), `services.py` skeleton with membership check, pack + `plugin_config()` loader, plugin registry + fakes, `LLM` port + `openrouter` plugin + `llm.py` on Instructor with usage logging (ADR-007 spike: model ids, strict schema, caching, `usage.cost`), dbmate + squawk wired, `safe_http`, Sentry + heartbeat; **DBOS spike (½ day) → ADR-001 decision** | CI green on empty skeleton with gates enforced (incl. tenant-scoping test and squawk); DBOS keep/drop recorded in ADR-001; `cli.py plugins list` shows fakes; deliberately complex/duplicated/untested sample code fails CI; a second seeded workspace sees none of the first one's rows |
| M1 | 3–4 | Research engine: `rss`, `brave` (only search plugin, S4), `trafilatura` plugins + date check + dedup + `site_search` fetch mode; `campaigns` + `sources` tables; seed sources (§5.8) verified; Google sign-in + membership; Settings (incl. spend caps, campaign strategy), Brand, Sources and **Integrations** screens (forms from plugin `Settings`, Test connection, masked secrets; UI in **pt-BR and en-US**: all strings through `gettext`, catalogues managed with Babel, language switch per user); budget guard. **Trials (1 day each):** RSSHub for the `site_search` seed sources; `gdelt` NewsSearch plugin | Contract suite passes for each plugin; each seed source returns ≥ 1 dated record or is reclassified; run on last 14 days → records stored for pt-BR and en-US; manual check of 20 records: dates correct, duplicates merged; offline E2E J0–J2 green; trial results recorded (RSSHub kept if ≥ 5 sources get dated feeds; GDELT kept if ≥ 20 % unique relevant pt-BR stories) |
| M2 | 5–6 | Scoring + `events` + webhook delivery (Standard Webhooks) + Webhooks screen + weekly report (top N from articles/week) + Streamlit approve/reject; `cli.py import-sitemap` for existing blog content (S2); **staging on the VPS (S1)**: staging Compose project + Caddy + GitHub Actions SSH deploy; live E2E J0–J4 nightly | Report matches §7 spec; regression cases 1–5 (priority bands) pass; offline E2E J3–J4 green; `topics.awaiting_approval` delivered and verifiable with the `standardwebhooks` lib; existing Acme articles imported and a topic matching one is flagged as already covered; live E2E J0–J4 green on staging 3 nights in a row |
| M3 | 7 | DataForSEO keywords (via `keyword_cache`) + brief generation; **Docling trial** for PDF evidence sources (optional image/profile) | Brief validates against `EditorialBrief`; volumes present or flagged estimate; Docling kept only if PDF statistics extract correctly from ≥ 3 seed data sources |
| M4 | 8–10 | Writer + deterministic checks + LLM evaluator + revision loop + `Publisher` port/contract suite + `markdown_export` + `http_webhook`; prod Compose project + nightly offsite backups + one restore test; live E2E extended to J0–J8 | Regression cases 6–7 pass; 3 end-to-end articles produced; live E2E green on staging 3 nights in a row |
| M5 | 11 → +4 wks | Production deploy; pilot: 4 weekly runs with real editor | Section 3 success criteria; post-deploy smoke green |

## 11. Risks

| Risk | Mitigation |
|---|---|
| Hallucinated facts/stats | Claims must reference stored sources (code check); evaluator verifies against evidence text; human gate 2 |
| Thin general news volume in chosen language/market | User adds sources in the Sources screen (incl. international trade press); report allows evergreen/MOFU fallback topics |
| User picks a locale with weak prompt/eval coverage | Regression cases are parametrised by locale; a locale is marked "supported" only once its cases pass |
| User-chosen volume drives cost up | User-set monthly + per-run caps enforced in code; Settings shows estimate and spend-to-date |
| Many seed sources have no RSS | `site_search` fetch mode via `NewsSearch` port; M1 contract check per seed source |
| Single VPS = single point of failure | Nightly offsite `pg_dump`; pipeline is resumable per row; weekly cadence tolerates a day of downtime |
| Approver is one person | Pending items wait (state persisted); add delegation when a second reviewer exists |
| No keyword data for niche long-tail terms | Flag `is_estimate`; use related-keyword endpoints; never fabricate numbers |
| Evaluator rubber-stamps writer | Separate prompt, no shared reasoning, deterministic checks first, regression cases include known-bad drafts |
| Sites block scraping / paywalls | Respect robots.txt; keep summary + link only; mark `extraction_failed`; prefer feeds with full text |
| Copyright | Write original analysis; quote ≤ 1 short sentence per source with attribution; store extracts for internal evidence only |
| OpenRouter strict schema not honoured by an endpoint / model id changes | `require_parameters: true`; Pydantic re-validation + 1 retry; M0 spike; `anthropic_direct` plugin as fallback |
| No batch discount via OpenRouter | Haiku for bulk scoring (regression-gated); prompt caching on the stable prefix; spend caps |
| User-entered URLs abused for SSRF | `safe_http` only; private/metadata ranges refused; tests in CI + E2E J8 |
| Encryption key lost or leaked | `APP_ENCRYPTION_KEY` backed up separately from DB backups; `rotate-key` command; leak → rotate + users re-enter vendor keys |
| Webhook receiver down (e.g. user's n8n) | Outbox + 24 h retry + dead-letter + manual re-send; pipeline never blocks on delivery |
| Postgres 19 GA slips or early-minor bugs | Develop on 18 until GA; pinned tags; prod upgrade only after 19.1 if GA is late in M4 |
| Prompt drift breaks quality silently | Prompts versioned in git, `prompt_version` logged per call, regression suite in CI |
| Strict gates slow early velocity | Gates set in M0 on an empty repo (nothing to retrofit); complexipy ratchet only blocks regressions; thresholds changeable via OpenSpec change |
| LLM-step tests pass on fakes but prompts regress | Separate live regression suite triggered on any `prompts/`/`packs/` change |

## 12. Decisions log & open questions

### Resolved (2026-11-03)

| Topic | Decision |
|---|---|
| Language / market | User choice at runtime (Settings screen, per campaign) — §5.7 |
| Articles per week | User choice at runtime — §5.7 |
| Trusted sources | User-managed list (add/edit/disable), seeded from pack — §5.7 |
| CMS | Not decided; generic `Publisher` port + contracts in MVP, CMS plugin later — §5.6 |
| Hosting | Existing VPS, Docker Compose prod + staging, Caddy, GitHub Actions SSH deploy — §6 |
| Git / CI | Git + GitHub + GitHub Actions |
| Language of the codebase | Python (ADR-006) |
| LLM API | OpenRouter behind the `LLM` port; model per stage chosen by user (ADR-007) |
| Database | PostgreSQL latest stable major (19; 18 until GA on 2026-11-19), official Docker image, pinned tag |
| Deploy | Docker + Compose in every environment (dev, CI, staging, prod) |
| External integrations | All user-configured in the UI per workspace: plugins, credentials (encrypted), webhooks; n8n via webhooks + `http_webhook` publisher (ADR-008) |
| User-flow validation | Live E2E suite on staging — §9.6 |
| Starting locales | `pt-BR` and `en-US` built and marked supported first |
| Seed sources | User's list, 18 sources — §5.8 |
| Test seams | §9.2 approved as written |
| Login | Google sign-in (Streamlit OIDC) + workspace membership — §4 |
| Data model | Robust schema in [DATA-MODEL.md](../architecture/DATA-MODEL.md): composite tenant FKs, `runs`, `documents` vs `research_records`, `draft_claims` provenance, single `events` log, `usage_ledger`, retention/LGPD |
| Tech radar (Adopt) | Instructor, Standard Webhooks, lingua-py, dbmate + squawk, Hypothesis, PG 18/19 native features — [TECH-RADAR.md](../architecture/TECH-RADAR.md) |
| Trials | DBOS spike M0 (may replace ADR-001 runner), RSSHub + GDELT in M1, Docling in M3 — each with keep/drop criteria in §10 |
| Scope | S1 staging + live E2E from M2; S2 import existing blog via sitemap; S4 Brave only in M1 |
| Admin UI language | pt-BR and en-US; per-user choice (`users.ui_locale`), default from browser `Accept-Language`, fallback pt-BR. Independent of the campaign content locale |
| Product path | Multi-workspace schema and service boundary from M0; brand/campaign as data; product features deferred with triggers — §5.9, [MVP-Product-Analysis.md](../architecture/MVP-Product-Analysis.md) |
| Approver | Campaign creator approves gates 1 and 2 — §4 |
| Spend limits | User-configured monthly + per-run caps, required, enforced in code — §5.7 |

### Still open (none blocking M0–M3)

1. **Fenóptica and Fique de Olho URLs** — resolved during M1 source verification; ask only if not findable.
2. **Google OAuth client** — someone with access to the company Google Cloud project creates the OAuth client (redirect URIs for prod + staging) and the test account for E2E. Needed by M1.
3. **VPS specs + domain** — confirm the VPS meets 2 vCPU / 4 GB / 40 GB and which (sub)domains to use. Needed by M4.
4. **CMS choice** — deferred; needed only when building the first CMS plugin (Phase 3).

## 13. Research sources

- Durable workflow comparisons: [Diagrid — Temporal alternatives](https://www.diagrid.io/infrastructure/10-best-temporal-alternatives-2026), [Noqta — durable execution for AI agents](https://www.noqta.tn/blog/durable-execution-ai-agents-inngest-trigger-temporal-2026), [PkgPulse — Hatchet vs Trigger.dev vs Inngest](https://www.pkgpulse.com/guides/hatchet-vs-trigger-dev-v3-vs-inngest-durable-workflows-2026), [Automation Atlas — Inngest vs Temporal](https://automationatlas.io/answers/inngest-vs-temporal-2026/), [Railway — Hatchet template](https://railway.com/deploy/hatchet.md)
- Search APIs: [Firecrawl — best search tools for agents](https://www.firecrawl.dev/blog/best-search-tools-for-agents.md), [Beri — Tavily vs Exa vs Brave cost](https://www.beri.net/article/tavily-vs-exa-vs-brave-vs-perplexity-search-api-agent-cost-2026), [Parallel — Exa alternatives](https://parallel.ai/articles/exa-alternatives)
- SEO data: [DataForSEO Keyword Planner API](https://dataforseo.com/keyword-planner-api), [DataForSEO clickstream pricing](https://dataforseo.com/pricing/keywords-data/clickstream-api-pricing), [StoryLab — best SEO data APIs 2026](https://storylab.ai/best-seo-data-apis/)
- Extraction: [trafilatura (ecosyste.ms)](https://summary.ecosyste.ms/projects/378874)
- OpenRouter: [structured outputs](https://openrouter.ai/docs/features/structured-outputs), [prompt caching](https://openrouter.ai/docs/features/prompt-caching), [API overview / usage cost](https://openrouter.ai/docs/api/reference/overview), [pricing guide (fees, 3rd-party)](https://www.truefoundry.com/blog/openrouter-pricing)
- PostgreSQL 19: [RC1 announcement (GA 2026-11-19)](https://www.postgresql.org/about/news/postgresql-19-rc-1-released-3409/), [what's in 19](https://versionlog.com/blog/postgresql-19-whats-coming-september-2026/)
- Evals/observability: [Promptfoo guide](https://dev.to/preecha/how-to-test-llm-applications-the-complete-guide-to-promptfoo-2026-jl2), [Blck Alpaca — eval frameworks](https://blckalpaca.at/en/knowledge-base/ai-agents/prompt-engineering-for-agents/prompt-evaluation-frameworks.md), [Langfuse self-host guide](https://dev.to/jangwook_kim_e31e7291ad98/langfuse-self-host-llm-observability-for-free-2026-guide-3blp)
- Search Console (Phase 4): [GSC API usage limits](https://developers.google.com/webmaster-tools/limits)
- fallow: [LogRocket — clean AI code with fallow](https://blog.logrocket.com/clean-ai-code-fallow/), [fallow-core on docs.rs](https://docs.rs/crate/fallow-core/latest), [fallow skill overview](https://skillselion.com/skills/fallow-rs/fallow-skills/fallow)
- OpenSpec: [Agentic AI docs — OpenSpec](https://agentic-ai.readthedocs.io/en/latest/Standards/openspec/), [OpenSpec SDD walkthrough](https://recca0120.github.io/en/2026/03/08/openspec-sdd), [SourcePulse — Fission-AI/OpenSpec](https://www.sourcepulse.org/projects/15737171)
- Python complexity gates: [complexipy](https://github.com/rohaquinlop/complexipy), [complexipy docs](https://rohaquinlop.github.io/complexipy/), [xenon](https://pypi.python.org/pypi/xenon), [ADR example: code-quality gates](https://jjviscomi.github.io/bqemulator/main/adr/0035-code-quality-gates/)

Prices quoted are list prices seen in Nov 2026 sources; confirm on vendor pages before committing.
