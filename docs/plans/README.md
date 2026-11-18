# Delivery Plan: PRs, Checks and Milestone Gates

The MVP is split into **40 core PRs across 6 milestones** (plus `BM-1`–`BM-6` benchmark PRs, `M5-03`–`M5-08` MCP/compliance PRs, `M6-01`–`M6-03` SEO/GEO audit & monitoring PRs, `M7-01`–`M7-04` Pocket & modular capability PRs, and `M8-01`–`M8-04` OSS public-readiness & context overlay PRs). Each PR is one vertical slice: it is specified in OpenSpec, built test-first, verified step by step, and merged to a protected `main` (ADR-019).

| Milestone | Weeks | PRs | Plan | Exit gate (all must hold) |
|---|---|---|---|---|
| M0 Foundation | 1–2 | 9 | [M0.md](M0.md) | CI enforces every gate on a skeleton; schema v1 is migrated; a second workspace is isolated; ADR-001 and ADR-007 spikes are decided |
| M1 Research + settings | 3–4 | 12 | [M1.md](M1.md) | Sign-in, settings, brand, sources and integrations screens work in both languages; a 14-day run stores dated, deduplicated records for pt-BR and en-US; offline E2E J0–J3 green; trials decided |
| M2 Scoring + gate 1 + staging | 5–6 | 7 | [M2.md](M2.md) | Report and gate 1 work; signed webhooks are delivered; blog inventory imported; **staging live**; live E2E J0–J4 green 3 nights in a row |
| M3 Keywords + brief | 7 | 3 | [M3.md](M3.md) | Approved topic → brief with keyword data or an estimate label; Docling trial decided |
| M4 Write, evaluate, publish, prod | 8–10 | 7 | [M4.md](M4.md) | Regression cases 1–7 pass; 3 end-to-end articles; publish via both plugins; prod stack and backups; live E2E J0–J8 green 3 nights in a row |
| M5 Production, MCP server & pilot | 11 → +4 wks | 8 (+3 hardening) | [M5.md](M5.md) | PRD §3 success criteria met over 4 weekly runs; MCP server & Agent-Driven Mode compliance pipeline deployed with fail-closed auth, 100% diff-cover, Compose/Caddy smoke & offline/live E2E |
| M6 SEO/GEO Audit, Schema & Monitoring | Post-M5 | 3 | [M6.md](M6.md) | Tiered bilingual readability & 5 JSON-LD schemas; CMS-agnostic URL & bounded site crawler (20/50/200 caps) with optional axe-core/PSI toggles; hybrid GSC, Standard-Webhook monitors & paid RankTrackerPort toggle |
| M7 Pocket & Modular Plugins | Post-M6 | 4 | [M7.md](M7.md) | Path B zero-Docker stateless CLI/MCP (`--stateless`, `uvx macy[core]`); Path A `compose.pocket.yaml` zero-`.env` stack, local PBKDF2 auth, embedded worker, zero-key adapters (`ollama`, `gnews_rss`, `autocomplete`), capability switchboard & 3-step Setup Wizard (`J-POCKET`) |
| M8 OSS Public-Readiness & Context Overlay | Post-M7 | 4 | [M8.md](M8.md) | Hybrid 3-layer context overlay (`MACY_CONTEXT_DIR` → Pocket `/data/context` → OSS) + `workspaces.config.industry_pack_snapshot` DB fallback & UI `.yaml` import/export; neutral `general` + `b2b-saas` starter packs & `seed_example.yaml`; domain-agnostic `prompts/score.md`; Apache-2.0 `LICENSE`/`CONTRIBUTING`/`SECURITY`; sanitized `openspec/changes/archive/`; `check_oss_hygiene.py` & clean `open-macy` orphan cutover green |
| BM Claim-judge benchmark | Unscheduled | 6 | [BM.md](BM.md) | Delivered key results D1–D7 met and outcome computed from W1–W9 |

## Workflow for every PR

1. **Pick** the next PR whose dependencies are merged.
   → verify: the "Depends on" items are merged on `main`.
2. **Branch** as `m<milestone>-<nn>-<slug>`, then `openspec propose`. The change's scenarios are copied from the feature's acceptance criteria (AC).
   → verify: `openspec validate` green; `tasks.md` lists vertical slices, each starting with "write failing test".
3. **Red:** write the failing tests for the first slice.
   → verify: the test run fails for the expected reason (output pasted into the PR).
4. **Green:** write the minimal code to pass, then refactor.
   → verify: tests pass, and `scripts/check.sh` is green.
5. **Repeat** steps 3–4 per slice.
   → verify: every scenario maps to at least one test (listed in `tasks.md`).
6. **E2E:** add or extend the offline journey step if the change is user-facing.
   → verify: `docker compose -f compose.yaml -f compose.e2e.yaml run e2e` is green.
7. **Docs:** update the feature status, the ADR (if a decision changed) and the runbook (if ops changed).
   → verify: the links resolve, and the PR template's docs box is ticked.
8. **Open the PR** with the template ([../templates/pr-plan.md](../templates/pr-plan.md)), the per-step evidence, and required checks green.
   → verify: GitHub shows all required checks passing.
9. **Merge** (squash), then `openspec archive`.
   → verify: the spec is updated; from M2 on, the staging deploy is green.

## Universal checks (required on every PR; enforced by CI where marked)

| Check | Enforced by |
|---|---|
| All §9.4 gates (complexity, dead code, duplication, boundaries, types, tenant scoping, squawk, translations) | CI `check` |
| 100 % diff coverage, ≥ 90 % branch coverage | CI `test` |
| Offline E2E | CI `e2e-offline` |
| `openspec validate` | CI `openspec-validate` |
| `docker compose config` for all files | CI `compose-config` |
| No secrets (gitleaks) | pre-commit + CI |
| Regression evals when `prompts/` or `packs/` changed | CI `eval` (path filter; costs money, capped) |
| PR ≤ ~400 changed lines (excluding tests and fixtures) | Review |
| Feature, ADR and runbook docs updated | Review (template checkbox) |

## Milestone close-out (last day of each milestone)

- [x] Exit gate above demonstrated, with evidence linked in the milestone file (M1–M4 exit gates documented in `docs/plans/M1.md`–`M4.md`; M6–M8 documented in `docs/plans/M6.md`–`M8.md`).
- [x] Every feature touched has its status updated (`F01–F19`, `F21–F23` Done; `F20` M5-01 verified locally/CI, live VPS & M5-02 pilot pending).
- [x] Tech radar reviewed; trial outcomes recorded in TECH-RADAR and the ADRs (DBOS, RSSHub, GDELT, & Docling dropped; `TECH-RADAR.md`, `ADR-001`, `ADR-003`, & `ADR-025` updated).
- [x] Risks in PRD §11 reviewed; new risks added (Paywall/anti-bot scraping mitigation covers gated statistical PDFs).
- [x] Next milestone's PR list re-checked for size; split any PR that exceeds 2 days (`M5-01` 1 day, `M5-02` 4-week pilot).

## Critical path

```
M0-01 → M0-03 → M0-04 → M0-05 → M0-06 → M0-07 ─┐
                                               ├→ M1-01 → M1-03 → M1-07 → M1-10 → M2-01 → M2-03 → M3-02 → M4-01 → M4-03 → M4-04 → M4-05 → M5-01
M0-02, M0-08, M0-09 (parallel)                 ┘
Off the critical path: M1-02, M1-04–06, M1-08/09/11/12, M2-02, M2-04–07, M3-01/03, M4-02, M4-06/07
```

Estimates assume about 1 PR per 1–2 days. A slip of more than 2 days on a critical-path PR triggers a re-plan, recorded in the milestone file.
