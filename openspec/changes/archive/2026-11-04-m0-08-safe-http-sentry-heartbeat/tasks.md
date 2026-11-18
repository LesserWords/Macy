## 1. Outbound safe HTTP client and SSRF protection

- [x] 1.1 Write failing tests in `tests/test_safe_http.py` asserting HTTPS enforcement in prod, literal IP refusal (`169.254.169.254`, `127.0.0.1`, `10.x`, `::1`), 10s timeout, 1 MB cap, and cross-host redirect refusal → verify: tests fail
- [x] 1.2 Write Hypothesis property test asserting all private, loopback, link-local, and reserved IPv4 and IPv6 addresses are refused → verify: test fails
- [x] 1.3 Implement `engine/safe_http.py` with DNS resolution, IP range checking, response size limiting, and safe request helper → verify: all safe_http tests pass

## 2. Sentry integration with secret scrubbing and tags

- [x] 2.1 Write failing tests in `tests/test_observability.py` asserting `init_sentry`, workspace/run tags, and `before_send` scrubber removing `api_key`, `Authorization`, and tokens → verify: tests fail
- [x] 2.2 Implement Sentry configuration, context tagging, and recursive secret scrubber in `engine/observability.py` → verify: tests pass

## 3. Heartbeat ping helper

- [x] 3.1 Write failing tests in `tests/test_observability.py` asserting `send_heartbeat` executes ping, no-ops when URL is unset, and catches errors gracefully → verify: tests fail
- [x] 3.2 Implement `send_heartbeat` in `engine/observability.py` and verify all quality gates pass with 100% diff coverage → verify: check pipeline is green
