# Architecture Decision Records

One file per decision. Template: [../templates/adr.md](../templates/adr.md). To change a decision, add a new ADR that supersedes the old one; never rewrite history. Every ADR names the plan items (PRs) that implement it.

| ADR | Decision | Status | Features |
|---|---|---|---|
| [001](ADR-001-orchestration.md) | Orchestration: DB state machine + cron; DBOS as the upgrade path | Accepted (M0 spike) | F18 |
| [002](ADR-002-llm-call-pattern.md) | One structured LLM call per step, no agents | Accepted | F09, F12–F14 |
| [003](ADR-003-research-sources.md) | Curated sources first, one search API (Brave); RSSHub/GDELT/Docling trials | Accepted | F07, F08 |
| [004](ADR-004-scoring.md) | LLM scores each criterion, code computes the total | Accepted | F09 |
| [005](ADR-005-ports-and-plugins.md) | Protocol ports + entry-point plugins | Accepted | F05, F07, F11, F16 |
| [006](ADR-006-python-and-quality-gates.md) | Python + fallow-equivalent quality gates | Accepted | all |
| [007](ADR-007-llm-gateway-openrouter.md) | OpenRouter + Instructor | Accepted (M0 spike) | F05, F17 |
| [008](ADR-008-user-configured-integrations.md) | All integrations user-configured; encrypted secrets; `safe_http` | Accepted | F05, F06, F16 |
| [009](ADR-009-multi-workspace-foundations.md) | Multi-workspace foundations from day one | Accepted | F01, F03, F05 |
| [010](ADR-010-data-model-principles.md) | Data model principles | Accepted | all |
| [011](ADR-011-postgres-and-migrations.md) | PostgreSQL 19 (18 until GA) + dbmate + squawk | Accepted | — |
| [012](ADR-012-docker-compose-and-hosting.md) | Docker Compose everywhere; existing VPS + Caddy | Accepted | F20 |
| [013](ADR-013-auth-and-approver.md) | Google OIDC + memberships; approver = campaign creator | Accepted | F01, F10, F15 |
| [014](ADR-014-admin-ui-and-services.md) | Streamlit over services layer; pt-BR/en-US UI | Accepted | F19 + all screens |
| [015](ADR-015-locale-runtime-setting.md) | Locale and market as runtime settings; pt-BR + en-US first | Accepted | F02 |
| [016](ADR-016-spend-control.md) | User-set spend caps + usage ledger | Accepted | F17 |
| [017](ADR-017-webhooks.md) | Standard Webhooks + transactional outbox | Accepted | F06 |
| [018](ADR-018-testing-strategy.md) | TDD at seams; offline E2E per PR; live E2E nightly | Accepted | all |
| [019](ADR-019-git-ci-pr-workflow.md) | GitHub + Actions; trunk-based small PRs | Accepted | — |
| [020](ADR-020-openspec-workflow.md) | OpenSpec: feature → capability spec, PR → change | Accepted | all |
| [021](ADR-021-publisher-port.md) | CMS-neutral Publisher; `markdown_export` + `http_webhook` | Accepted | F16 |
| [022](ADR-022-source-model.md) | Sources with roles, tiers, fetch modes | Accepted | F04 |
| [023](ADR-023-observability.md) | Usage ledger + Sentry + heartbeat | Accepted | F17, F20 |
| [024](ADR-024-retention-and-privacy.md) | Retention, LGPD, copyright | Accepted | F20 |
| [025](ADR-025-supporting-libraries.md) | lingua-py, Hypothesis (radar Adopt) | Accepted | F14 |
| [026](ADR-026-claim-judge-decision-models.md) | Claim-judge decision models (Jev-only judge accepted) | Accepted | F14 |
| [027](ADR-027-mcp-server.md) | Model Context Protocol server (dual transport, services boundary, fail-closed auth) | Accepted | F01, F20, F21 |
| [028](ADR-028-seo-audit-and-plan-toggles.md) | Modular SEO/GEO audit, bilingual readability & plan-tier toggles | Accepted | F05, F08, F13, F14, F17, F21 |
| [029](ADR-029-capability-plugins-and-switchboard.md) | Two-tier plugin architecture: capability plugins, switchboard & stateless mode | Accepted | F01, F05, F19, F21, F22 |
| [030](ADR-030-pocket-single-container-and-local-auth.md) | Macy Pocket single-container runtime, embedded worker, Python migrations & local auth | Accepted | F01, F18, F20, F22 |
| [031](ADR-031-zero-key-adapters-and-guided-onboarding.md) | Zero-key default adapters (`ollama`, `gnews_rss`, `autocomplete`) & guided URL onboarding | Accepted | F01, F03–F05, F07, F11, F22 |
| [032](ADR-032-oss-context-overlay-and-public-readiness.md) | OSS public-readiness, hybrid 3-layer context overlay + DB snapshot (`MACY_CONTEXT_DIR`), Apache-2.0 & clean `open-macy` release | Accepted | F01, F03, F04, F08, F09, F20–F23 |
| [033](ADR-033-macy-package-cli-and-tooling-identity.md) | Clean-break rename of Python package, CLI entrypoint (`macy`), `macy.*` plugin groups, default image & tooling from `content-engine` | Accepted | F01, F05, F18, F20–F23 |
