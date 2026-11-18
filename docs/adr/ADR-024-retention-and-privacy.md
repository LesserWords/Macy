# ADR-024: Data retention, privacy (LGPD) and copyright handling

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner (legal review before first paid customer)
**Related:** F20, ADR-010 · DATA-MODEL §5

## Context
The system stores three kinds of sensitive or regulated data:
- users' personal data, covered by LGPD;
- third-party article text, which raises copyright exposure;
- LLM request and response bodies, which are large and sensitive.

## Options considered
| Option | Fit |
|---|---|
| **A. Fixed retention per data class, nightly purge job, anonymise users on deletion** | Bounded exposure; the facts we need are kept |
| B. Keep everything | Growing legal and storage risk |

## Decision
A.

| Data | Retention |
|---|---|
| `llm_payloads` | 90 days |
| `documents.extracted_text` | 365 days; then set to NULL, with title, URL, date and hash kept |
| Delivered or dead `webhook_deliveries` | 30 days |
| `events` | 24 months |

- **User deletion:** the user's email and name are anonymised; `actor_user_id` is kept as an opaque id.
- **Copyright:** extracted text is internal evidence only. Articles contain original analysis, with at most one short quoted sentence per source, attributed.
- **Purge job:** `cli.py purge` runs nightly under the `retention` database role.

## Consequences
- Easier: storage stays bounded, and LGPD requests can be answered.
- Harder: old drafts can't be re-evaluated against purged text. Drafts keep their claims and links.

## Revisit when
A legal review happens, or a customer needs a different retention contract. Retention may become a per-workspace setting.

## Action items
- [x] M4-06: purge job + user anonymisation command + tests
