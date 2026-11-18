# MVP → Product: Architecture Analysis

**Subject:** structure proposed in [MVP-PRD.md](../prd/MVP-PRD.md) · **Date:** 2026-11-03 · **Question:** can this MVP become a sellable product without a rewrite, and what must change now vs later?

## 1. Verdict

The structure is a good base for a product. The parts that are expensive to change later are already right:

- typed data contracts between steps;
- external services behind swappable plugins;
- language and market chosen as settings at run time;
- run state stored in the database, with a person approving at each gate;
- end-to-end tests of the user flow.

**One assumption runs through the whole design: there is only one customer (Acme Demo).** It shows up in six places (§4). Each costs hours to fix now and weeks of data migration plus a check of every query to fix later. Fix those six now; defer the rest (§5).

## 2. Assumptions about "product later"

| # | Assumption | If wrong |
|---|---|---|
| A1 | Product = several customer companies ("workspaces") on one deployment, each with its own brand, sources, languages, approvers and CMS | If the plan is one installation per customer, §4 items 1 and 6 shrink; the rest still apply |
| A2 | Stay in one industry first (optical retail, several countries), then expand to other industries | Going broad from the start makes industry packs and evaluation cases the main cost (§6) |
| A3 | Early customers get help setting up; no self-signup or billing in year 1 | Self-signup pulls the UI rewrite and billing forward |
| A4 | Scale in year 1: up to ~50 workspaces × ~3 articles/week | See load estimate in §7 |

## 3. Requirements: MVP vs product

| | MVP (now) | Product (later) |
|---|---|---|
| Functional | 1 brand; pt-BR + en-US; weekly run; 2 approval gates; Markdown export | N workspaces; their own brand profiles; their own CMS plugins; team roles; notifications; usage reports |
| Availability | Best effort; a day of downtime is fine (weekly cadence) | Uptime target for the review UI; runs finish within their scheduled window |
| Isolation | Not needed | One workspace never sees another's drafts, briefs, settings or credentials |
| Cost | Caps set by the user per campaign | Same, plus cost per workspace for pricing and margin |
| Team | 1 developer | 1–3 developers, so module boundaries must keep holding |

## 4. Do now: one-way doors (cheap today, expensive later)

| # | Gap in current PRD | Change now | Cost now | Cost if deferred |
|---|---|---|---|---|
| 1 | **No tenant key.** No table has a `workspace_id` | Add `workspaces` table and a `workspace_id NOT NULL` column on every business table. Every query in `db.py` takes `workspace_id` as a required argument. Import-linter forbids raw SQL outside `db.py` | ~1 day | Backfill the data, audit every query, risk of cross-customer data leaks |
| 2 | **Brand is a git file** (`packs/brands/acme-demo/`). A customer can't edit their own brand voice | Move brand profile, prohibited claims and CTA rules into a `brand_profiles` table (JSONB checked against the same Pydantic model), edited in the UI. **Industry packs, formats and prompts stay in git**: they are product assets you own and test | ~2 days | Every customer onboarding needs a developer and a deploy |
| 3 | **Campaign settings are in two places** (`packs/campaigns/*.yaml` and the `campaign_settings` table) | Delete `packs/campaigns/`; the campaign lives only in the database | Less code | Two sources of truth that drift apart |
| 4 | **Plugin configuration is global** (`integrations.yaml` + env vars). In a product each customer brings their own CMS token, and possibly their own SEO account | Route every lookup through one function, `plugin_config(workspace_id, port)`. In the MVP it reads `integrations.yaml`; later it reads encrypted rows from `workspace_integrations`. Callers never change | ~0.5 day | Plugin wiring rewritten in every module |
| 5 | **Login = Google + email allowlist**; approver = campaign creator | Add `users` and `memberships(workspace_id, user_id, role)` tables, with the single role `owner` in the MVP. The allowlist becomes "has a membership". Approver check = `role in {owner}` | ~1 day | Approvals and audit rows tied to emails instead of users; roles bolted on later |
| 6 | **Logic can leak into the UI.** Streamlit `app.py` imports `engine` directly | Add an `engine/services.py` layer of application actions (`approve_topic`, `save_settings`, `run_now`, …). Everything goes through it: Streamlit, the CLI, and later a REST API. Import-linter rule: `app` may import only `engine.services` and `engine.contracts` | ~1 day, and it makes the tests better | Moving off Streamlit means rewriting business logic, not just screens |

Also free now:
- Add `workspace_id` to `llm_calls` and to API cost rows. Cost per customer then exists from day one, and pricing and margin analysis come with it.
- Give each source an owner: `workspace_id NULL` means a shared pack source. This keeps open sharing collected research across workspaces in the same industry later: collect once, serve many (§6).

## 5. Defer: two-way doors (cheap to change later)

| Item | MVP choice | Upgrade when | Upgrade to |
|---|---|---|---|
| UI | Streamlit | The first external customer logs in, or design matters for sales | FastAPI over `engine/services.py` + a real web front end (rewrites screens only, if §4.6 is done) |
| Job execution | Cron + DB state machine, single worker | Weekly runs in series take longer than about 1 day (≈ 50 workspaces × 30 min), or runs collide | Postgres-backed queue (Procrastinate) or Hatchet (ADR-001), with N workers |
| Hosting | One VPS, Compose for prod + staging | First paid SLA, or data-residency terms in a contract | Managed Postgres + 2 app hosts, or a PaaS |
| Isolation | `workspace_id` in queries | More than one developer writing queries, or the first security review | Postgres row-level security as a second layer |
| Roles | `owner` only | A customer has an editor plus an approver | `editor`, `approver`, `viewer` |
| Notifications | Outbound webhooks (MVP, ADR-008) — users wire email/Slack via n8n | Users without n8n need built-in email | Email channel as a webhook-like subscription type |
| Billing | None | Self-signup | Stripe metered on articles, using cost rows per workspace |
| Secrets | Encrypted column under `APP_ENCRYPTION_KEY` (MVP, ADR-008) | Security review or enterprise customer | Key held in a KMS, per-workspace data keys |

## 6. Product risks the architecture doesn't solve

1. **Each new industry needs its own content work, not code.** It needs a pack, sources, terminology and evaluation cases, and quality depends on them. *Mitigation:* stay in one industry (A2). Treat "pack + 7 evaluation cases pass" as the definition of a supported industry.
2. **Collecting third-party content commercially at scale.** robots.txt, terms of service and copyright matter more when you are selling the output. *Mitigation:* the rules already in the PRD hold (summaries and links only, short attributed quotes, original analysis). Get a legal review before the first paid customer.
3. **Margin per article.** LLM cost is ~$0.5–2 per article plus a fixed research cost per run. *Mitigation:* the per-workspace cost data (§4) informs pricing; the Haiku downgrade is the main cost lever once the regression suite proves it holds.
4. **Shared research across customers.** Two workspaces in the same industry and locale would collect and extract the same sources twice. Sharing research records (never opportunities, briefs or drafts) roughly halves research cost. The source-ownership field (§4) keeps this possible; build it only when two such workspaces exist.
5. **Single approver.** Pending items wait safely, but a customer's output stops when one person is away. Roles plus a notification (§5) fix it.

## 7. Load estimate

| | MVP | Product, year 1 (A4) |
|---|---|---|
| Research records/week | ~200 | ~50 × 200 = 10k (≈ 5k if research is shared) |
| Articles/week | 1–3 | ~150 |
| LLM calls/week (≈ 10 per article + scoring) | ~50 | ~3–5k |
| Postgres rows/year | ~15k | ~1M (fine on one small instance) |
| Worker time/week | ~0.5 h | ~25 h in series, **the first real limit** → queue + parallel workers |

The first limits are in this order: worker run time, then Claude rate limits and spend, then Postgres. A single Postgres instance holds year-1 volume easily.

## 8. Reliability & monitoring (small additions now)

- **Errors:** add Sentry (free tier, ~3 lines). Plugins are where failures happen.
- **Missed runs:** a heartbeat check (e.g. Healthchecks.io) pinged at the end of each weekly run. A missed ping sends an alert.
- **Retries:** wrap each external call in the plugin with exponential backoff (3 tries). The state machine already handles retrying a whole step. Publishing must not create duplicates: the publish contract already requires a re-publish of the same draft to be harmless, keyed by `draft_id`.
- **Product later:** a status page and a run-history screen per workspace.

## 9. Target shape (after the §4 changes)

```
            Streamlit (MVP)  ──┐
            CLI              ──┼──► engine/services.py  (workspace-scoped actions, authz check)
            REST API (later) ──┘            │
                                            ▼
     packs/ (git: industry, format, prompts)   modules (research · strategy · seo · production · quality)
                                            │  contracts only
                                            ▼
       plugin_config(workspace_id, port) ─► ports ─► plugins (rss, brave, trafilatura, dataforseo, publisher…)
                                            │
                                            ▼
   Postgres: workspaces · users · memberships · brand_profiles · campaign_settings · sources(owner)
             research_records · opportunities · briefs · drafts · quality_reports · published · llm_calls
             (every business row carries workspace_id)
```

> **Update 2026-11-03:** per-workspace integration config, encrypted credentials and outbound webhooks moved into the MVP (PRD ADR-008); item 4 is now fully implemented, not just routed.

## 10. Recommendation

Do the six §4 changes in M0/M1. Together they add about 5–6 developer-days to the ~9-week plan. After that, every item in §5 is an upgrade on its own, triggered by a measurable signal, with no rewrite. Keep the product to one industry until the second paying customer.
