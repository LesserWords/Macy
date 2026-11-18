# Tasks: M6-03 — Post-Publish Monitoring, Hybrid GSC & Optional Rank Tracking

## 1. Hybrid GSC `AnalyticsSourcePort` (Delivery KR: D1)
- [x] 1.1 Write failing tests in `tests/test_gsc_integration.py` covering both `service_account` JSON and `oauth2` refresh-token credential shapes in encrypted `workspace_integrations`, Search Analytics queries, and 12-hour Postgres caching for URL Inspection (`2,000/day` quota protection).
- [x] 1.2 Implement `GSCAnalyticsSource` in `engine/integrations/gsc.py` and register `gsc` under `content_engine.analytics_source` in `pyproject.toml`. Verify D1: `uv run pytest tests/test_gsc_integration.py --no-cov` exits 0.

## 2. GSC Diagnostic Analyzers (Delivery KR: D2)
- [x] 2.1 Write failing tests in `tests/test_gsc_insights.py` for Striking-Distance (`pos 4..20`, high impressions, low CTR), Decay Triage (`7d`/`28d` comparison), and Cannibalization detection.
- [x] 2.2 Implement `engine/strategy/gsc_insights.py`. Verify D2: `uv run pytest tests/test_gsc_insights.py --no-cov` exits 0.

## 3. Scheduled URL/TLS Monitors & Standard Webhooks (Delivery KR: D3)
- [x] 3.1 Write failing tests in `tests/test_monitors.py` for `30s` single retry, TLS expiry (`<= 14d`), report diffing (`score_drop >= 5` or new `critical`), and Standard Webhook event emission (`audit.score_dropped`, `audit.critical_issue`, `monitor.tls_expiring`).
- [x] 3.2 Implement `engine/monitors/runner.py` and wire into `engine/scheduler.py`. Verify D3: `uv run pytest tests/test_monitors.py --no-cov` exits 0.

## 4. Paid `RankTrackerPort` Toggle (Delivery KR: D4)
- [x] 4.1 Write failing tests in `tests/test_rank_tracker.py` verifying plan-flag gating (`rank_tracking_enabled`), `keyword_rankings` history persistence, and `usage_ledger` spend caps (`BUDGET_CAP_EXCEEDED`).
- [x] 4.2 Implement `RankTrackerPort` + Serper/DataForSEO SERP adapters in `engine/integrations/serp_tracker.py`. Verify D4: `uv run lint-imports && uv run mypy && uv run pytest tests/test_rank_tracker.py --no-cov && sh scripts/check.sh` exits 0.
