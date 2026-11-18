# Features

One spec per feature. Template: [../templates/feature.md](../templates/feature.md). In M0, each feature seeds an OpenSpec capability spec (`openspec/specs/<capability>/spec.md`), and its acceptance criteria (AC) become WHEN/THEN scenarios. Once code exists, OpenSpec is the source of truth for behaviour; these docs keep the product view, data, dependencies and plan links.

| ID | Feature | Status | Milestone | Plan items | ADRs | E2E |
|---|---|---|---|---|---|---|
| [F01](F01-access-and-workspaces.md) | Access, workspaces, Google sign-in & local PBKDF2 auth | Done | M0–M1, M7 | M0-04, M0-05, M1-01, M7-02 | 009, 013, 014, 030 | J0, J-POCKET |
| [F02](F02-campaign-settings.md) | Campaign settings (locale, volume, schedule, strategy, caps) | Done | M1 | M1-03 | 004, 015, 016 | J1 |
| [F03](F03-brand-profile.md) | Brand profile | Done | M1, M3 | M1-04, M3-02 | 009, 010 | J1 |
| [F04](F04-source-management.md) | Trusted source management | Done | M1 | M1-05, M1-12 | 003, 022 | J2 |
| [F05](F05-integrations-management.md) | Integrations management & stack presets | Done | M0–M1, M4, M6–M7 | M0-06, M0-07, M1-06, M4-07, M6-02, M6-03, M7-03 | 005, 007, 008, 028, 031 | J8 |
| [F06](F06-webhooks.md) | Outbound webhooks (n8n) | Done | M2, M4, M6 | M2-04, M4-07, M6-03 | 008, 017, 028 | J8 |
| [F07](F07-research-collection.md) | Research collection | Done | M1 | M1-07, M1-08, M1-11 | 003, 005, 022 | J3 |
| [F08](F08-dedup-and-content-inventory.md) | Dedup + content inventory + URL/site SEO & GEO audit | Done | M1–M2, M6 | M1-09, M2-05, M6-02 | 003, 010, 028 | J3 |
| [F09](F09-scoring-and-weekly-report.md) | Scoring + weekly report | Done | M2 | M2-01, M2-02 | 002, 004 | J3 |
| [F10](F10-topic-approval-gate.md) | Topic approval (gate 1) | Done | M2 | M2-03 | 013 | J4, J7 |
| [F11](F11-keyword-research.md) | Keyword research | Done | M3, M6 | M3-01, M6-03 | 004, 005, 015, 028 | J4 |
| [F12](F12-editorial-brief.md) | Editorial brief | Done | M3, M4 | M3-02, M3-03, M4-03 | 002, 022 | J4 |
| [F13](F13-article-writing.md) | Article writing with provenance, JSON-LD schema & rewrite modes | Done | M4–M6 | M4-01, M5-07, M6-01 | 002, 010, 015, 028 | J5 |
| [F14](F14-quality-evaluation.md) | Quality evaluation, extended SEO/readability & revision loop | Done | M4–M6 | M4-02, M4-03, M5-07, M6-01, M6-02 | 002, 025, 028 | J5 |
| [F15](F15-draft-review-gate.md) | Draft review (gate 2) | Done | M4 | M4-04 | 013, 014 | J6, J7 |
| [F16](F16-publishing.md) | Publishing | Done | M4, M6 | M4-05, M6-01 | 008, 021 | J6 |
| [F17](F17-spend-control.md) | Spend control + cost view | Done | M0–M4, M6 | M0-07, M1-10, M4-07, M6-03 | 016, 023, 028 | J1 |
| [F18](F18-run-orchestration.md) | Run orchestration + scheduling + URL/TLS monitors | Done | M0–M1, M6 | M0-09, M1-10, M6-03 | 001, 010, 028 | J3 |
| [F19](F19-admin-ui-i18n.md) | Admin UI shell + pt-BR/en-US | Done | M1 | M1-02 | 014 | J1 |
| [F20](F20-operations.md) | Operations (deploy, backups, monitoring, retention) | Implemented (M5-01 verified locally/CI; live VPS & M5-02 pending) | M0–M5 | M0-03, M0-08, M2-06, M4-06, M5-01 | 012, 019, 023, 024 | smoke |
| [F21](F21-mcp-server.md) | Model Context Protocol (MCP) server (dual transport, stateless, auth, tools, Agent-Driven Mode, audit) | Done | M5–M7 | M5-03–M5-08, M6-01–M6-03, M7-01 | 012, 013, 014, 016, 026, 027, 028, 029 | J-MCP, smoke |
| [F22](F22-pocket-and-modular-capabilities.md) | Macy Pocket (Path A & Path B), capability plugins, zero-key adapters & guided setup | Done | M7 | M7-01–M7-04 | 029, 030, 031 | J-POCKET |
| [F23](F23-oss-public-readiness-and-context-overlay.md) | OSS public-readiness, private context overlay & domain decoupling | Done | M8 | M8-01–M8-04 | 032 | overlay-smoke |
