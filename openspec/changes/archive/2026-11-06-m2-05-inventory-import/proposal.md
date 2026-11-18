# Proposal: Content Inventory Import & Already-Covered Check

## Why
To prevent proposing article topics that an existing customer blog has already covered (F08 AC3, AC4, Scope change S2), Macy needs to import historical publications from XML sitemaps into a tenant-scoped `publications` table and perform trigram similarity matching (`similarity >= 0.60`) against past publication titles when compiling the weekly opportunity report.

## What Changes
1. **Database Migration (`publications`):**
   - Create the `publications` table with `workspace_id`, `id`, `mode`, `status`, `url`, `title`, `published_at`, and `created_at`.
   - Add a unique index on `(workspace_id, url)` for idempotent imports.
   - Add a GIN trigram index on `title gin_trgm_ops` for efficient fuzzy matching.
2. **Sitemap Ingestion Service & CLI (`cli.py import-sitemap`):**
   - Support standard XML sitemaps (`<urlset>`) and sitemap index files (`<sitemapindex>`).
   - Validate target URLs using `safe_http` to guard against SSRF.
   - Extract publication URLs, dates (`lastmod`), and article titles (extracting `<title>` with clean fallback to slug).
   - Upsert/insert publication rows idempotently without duplicating existing entries on re-run (F08 AC3).
3. **Already-Covered Check in Weekly Report:**
   - In `services.report()`, check each opportunity's title against `publications` in the workspace using Postgres `similarity(opp.title, pub.title) >= 0.60`.
   - Flag `already_covered: True` on matching opportunities in the weekly report (F08 AC4).
4. **Real Blog Sitemap Verification:**
   - Verify importing the real Acme blog sitemap (`https://example.com/post-sitemap.xml`) in a live test, verifying expected row count and spot-checking articles.

## Capabilities Affected
- `publishing`
- `scoring`
