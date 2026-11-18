# Proposal

## Why

Plan item **M0-08** (docs/plans/M0.md). Implement safe outbound HTTP requests (`safe_http`) protecting against SSRF and resource exhaustion, integrate the Sentry SDK with secret scrubbing and workspace/run tags, and provide a heartbeat ping helper for run monitoring. Features: F20. ADRs: 008, 023.

## What Changes

- Write `engine/safe_http.py` providing:
  - HTTPS enforcement when running in production (`APP_ENV=production` or `ENV=production`);
  - DNS resolution checking against private, loopback, link-local, carrier-grade NAT, and cloud metadata IP ranges (`169.254.169.254`, `127.0.0.1`, `10.x`, `::1`);
  - 10-second request timeout;
  - 1 MB response size ceiling (`max_bytes=1_048_576`);
  - refusal of cross-host redirects;
  - `validate_safe_url(url, is_production)` and `safe_request(method, url, ...)`.
- Write `engine/observability.py` providing:
  - `init_sentry(dsn, environment)` with `before_send` scrubber stripping `api_key`, `Authorization`, and tokens from event headers, extras, and breadcrumbs;
  - `set_observability_tags(workspace_id, run_id)` applying Sentry context tags;
  - `send_heartbeat(url)` ping helper that is a no-op when unset and safe in test/dev.
- Add `sentry-sdk` dependency to `pyproject.toml`.
- Add test suites in `tests/test_safe_http.py` (including Hypothesis property tests over IP address ranges and literal edge cases) and `tests/test_observability.py`.

Out of scope:
- Self-hosted Langfuse (rejected per ADR-023).
- Celery / background worker queue monitoring (covered in later milestones).

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `operations`: Add requirements for outbound safe HTTP (SSRF guard), Sentry exception scrubbing, and heartbeat monitoring.

## Impact

- New: `engine/safe_http.py`, `engine/observability.py`, `tests/test_safe_http.py`, `tests/test_observability.py`.
- Modified: `pyproject.toml`, `openspec/specs/operations/spec.md`.
