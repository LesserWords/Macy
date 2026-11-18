# ADR-008: All external integrations configured by the user (plugins, credentials, n8n)

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** F05, F06, F16, ADR-005, ADR-017, ADR-021 · PRD §5.6, §5.7

## Context
Every external connection must be set up by the user, not by a developer or a deploy. That covers the LLM gateway, search, SEO, CMS, n8n, webhooks and any other API.

## Options considered
| Option | Fit |
|---|---|
| **A. Plugins + per-workspace configuration in the database + outbound webhooks** | Users configure everything in the UI; code stays typed and tested |
| B. Route all integrations through n8n | Core I/O would leave the tested code; rejected |
| C. Config files or environment variables per deploy | A developer changes every connection; fails the requirement |

## Decision
A.
- **Configuration storage:** `workspace_integrations` holds one row per (workspace, port): plugin, non-secret settings, and the secrets encrypted with Fernet under `APP_ENCRYPTION_KEY`.
  - Keys are rotated with MultiFernet and `cli.py rotate-key`.
  - Secrets are masked in the UI, never logged, and never returned by `services`.
- **Settings form:** the UI form is generated from each plugin's `Settings` JSON Schema, with a **Test connection** button (`healthcheck()`).
- **Lookup:** every module gets its settings through `plugin_config(workspace_id, port)`.
  - If a port has no plugin configured, the run is `blocked` with `integration_missing:<port>`; it never crashes.
- **Platform secrets:** only `DATABASE_URL`, `APP_ENCRYPTION_KEY`, the OIDC client and the Sentry DSN are environment variables.
- **n8n:** connects through webhooks (ADR-017) and through the `http_webhook` publisher (ADR-021). There is no n8n-specific code.
- **Outbound HTTP:** every call to a user-supplied URL goes through `safe_http`:
  - HTTPS only in production;
  - private, loopback, link-local and metadata addresses refused (SSRF protection);
  - 10 s timeout and 1 MB response cap;
  - no redirects to other hosts.

## Consequences
- Easier: onboarding a customer and swapping vendors need no deploy.
- Harder: encryption key management. The key must be backed up separately from database backups.

## Revisit when
There is an enterprise customer or a security review; then move the key to a KMS. When users need to drive the engine from n8n (inbound calls), add a thin FastAPI layer over `services` with per-workspace API tokens.

## Action items
- [x] M0-06: `workspace_integrations`, Fernet, `plugin_config()` and `cli integrations set`
- [x] M0-08: `safe_http` with property tests
- [x] M1-06: Integrations screen
