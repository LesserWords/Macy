# Tasks: Content Inventory Import & Already-Covered Check

## 1. Database Migration & Access Layer
- [x] 1.1 Create migration `20261106000000_create_publications.sql` with `publications` table, unique `(workspace_id, url)`, GIN trigram index on `title`, and squawk check pass.
- [x] 1.2 Implement database persistence functions in `engine/db.py`: `create_publication`, `bulk_insert_publications`, `get_publication_by_url`, `list_publications`, and `find_similar_publication(workspace_id, title, threshold=0.60)`.

## 2. Sitemap Parser & Ingestion Service Layer
- [x] 2.1 Implement `engine/sitemap.py` (or `engine/inventory.py`): safe XML sitemap parsing supporting `<urlset>` and `<sitemapindex>` with SSRF protection, date parsing, and title extraction.
- [x] 2.2 Implement `services.import_sitemap(workspace_id, sitemap_url)`: fetches and ingests articles idempotently with `mode='live'` and `status='published'`.
- [x] 2.3 Implement CLI subcommand `import-sitemap` in `engine/cli.py`.

## 3. Opportunity "Already-Covered" Check
- [x] 3.1 Update `engine/services.py` `report()` and `_format_report_item()` to check trigram similarity against `publications` in the workspace, setting `already_covered = True` when similarity >= 0.60 (F08 AC4).

## 4. Tests, Verification & Live Sitemap Import
- [x] 4.1 Unit and property tests in `tests/test_inventory.py` (sitemap parser, duplicate rejection, idempotency AC3, trigram matching AC4).
- [x] 4.2 CLI integration tests in `tests/test_cli.py`.
- [x] 4.3 Offline and live verification against the real Acme blog sitemap (`https://example.com/post-sitemap.xml`), asserting row count and spot-checking articles.
- [x] 4.4 Run all quality gates (100% diff-cover, ruff, mypy, complexipy <= 15, vulture, jscpd, squawk, check_translations, openspec validate).
