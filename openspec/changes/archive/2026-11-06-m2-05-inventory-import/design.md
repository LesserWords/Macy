# Design: Content Inventory Import & Already-Covered Check

## Architecture Overview
The content inventory import allows workspace owners to seed their existing blog corpus into Macy so that the engine never re-proposes topics that have already been published.

```
XML Sitemap / Index
      │
      ▼
safe_http.safe_request (SSRF protection)
      │
      ▼
Sitemap Parser (XML ElementTree / defused)
      │
      ├─► Extract <loc>, <lastmod>
      └─► Extract article title (HTML <title> tag or unslugified path)
      │
      ▼
db.upsert_publication / insert_publications (mode='live', status='published')
      │  (ON CONFLICT (workspace_id, url) DO NOTHING)
      ▼
publications table (GIN trigram index on title)
      │
      ▼
services.report(run_id)
      │
      ▼
trigram similarity(opp.title, pub.title) >= 0.60
      │
      ▼
opportunity["already_covered"] = True (F08 AC4)
```

## Database Schema
`publications` table definition:
- `workspace_id uuid NOT NULL`
- `id uuid NOT NULL DEFAULT uuidv7()`
- `draft_id uuid` (nullable for imported posts)
- `destination text NOT NULL DEFAULT 'sitemap'`
- `mode text NOT NULL CHECK (mode IN ('draft','scheduled','live'))`
- `status text NOT NULL CHECK (status IN ('pending','published','failed'))`
- `external_id text`
- `url text NOT NULL`
- `title text NOT NULL`
- `scheduled_at timestamptz`
- `published_at timestamptz`
- `response jsonb`
- `created_at timestamptz NOT NULL DEFAULT now()`
- `PRIMARY KEY (id)`
- `UNIQUE (workspace_id, id)`
- `UNIQUE (workspace_id, url)`
- GIN Index: `CREATE INDEX pub_title_trgm ON publications USING gin (title gin_trgm_ops);`

## CLI Interface
`cli.py import-sitemap <url> [--workspace <slug_or_id>] [--fetch-titles/--no-fetch-titles]`
- Validates the sitemap URL via `safe_http`.
- Recursively expands `<sitemapindex>` files if given a root sitemap index.
- Ingests all articles with `mode='live'` and `status='published'`.
- Prints summary count of imported and skipped (already existing) articles.
- Returns exit code 0 on success.

## Similarity Matching
- Query `SELECT id, title, similarity(title, %(topic_title)s) AS sim FROM publications WHERE workspace_id = %(workspace_id)s AND similarity(title, %(topic_title)s) >= 0.60 ORDER BY sim DESC LIMIT 1`.
- If match exists, set `already_covered = True` on the report opportunity.
