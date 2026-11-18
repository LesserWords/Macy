# F05 — Integrations management

**Status:** Done (`M0-06`, `M0-07`, `M1-06`, `M4-07`, `M6-02`–`M6-03`, `M7-03` zero-key adapters & presets delivered) · **Milestone:** M0, M1, M4, M6, M7 · **Plan items:** M0-06, M0-07, M1-06, M4-07, M6-02, M6-03, M7-03 · **ADRs:** 005, 007, 008, 028, 031 · **OpenSpec capability:** `integrations`

## User story
As an owner, I connect the external services myself, entering keys once and testing them. That covers the OpenRouter LLM (and its model for each stage), news search, keywords and publisher.

## Scope
- In:
  - per-port plugin choice;
  - a form generated from the plugin's `Settings`;
  - encrypted secrets that are never shown again;
  - Test connection;
  - model per stage;
  - `cli integrations set`;
  - key rotation.
- Out: OAuth-based integrations (e.g. Google Search Console) until Phase 4.

## Acceptance criteria
- **AC1** WHEN the owner picks a plugin for a port, THEN a form is rendered from its `Settings` JSON Schema, with `SecretStr` fields as password inputs.
- **AC2** WHEN it is saved, THEN non-secret fields go to `settings` and secrets are Fernet-encrypted in `secrets`. Neither the UI, the logs, `services` nor `events` ever contain a secret value.
- **AC3** WHEN Test connection is clicked, THEN the plugin's `healthcheck()` runs and `last_test_ok` / `last_tested_at` are stored.
- **AC4** WHEN a run needs a port that has no configured plugin, THEN the run becomes `blocked(integration_missing:<port>)` and a `run.failed` event names the port.
- **AC5** WHEN the LLM model for a stage is changed, THEN the next stage uses it and `usage_ledger.model` reflects the change.
- **AC6** WHEN `cli.py rotate-key` runs with a new key, THEN all secrets are re-encrypted and remain readable.
- **AC7** WHEN a URL setting points to a private or metadata IP, THEN saving or testing is refused (`safe_http`).

## Data
`workspace_integrations`

## Interfaces
- `plugin_config(workspace_id, port)`
- `services.save_integration()`
- `services.test_integration()`
- plugin `Settings` + `healthcheck()`

## Tests
| Seam | Test | E2E |
|---|---|---|
| services | Encryption round-trip; secrets absent from returned objects and logs (caplog) | — |
| plugins | Contract suite per port (fake + real plugins, recorded) | — |
| UI | Configure OpenRouter, Brave and `http_webhook`; Test connection | **J8** |

## Dependencies
F01, M0-06
