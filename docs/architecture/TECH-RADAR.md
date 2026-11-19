# Scope Re-analysis & Tech Radar

**Date:** 2026-11-03 · **Inputs:** [MVP-PRD.md](../prd/MVP-PRD.md), [DATA-MODEL.md](DATA-MODEL.md), [MVP-Product-Analysis.md](MVP-Product-Analysis.md) · **Method:** each layer checked against current open-source projects (late 2026). Rule: adopt only what removes code we would otherwise write, or reduces risk, without adding a service to run.

## 1. Scope re-analysis

### 1.1 Still right
- A pipeline controlled by code, with one structured LLM call per step and no agents.
- Two human approval gates.
- The `Publisher` port, with `http_webhook` covering any CMS through n8n.
- Plugins behind ports, configured by the user.
- A schema ready for several customers from day one.
- Docker Compose on the existing VPS.
- The quality gates (§9) and the live end-to-end tests (E2E).

### 1.2 Gaps found (now fixed in DATA-MODEL.md)

| Gap | Risk | Fix |
|---|---|---|
| No `runs` table, although `run_id` is referenced everywhere | Can't enforce "one active run per campaign", nowhere to store per-run cost, no code/pack version per run | `runs` table + `runs_one_active` partial unique index + `code_version`/`pack_version` |
| Fetched page and per-run record were one thing | The same article refetched every week; evidence tied to a run instead of the source | `documents` (the source, unique per workspace) separate from `research_records` (the source as seen by a run) |
| Claim provenance only inside draft JSON | "Every fact has a source" checked only in code | `draft_claims` table with `CHECK (kind <> 'fact' OR document_id IS NOT NULL)` and an FK to `documents` |
| Three overlapping logs (`settings_audit`, `events_outbox`, `llm_calls`) | Duplicate writes; audit and webhooks drift apart | One append-only `events` table (audit + outbox) + `webhook_deliveries`; `usage_ledger` for all costs (LLM **and** paid APIs) + purgeable `llm_payloads` |
| Cross-tenant references only prevented in code | One bug and a draft references another customer's document | Composite FKs `(workspace_id, id)` on every child table |
| Keyword data re-bought every run | DataForSEO cost grows with every workspace | Global `keyword_cache` (market data, not customer data), 30-day freshness |
| No retention or privacy policy | LGPD (users' personal data); copyright exposure from storing article text indefinitely | Retention table in DATA-MODEL §5; nightly purge |
| Contract changes would break stored JSON | Old briefs and drafts unreadable after a model change | `schema_version` on every payload + upcasters |

### 1.3 Scope changes (S1–S4 applied to PRD on 2026-11-03; S3 = pt-BR + en-US)

| # | Change | Why |
|---|---|---|
| S1 | **Deploy staging on the VPS in M2, not M4**, and run live E2E J0–J4 from then on | M4 currently holds the writer, evaluator, publisher, hosting and live E2E. That is too much risk in one milestone. Deploying early finds VPS, OIDC and Caddy problems while the app is small |
| S2 | **Import the existing blog's content** (`cli.py import-sitemap <url>` → `publications` with `mode='live'`) | The "similar to previously published" check needs Acme's existing articles; otherwise the engine re-proposes topics already covered |
| S3 | **Admin UI language** — pt-BR, en-US or both? | The approver is probably Brazilian. Streamlit has no built-in i18n, so if both are needed, add a small `gettext` catalogue from M1. *Open question* |
| S4 | **One news-search plugin in M1** (Brave), with GDELT as a free second option (radar) | `brave` *or* `exa` was ambiguous; building both in M1 doubles contract-test work |

## 2. Tech radar

> Adopt list and Trials applied to the PRD on 2026-11-03 (§6, §7 ADR-001/007, §9.4, §10).

**Adopt** = put in the MVP now · **Trial** = time-boxed test in the named milestone, kept only if it beats the current plan · **Assess** = watch, with a trigger · **Hold** = deliberately not used.

### Adopt

| Tech | Replaces / adds | Why | Cost |
|---|---|---|---|
| **PostgreSQL 18/19 native features**: `uuidv7()`, `UNIQUE NULLS NOT DISTINCT`, partial unique indexes | uuid4 / bigserial; app-side uniqueness checks | Time-ordered keys; integrity rules enforced by the database | 0, already the chosen DB |
| **[Standard Webhooks](https://github.com/standard-webhooks/standard-webhooks)** spec + `standardwebhooks` PyPI lib | Our own HMAC header format | Open spec (`webhook-id`, `webhook-timestamp`, `webhook-signature`), secret rotation, verifier libraries in many languages; receivers including n8n users can verify with copy-paste code | 1 small dependency |
| **[Instructor](https://python.useinstructor.com/integrations/openrouter/)** for structured LLM calls | Our own json_schema → validate → retry loop in `llm.py` | Has a documented OpenRouter integration (incl. `require_parameters`); Pydantic response models; validation retries with the error fed back; JSON mode fallback when strict schemas aren't honoured (the ADR-007 risk) | 1 dependency; `llm.py` shrinks |
| **[lingua-py](https://github.com/pemistahl/lingua-py)** | Unspecified "language detector" in checks and E2E J5 | Accurate on short and mixed text; offline; restrict to pt/en for speed | 1 dependency |
| **dbmate** (migrations runner, plain SQL, Docker image) + **[squawk](https://github.com/sbdchd/squawk)** (Postgres migration linter, v2.x, pre-commit hook) | "plain SQL files" with no runner or safety check | Repeatable migrations as a Compose one-shot; squawk blocks locking/unsafe DDL before it reaches prod | 2 binaries, no Python deps |
| **Hypothesis** (property-based tests) | Example-only tests for scoring maths, deterministic checks, URL canonicalisation, SSRF guard | These are pure functions with many edge cases; property tests find what examples miss | Dev dependency |
| **[python-frontmatter](https://github.com/eyeseast/python-frontmatter)** (`M5-07`) | Custom regex/split YAML frontmatter parsing in `.md` draft imports | Cleanly separates YAML metadata (`title`, `slug`, `meta_description`, `format_slug`, `sources`) from Markdown body | 1 tiny pure-Python dependency |
| **[pymupdf4llm](https://github.com/pymupdf/RAG)** (`M5-07`) | Heavy ML PDF extractors (`docling-serve-cpu` 7.05 GiB) for `.pdf` draft/source imports | Converts PDFs (including multi-column layouts and tables) directly into GitHub-Flavored Markdown on CPU in milliseconds (~25 MB wheel, 0 GPU/PyTorch deps, fits 4 GB VPS) | 1 lightweight wheel |
| **Pocket Stack & Zero-Key Adapters (`M7`)** (`engine/bootstrap.py`, `engine/migrations.py`, `openai_compat.py`, `gnews_rss.py`, `autocomplete_keywords.py`) | Separate `dbmate`/`worker` containers + mandatory paid API keys for local onboarding | Enables zero-`.env` Pocket single-container runtime with PBKDF2 local auth, Ollama / OpenAI-compatible LLM, Google News RSS, public Autocomplete (`is_estimate=True`), `CapabilityPlugin` switchboard, and `--stateless` CLI/MCP | Built-in (`0` extra services; optional `--profile local-llm` Ollama) |

### Trial

| Tech | Where | Trial / keep criteria | Status / Decision |
|---|---|---|---|
| **[DBOS Transact](https://github.com/DBOS-project/dbos-transact-py)** | M0 spike | Less code than ADR-001 runner with same tests | **Decided (Dropped for MVP).** Confirmed ADR-001 Postgres state machine; DBOS kept as future upgrade path. |
| **[RSSHub](https://github.com/DIYgod/RSSHub)** self-hosted (Compose service) | M1, for the 13 `site_search` seed sources | Keep if dated feeds for ≥ 5 of them: fewer paid search calls, verified dates | **Decided (Dropped).** 0 of 13 niche optical association/trade domains have native radar routes in standard image without Chromium (0 < 5). Profile removed from `compose.yaml`. |
| **[GDELT DOC 2.0 API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)** as a `NewsSearch` plugin | M1 contract suite | Free, no key, filters by source country/language. Keep if adds ≥ 20 % unique relevant pt-BR stories vs Brave | **Decided (Dropped).** 0% unique relevant stories in 14-day optical retail test (0% < 20%). Severe global rate limits (429) and high wire noise. Brave remains sole search plugin in M1. |
| **[Docling](https://github.com/docling-project/docling)** as an `Extractor` plugin for PDFs | M3 (evidence sources: IBGE, The Vision Council, public Euromonitor summaries) | Many statistics come as PDF tables, which trafilatura can't read. Heavy (ML models): run in a separate optional image/profile; keep only if ≥ 3 briefs/month cite PDF data | **Decided (Dropped for MVP).** 3/3 fixture table accuracy (100%), but only 1/3 named seed `data` sources (`IBGE`) serves open un-gated PDFs (`< 3 briefs/month`), while `docling-serve-cpu` consumes 7.05 GiB disk and 1.60 GiB RSS on a 4 GB VPS. Plugin & profile omitted from runtime. |

### Completed Trials Summary (M0, M1, M3)

- **M0-09 (DBOS Transact):** Tested two-stage toy pipeline against ADR-001 state machine. The state machine achieved identical failure isolation, resume, and single-active-run constraints in ~200 LOC without extra dependencies or database schema requirements. Confirmed hand-written state machine; DBOS preserved as upgrade path.
- **M1-11 (RSSHub):** Evaluated routes for 13 `site_search` sources in `packs/industries/general/sources.yaml`. 0 sources yielded dated items without custom scrapers/Chromium. Dropped from stack; `rsshub` service profile removed from `compose.yaml`.
- **M1-11 (GDELT DOC 2.0):** Tested `NewsSearchPort` plugin with 1 req / 5 s rate limit against Brave Search News over 14 days of pt-BR optical queries. Brave returned 12 relevant trade/retail stories with verified dates and author attribution; GDELT returned 0 unique relevant stories (0% unique contribution vs 20% threshold) and exhibited repeated 429 rate limits. Dropped; Brave remains the sole search plugin for M1.
- **M3-03 (Docling):** Built `DoclingExtractor` (`ExtractorPort`, `application/pdf` only) and benchmarked `quay.io/docling-project/docling-serve-cpu:latest` against 3 PDF fixtures (real IBGE August 2026 PMC `pmc_202608caderno.pdf` 3-page excerpt `Tabela 1` & `Tabela 6`, Vision Council *Market inSights* summary, Euromonitor *Eyewear* public summary). Docling achieved 3/3 (100%) table extraction accuracy vs `trafilatura`'s 0/3 (`None` on PDFs), taking 34.07s CPU on the 3-page IBGE excerpt. However, live source probing showed only 1/3 named seed `data` sources serves open un-gated statistical PDFs (IBGE on `ftp.ibge.gov.br`, `pt-BR` only; Vision Council gates PDFs behind member login with public stats on HTML pages; Euromonitor returns HTTP 403 / paid Passport), yielding `< 3 briefs/month` relying on PDF-only data. Meanwhile, `docling-serve-cpu` required 2.07 GiB compressed / 7.05 GiB unpacked image storage, 820.8 MiB idle RAM, and 1.60 GiB active RSS RAM (~40% of the ADR-012 4 GB VPS memory budget). **Dropped** from production entry points and Compose profiles; candidate plugin and benchmark suite preserved in `spikes/trials/`. Revisit only when `>= 3` un-gated PDF data sources feed `>= 3` briefs/month and host RAM `>= 8 GB` (or via a lightweight external parsing API).

### Assess (with trigger)

| Tech | Trigger |
|---|---|
| Procrastinate (Postgres task queue) | Only if DBOS is rejected and a queue is needed (> 1 worker) |
| pgvector + embeddings for semantic dedup | Trigram dedup misses > 10 % of duplicates in a manual sample |
| PG 19 temporal constraints (`WITHOUT OVERLAPS`, temporal FKs) | If brand/campaign need effective-dated versions (scheduled changes) |
| Postgres row-level security | Second developer, or first security review |
| `ty` / basedpyright (faster type checkers) | mypy `--strict` CI time > 2 min |
| testcontainers-python | Need per-test isolated databases; until then, the Compose `db` service + transaction-rollback fixtures |
| OpenRouter Broadcast → Langfuse **Cloud** | Prompt iteration volume makes the `usage_ledger` + Streamlit view insufficient; review the data-processing terms first |
| **[Strands Agents SDK (`strands-agents`)](https://strandsagents.com/)** (`M5-08`) | Headless/autonomous **Agent-Driven Mode** execution where an external agent connects via `MCPClient` to `macy mcp` to research, write, and self-correct drafts (`revision 0..2`) without putting agent loops inside `engine/` (`ADR-002`, `ADR-027`) |

### Hold

| Tech | Why not |
|---|---|
| Langfuse **self-hosted** | v3 needs Postgres + ClickHouse + Redis + S3: too heavy for the single VPS for what `usage_ledger` already gives |
| PydanticAI / LangChain / CrewAI *inside `engine/`* | Heavy internal agent frameworks violate deterministic contract-in/contract-out steps (`ADR-002`). External agent orchestration belongs outside `engine/` over MCP (`ADR-027`, `strands-agents` / Antigravity / Claude Code) |
| Crawl4AI / Firecrawl (self-hosted) | Need Chromium/Redis; trafilatura covers static news sites. Revisit only for JS-rendered sources |
| Svix server (self-hosted webhooks) | Our outbox handles this volume; Standard Webhooks keeps us compatible if we ever move to Svix |
| Temporal / Hatchet | Superseded as the upgrade path by the DBOS trial (no extra service) |
| n8n inside our stack | Users run their own; we integrate via webhooks (ADR-008) |

## 3. Net effect if all Adopt + successful Trials are applied

- **Less custom code:** the LLM retry loop (Instructor), the webhook signature format (Standard Webhooks), possibly the stage runner and scheduler (DBOS), and the migration runner (dbmate).
- **More safety:** squawk on migrations, composite foreign keys, constraints in the database, property tests.
- **Lower running cost:** keyword cache, RSSHub/GDELT replacing some paid search calls, retention purges.
- **No new long-running services**, except RSSHub if its trial passes (one container).

## 4. Sources

- DBOS: [repo](https://github.com/DBOS-project/dbos-transact-py), [July 2026 update](https://dbos.dev/blog/new-in-dbos-july-2026), [June 2026 update](https://dbos.dev/blog/new-in-dbos-june-2026)
- Procrastinate: [PyPI](https://pypi.org/pypi/procrastinate)
- Standard Webhooks: [spec repo](https://github.com/standard-webhooks/standard-webhooks)
- Instructor: [OpenRouter integration](https://python.useinstructor.com/integrations/openrouter/), [retries](https://python.useinstructor.com/concepts/retrying/index.md)
- PydanticAI via OpenRouter: [OpenRouter community docs](https://openrouter.helicone.ai/docs/community/pydantic-ai), [reported JSON issue](https://blog-438.pages.dev/posts/openrouter-pydantic-gemini-structured-output)
- PostgreSQL: [UUIDv7 in PG 18](https://www.thenile.dev/blog/uuidv7), [PG 19 temporal features](https://news.lavx.hu/article/looking-forward-to-postgres-19-it-s-about-time), [PG 19 RC1](https://www.postgresql.org/about/news/postgresql-19-rc-1-released-3409/)
- RSSHub: [overview 2026](https://blog.brightcoding.dev/2026/02/10/rsshub-the-revolutionary-rss-generator-every-developer-needs)
- squawk: [project listing](https://awesome.ecosyste.ms/projects/github.com%2Fsbdchd%2Fsquawk), [Postgres SQL review tools 2026](https://www.bytebase.com/blog/top-postgres-sql-review-tools/)
- Langfuse: [OpenRouter integration](https://langfuse.com/integrations/gateways/openrouter), [self-host components](https://docs.railway.com/guides/langfuse-llm-observability.md)
- GDELT: [DOC 2.0 API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/), [Python client notes](https://pypi.org/project/gdelt-client/)
- lingua-py: [repo](https://github.com/pemistahl/lingua-py)
- Docling: [2026 guide](https://bestllmfor.com/guide/docling-conversion-documents-ia/), [table extraction comparison](https://llamaindex.ai/insights/best-ai-for-pdf-table-extraction)
- Crawlers: [Firecrawl vs Crawl4AI](https://zackproser.com/blog/firecrawl-vs-crawl4ai), [Spider benchmark](https://spider.cloud/blog/firecrawl-vs-crawl4ai-vs-spider-honest-benchmark/)
- testcontainers: [python guide](https://qaskills.sh/blog/testcontainers-python-pytest-integration-guide)
