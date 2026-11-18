# User & Editorial Guide

This guide explains how workspace owners and editors configure **Macy** and run the weekly content workflow using the **Streamlit Admin UI** (`app.py`).

> **Related guides:**
> - Where to get API keys, OIDC & env variables → [Environment Variables & External Credentials Guide](environment-and-credentials.md)
> - Receiving outbound notifications in n8n → [n8n Webhooks Guide](n8n-webhooks.md)
> - Publishing approved articles to any CMS via n8n → [n8n CMS Publishing Guide](n8n-publish.md)
> - CLI commands, local setup, and developer workflows → [Developer & CLI Guide](developer-guide.md)
> - VPS deployment, backups, and retention → [Operations Runbook](../runbook.md)

---

## 1. Signing In & UI Language

### Authentication
- **Staging & Production (`APP_ENV=staging` or `production`):** Click **Sign in with Google** (`st.login("google")`). Your Google account email must already hold an `owner` membership in a workspace (seeded by an operator with `content-engine add-owner <workspace-slug> <email>`). Accounts without membership see an access-denied message and zero workspace data.
- **Local Development / Testing (`APP_ENV=development` or `test`):** Set `MACY_TEST_USER_EMAIL=<email>` in your environment before launching the UI to bypass OIDC locally. This test identity is strictly refused when `APP_ENV` / `ENV` is `production` or `prod`.

### Bilingual Interface (`pt-BR` / `en-US`)
- On first login, Macy inspects your browser's `Accept-Language` header (`pt` → `pt-BR`, `en` → `en-US`, defaulting to `pt-BR`).
- Use the **Language** selector in the left sidebar at any time to switch between **Português (Brasil)** and **English (US)**. Your choice is saved to your user profile (`users.ui_locale`) and persists across sessions.
- **Important:** The sidebar **Language** selector controls only the Admin UI language. The language of generated reports, briefs, and articles is controlled separately by **Content Locale** in **Campaigns**.

---

## 2. First-Time Workspace Setup (Admin UI Screens)

Before running your first live editorial cycle, configure your workspace across five sidebar screens.

### Step 1: Integrations (`Integrations`)
Macy connects to external services through swappable **ports**. Secrets are Fernet-encrypted at rest (`APP_ENCRYPTION_KEY`) and are never displayed again after saving (only masked hints appear).

1. Select an **Integration Port** and **Plugin**:

| Port | Available Plugins | Required / Key Settings |
|---|---|---|
| `llm` | `openrouter` (live) · `fake` (offline tests) | `api_key`, `default_model`, **LLM Models by Stage** (`research`, `score`, `brief`, `write`, `evaluate`), `data_policy` (`deny` or `allow`), `base_url` |
| `feed_source` | `rss` (live) · `fake` | `user_agent`, `timeout_seconds` |
| `news_search` | `brave` (live) · `fake` | `api_key`, `count` (`1–50`), `unit_cost_usd`, `base_url`, `timeout_seconds` |
| `extractor` | `trafilatura` (live) · `fake` | `respect_robots_txt`, `include_links`, `timeout_seconds`, `user_agent` |
| `keyword_provider` | `dataforseo` (live) · `fake` | `api_login`, `api_password`, `unit_cost_usd`, `base_url`, `timeout_seconds` |
| `publisher` | `markdown_export` · `http_webhook` · `fake` | `markdown_export`: `export_dir`, `base_url`, `default_mode` (and optional `schema_json_ld`)<br>`http_webhook`: `endpoint_url`, `signing_secret`, `timeout_seconds`, `default_mode` |
| `analytics_source` | `gsc` (live) · `fake` | Hybrid Google Search Console auth (`service_account` JSON or `oauth2` client/refresh token), `site_url` |

> **Optional Audit & Rank-Tracking Toggles (`ADR-028`):** Browser accessibility (`BrowserAuditPort` / `axe-core` via `--extra audit-browser`), Google PageSpeed Insights (`PageSpeedPort`), and paid SERP rank tracking (`RankTrackerPort` gated by workspace `rank_tracking_enabled` and `usage_ledger` spend caps) toggle cleanly per workspace without changing the core pipeline.

2. Click **Save Integration**, then click **Test Connection** to verify credentials (`healthcheck()`).
   - **Tip:** For cost control, use a fast, low-cost model (such as `anthropic/claude-haiku-5.5`) for bulk `research` and `score` stages, and a stronger reasoning/writing model (`anthropic/claude-opus-5.5`) for `brief`, `write`, and `evaluate`.
   - **SSRF protection:** Any private, loopback (`127.0.0.1`, `::1`), or cloud-metadata (`169.254.169.254`) URL is refused. Production URLs must use HTTPS.

### Step 2: Campaigns (`Campaigns`)
A campaign defines what language/market to target, how many articles to aim for per week, scoring priorities, and hard spend limits.

1. Fill in **Campaign Settings**:
   - **Campaign Name:** Display name (e.g., `Organic Growth BR`).
   - **Content Locale & Target Market:** e.g., `pt-BR` / `BR` or `en-US` / `US`. Saving validates that all currently configured plugins support the chosen locale (`UnsupportedLocale` otherwise).
   - **Articles per Week (`1–20`):** Determines how many top topics the Weekly Report marks as recommended (minimum `3` recommended cards shown when available) and caps how many topics can be approved per run.
   - **Run Schedule (Cron):** 5-field cron expression evaluated in the workspace timezone (default `0 9 * * 1` — Mondays at 09:00).
   - **Monthly Spend Cap (USD)** and **Per-Run Spend Cap (USD):** Required positive amounts (`run_cap_usd <= monthly_cap_usd`). Watch the live **Estimated Monthly Cost** preview below the form before saving.
   - **Campaign Goal & Scoring Weights:** Six weights (`Novelty`, `Audience Fit`, `Brand Fit`, `Competitor Gap`, `Trend`, `Urgency`) that **must sum to 100**.
2. Click **Save Campaign**. Every change writes an audit event (`settings.changed`) with before/after values.

### Step 3: Brand Profile (`Brand Profile`)
Your Brand Profile steers editorial briefs, article tone, and deterministic prohibited-phrase checks.

1. Enter:
   - **Brand Voice** & **Positioning**
   - **Capabilities** (one per line)
   - **Prohibited Claims** (one per line — claims the writer must never make)
   - **Prohibited Phrases** (one regex per line — e.g. `\bguaranteed profit\b`; invalid regex syntax is rejected immediately naming the field)
   - **CTA Rules** (one per line — e.g. TOFU vs MOFU vs BOFU call-to-action rules)
2. Click **Save Brand Profile**.
   - Every save creates an immutable new version (`v1`, `v2`, …). Generated briefs record exact `brand_version` provenance.
   - Operators can also seed the initial Acme Demo profile with `content-engine seed-brand <workspace-slug>`.

### Step 4: Trusted Sources (`Sources`)
Sources drive what Macy reads during research and brief evidence gathering.

- **Roles (`role`):**
  - `news` & `company`: Discovery inputs that become candidate topics in the Weekly Report.
  - `data` & `management`: Evidence inputs searched at **brief time** for verified statistics and operational context; never proposed as standalone topics.
- **Tiers (`tier`):** `1` (primary/authoritative), `2` (trade/industry), `3` (general/secondary).
- **Fetch Modes (`fetch_mode`):**
  - `feed`: Polls an RSS/Atom **Feed URL** directly (validated on save via `safe_http`).
  - `site_search`: Queries your configured `news_search` plugin scoped to the source **Domain**.
- Use the **Select Source to Edit** dropdown (`Add New Source` or pick an existing source) to add/edit entries, or click **Enable / Disable** in the source list below.
- **Tip (Prevent Duplicate Topic Proposals):** Have an operator run `content-engine import-sitemap <blog-sitemap-url> --workspace <slug>` so previously published blog posts are flagged as **already covered** in future reports.

### Step 5: Outbound Webhooks (`Webhooks`) — Optional
Subscribe external automation tools (such as n8n or Slack) to engine lifecycle events:
- **Pipeline events:** `topics.awaiting_approval`, `topic.decided`, `brief.created`, `run.completed`, `run.failed`, `budget.cap_reached`, `draft.awaiting_approval`, `draft.decided`, `article.published`.
- **Monitoring & integration events (`M6-03`):** `audit.score_dropped`, `audit.critical_issue`, `monitor.tls_expiring`, `integration.auth_failed`.
- On creation, **copy the `whsec_...` signing secret immediately** — it is shown only once.
- Use the **Delivery Log** tab to inspect HTTP status codes, retry counts, errors, and click **Re-send** on failed or `dead` deliveries. See [n8n Webhooks Guide](n8n-webhooks.md).

---

## 3. Running the Weekly Editorial Cycle

### A. Triggering a Run (`Runs` & `Dashboard`)
Open **Runs** to inspect the active campaign's schedule, spend caps, run history, and **Cost & Spend Overview**.
- **In the UI:** Clicking **Run now** creates a `queued` run row (refused if a run is already queued/running, or if monthly spend has reached `monthly_cap_usd`).
- **Execution & Adapter Resolution:**
  - The `worker` container (`python -m engine`) polls queued runs every `15 s` (`run_worker_tick()`), compiles weekly reports on completion, and delivers pending webhooks continuously. Operators can also trigger runs synchronously via CLI (`content-engine run-week <workspace> [--offline]` or `content-engine run-now <workspace> [--offline]`).
  - **Live vs Fake Plugins:** Any port configured under **Integrations** (`openrouter`, `brave`, `dataforseo`, `rss`, `trafilatura`, `markdown_export`, `http_webhook`) runs live even in `APP_ENV=development`. In `development`, unconfigured ports fall back to `Fake*` adapters; in `staging`/`production` (or `--offline=false`), `feed_source`/`extractor` default to `rss`/`trafilatura` and missing `llm` or `news_search` transitions the run cleanly to `blocked` (`integration_missing`).
  - **Scheduled Weekly Cron:** `schedule_cron` is exported by `content-engine crontab` for host cron / Supercronic; clicking **Run now** uses the background `worker` immediately without external cron setup.
- **Cost & Spend Overview** (also shown on **Dashboard**) displays:
  - Current month spend vs monthly cap and remaining budget
  - Average cost per run and average cost per published article
  - Per-run cost table and per-published-article breakdown against the **`< $5.00` pilot target**

### B. Reviewing Topics & Generating Briefs (`Weekly Report` — Gate 1)
Once a run completes research and scoring, click **View Weekly Report** (or select **Weekly Report** in the sidebar) and pick the run:

1. **Inspect the Top 10 Opportunities:**
   - Top *N* opportunities (matching `articles_per_week`, minimum `3`) are marked with a star as **Recommended Topic**.
   - Each card shows **Angle**, **Funnel Stage** (`TOFU`/`MOFU`/`BOFU`), **Total Score** (`/100`), **Confidence**, **Publication Date** (`Verified` or `Unverified` — unverified dates are never shown as verified), **View Original** link, **Summary**, and **Evaluation Scores & Evidence** (with `(Estimated)` badges where keyword data is not yet fetched).
2. **Make Gate 1 Decision (Campaign Creator `owner` only):**
   - Optionally enter a **Decision Note**.
   - Click **Approve** (up to `articles_per_week` approvals per run) or **Reject** (prevents the same story/duplicate cluster from being recommended in future runs).
   - Any undecided `scored` topics expire automatically when the next run begins.
3. **Inspect the Editorial Brief:**
   - Approving a topic automatically generates an **Editorial Brief** (`v1`) when a Brand Profile exists (or click **Generate Brief** if you configured the brand afterward).
   - The brief panel shows `Brand Version`, `Format`, `Topic`, `Target Audience`, `Angle`, **Target Keywords** (with search volume or `(Estimated)` label), numbered **Outline**, **CTA Goal**, and **Verified Facts** linked to evidence document IDs.
4. **Write & Evaluate the Draft:**
   - Click **Write & Evaluate Draft** below the brief. Macy drafts the article, runs all free deterministic checks (source provenance, freshness, URL status, primary keyword placement, meta/slug format, prohibited brand phrases, CTA intensity, language match via `lingua-py`, FormatPack `word_count` bounds across `blog-short`/`blog`/`blog-long`, plus optional extended SEO checks: keyword/subheading density and tiered bilingual Flesch readability using `en-US` and Martins et al. `pt-BR`), and invokes the isolated LLM evaluator (routing up to 2 automatic revisions to `brief` or `write` if needed).
   - Ready-made `.md` or `.pdf` drafts can also be imported for audit via CLI `content-engine import-draft` or MCP `macy_import_draft` (`deterministic_only=True` or full rubric), while structured JSON-LD (`Article`, `FAQPage`, `Product`, `LocalBusiness`, `HowTo`) and modular LLM rewrite modes (`improve`, `simplify`, `seo_optimize`) operate independently.
   - When finished, click **Open Draft Review** (or open **Draft Review** in the sidebar). If the evaluator returns `reject` (`research`), the topic is returned to the pool.

### C. Reviewing Drafts & Publishing (`Draft Review` — Gate 2)
The **Draft Review** screen shows status counters (`Awaiting Approval`, `Approved`, `Published`, `Declined`) and a draft selector. For each draft you can inspect:

1. **Decision & Publication Controls:**
   - If the draft reached 2 revisions and still had issues, a banner notes: *"Surfaced after 2 revisions for owner decision."*
   - Enter an optional **Decision Note** and click **Approve** or **Decline** (campaign creator `owner` only).
   - Once approved, click **Publish Now** (if a `publisher` plugin is configured). If a publication fails, inspect the error and click **Retry Publish**; if already published, **Re-publish** safely runs idempotently on the same `draft_id`.
   - **Staging safety rule:** When `APP_ENV=staging`, Macy always forces `mode="draft"` so staging tests never publish live content.
2. **Article Preview:** Rendered title, slug, meta description, Markdown body, FAQ pairs, and CTA.
3. **Claims & Sources Table:** Every claim (`Fact` or `Analysis`) mapped to its source document title, clickable URL, publication date, and `Verified` / `Unverified` date status.
4. **Quality Evaluation:** Evaluator verdict (`PASS` / `REVISION` / `REJECT`), overall score (`/100`), evaluator model, each **Deterministic Check** (`PASS`/`FAIL`, severity, detail), and **Evaluator Findings** (`severity`, `criterion`, `responsible_module`).
5. **Revision History & Run Cost Breakdown:** Every draft revision (`r0`, `r1`, `r2`), linked brief version, verdict/score, and stage-by-stage token + USD cost breakdown.

### D. Running Single-URL & Site SEO/GEO Audits (`SEO & GEO Audit`)
Open **SEO & GEO Audit** in the sidebar to audit any public blog post, landing page, or website across WordPress, Webflow, headless CMSs, or custom stacks:
1. Enter a public `URL` (`https://...`), choose your **Plan Tier** (`free` or `paid`), and set **Max Pages** (default `20` on free, up to `50` on paid plans, hard ceiling `200`).
2. Click **Run URL Audit** for a single-page score (`0–100`, grade `A+..F`) across 7 categories (`onpage`, `performance`, `usability`, `security`, `links`, `social`, `ai`/GEO), or **Run Site Crawl** for a bounded crawl detecting broken links, redirect chains, duplicate titles/meta descriptions, and orphan sitemap pages.

---

## 4. Common Errors & How to Resolve Them

| Message / Symptom | Cause | Resolution |
|---|---|---|
| `Access denied: '<email>' is not a member of any workspace` | Signed-in Google email is not registered as an owner in any workspace | Ask an operator to run `content-engine add-owner <workspace-slug> <email>` |
| Run status becomes `blocked` (`integration_missing`) | Live/production run lacked configured `llm` (`openrouter`) or `news_search` (`brave`) plugin | Configure the missing port in **Integrations** (verify **Test Connection**), then resume via `content-engine run-stage <run-id>` |
| `Integration missing for port '<port>'` | A stage requires a port (`llm`, `news_search`, `keyword_provider`, `publisher`, etc.) that has no saved row | Open **Integrations**, configure the named port, save, and click **Test Connection** |
| `Plugin '<plugin>' does not support locale '<locale>'` | Selected plugin cannot serve the campaign's `Content Locale` / `Target Market` | Switch the campaign locale/market (e.g. `pt-BR`/`BR` or `en-US`/`US`) or choose a compatible plugin |
| `Monthly budget cap reached. New runs are refused.` | Cumulative monthly spend in `usage_ledger` reached `monthly_cap_usd` | Raise `Monthly Spend Cap (USD)` in **Campaigns** or wait for the next month |
| Run status becomes `blocked` (`cap_reached`) | Run spend reached `run_cap_usd` before a paid stage call | Raise `Per-Run Spend Cap (USD)` and resume the run |
| `An active run is already queued or running for this campaign` | Database index `runs_one_active` prevents concurrent runs on one campaign | Wait for the active run to finish (`completed`/`failed`) before queuing another |
| `Forbidden` on topic/draft approval | Only the **campaign creator** (`campaigns.created_by`, role `owner`) can approve Gate 1 & Gate 2 in MVP | Sign in as the user who created the campaign |
| `Destination reported content live while PublishRequest.mode is 'draft'` | Remote CMS/n8n endpoint marked the post live (`raw.is_live=true`) when `draft` mode was requested | Fix the n8n / CMS node mapping per [n8n CMS Publishing Guide](n8n-publish.md) and click **Retry Publish** |

---

## 5. Pocket Guided Setup, Capabilities & Zero-Key Stacks (`M7`)

- **First-Boot Setup Wizard:** Starting Macy against a fresh database opens a 3-step bilingual wizard (`pt-BR` / `en-US`) to create your local admin login, toggle active capabilities (`core-compliance`, `seo-audit`, `editorial-pipeline`, `route-monitors`), pick a stack preset, and auto-seed your brand profile and sitemap from an existing website URL.
- **Stack Presets (`Integrations -> Stack Presets`):** Switch between `pocket_free` (zero API keys: `gnews_rss`, `autocomplete`, `rss`, `trafilatura`, `markdown_export`), `local_ollama` (`ollama` + free search/keywords), or `pro_cloud` (`openrouter`, `brave`, `dataforseo`, `http_webhook`) in one click.
- **Capability Switchboard (`content-engine capabilities`):** Hide unused sidebar pages and MCP tools (`editorial-pipeline`, `route-monitors`) when you only want Compliance Auditing and Brand Checking.
