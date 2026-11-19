# Content Intelligence Engine — Documentation

The documents link to each other in a chain: the **PRD** sets what and why; each **feature** spec defines a capability with acceptance criteria; each **ADR** records one decision; each **plan** turns features into PRs with a check for every step; and the **guides** and **runbook** explain how to operate and extend the system.

```
PRD (what/why) ──► Features F01–F23 (acceptance criteria) ──► OpenSpec capability specs (openspec/specs/)
      │                     │                                        │
      └──► ADRs 001–033 ◄───┘  (every decision, linked both ways)     │
                            │                                        ▼
                            └──► Plans M0–M9 ──► each PR = 1 OpenSpec change + step checks + universal checks
```

| Folder / Doc | Contents | Start here |
|---|---|---|
| [guides/](guides/user-guide.md) | User & Editorial Guide, Developer & CLI Guide, Environment Variables & External Credentials, n8n Webhooks & CMS Publishing Guides | [user-guide.md](guides/user-guide.md) · [environment-and-credentials.md](guides/environment-and-credentials.md) |
| [MCP.md](MCP.md) | MCP server guide (`stdio`, HTTPS `streamable-http` behind Caddy, 17 tools, Agent-Driven Mode compliance loop, URL/site audits) | [MCP.md](MCP.md) |
| [runbook.md](runbook.md) | Staging & prod VPS setup, deploy, rollback, logs, backups, restore, retention purge, LGPD anonymisation | [runbook.md](runbook.md) |
| [prd/](prd/MVP-PRD.md) | MVP PRD: problem, scope, success criteria, architecture, stack, process, risks, decisions log | [MVP-PRD.md](prd/MVP-PRD.md) |
| [features/](features/README.md) | 23 feature specs (user story, scope, acceptance criteria (AC), data, interfaces, tests, dependencies) | [index](features/README.md) |
| [adr/](adr/README.md) | 33 Architecture Decision Records | [index](adr/README.md) |
| [plans/](plans/README.md) | Delivery plan: PR workflow, universal checks, critical path, milestone files M0–M9 | [overview](plans/README.md) |
| [architecture/](architecture/DATA-MODEL.md) | [DATA-MODEL.md](architecture/DATA-MODEL.md) (full schema), [TECH-RADAR.md](architecture/TECH-RADAR.md), [MVP-Product-Analysis.md](architecture/MVP-Product-Analysis.md) | [DATA-MODEL.md](architecture/DATA-MODEL.md) |
| [../openspec/](../openspec/specs/) | Source-of-truth capability specs and archived PR change deltas | — |
| [templates/](templates/) | [ADR](templates/adr.md), [feature](templates/feature.md), [PR](templates/pr-plan.md) templates | — |

## Rules for keeping docs true

1. **New decision** → new ADR from the template, added to [adr/README.md](adr/README.md). Superseding is done with a new ADR, never by editing an accepted one's decision.
2. **New or changed feature** → update its F-file (acceptance criteria and status) and its OpenSpec capability spec in the same PR.
3. **Every PR** names its plan item, features, ADRs and OpenSpec change (PR template).
4. **Milestone end** → close-out checklist in [plans/README.md](plans/README.md): statuses, trial outcomes, risks.
5. **Implementation docs** (`runbook.md`, `guides/`) are updated alongside the behaviour they document.

## Status (2026-11-19 — M8 Complete; M9 Unified `macy` Rename Planned)

| Area | State |
|---|---|
| PRD | Baseline MVP specification ([MVP-PRD.md](prd/MVP-PRD.md)); pilot criteria §3 evaluated in M5-02 |
| ADRs | 33 accepted (`ADR-001`–`033`); all M0/M1/M3 spikes decided (DBOS dropped, OpenRouter confirmed, RSSHub dropped, GDELT dropped, Docling dropped) |
| Features | `F01–F19`, `F21–F23` Done; `F20` (`M5-01`) verified locally/CI (live VPS awaiting external credentials) |
| Plans | **M0–M4** (`38` PRs), **M5-03–M5-08** (MCP, Agent-Driven Mode `.md`/PDF compliance, Strands), **M6-01–M6-03** (readability, schema, audits, GSC, route monitors), **M7-01–M7-04** (`M7.md`, Pocket runtime, Path B stateless, adapters & Setup Wizard), and **M8-01–M8-04** (`M8.md`, OSS public-readiness, hybrid `./context` + DB snapshot overlay & clean public cutover) complete; **M9-01–M9-03** (`M9.md`, `ADR-033`, unified `macy` package/CLI/tooling rename) planned; `M5-01a`–`M5-01c` hardening and `M5-02` pilot remaining |
| Remaining before live pilot / M9 execution | Execute `M9-01`–`M9-03` (`M9.md`, `ADR-033`) to replace `content-engine` with `macy`; optionally complete `M5-01a`–`M5-01c` hardening PRs, provision production VPS `/opt/macy/prod/.env` & `PROD_*` secrets, push `v0.1.0` (`M5-01`), and run `M5-02` 4-week pilot |
