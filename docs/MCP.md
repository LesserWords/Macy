# Macy Model Context Protocol (MCP) Guide

This guide covers configuring, authenticating, and operating the Macy **MCP server** (`macy mcp`) across local `stdio` AI clients (**Antigravity**, **Claude Desktop**, **Claude Code**, **Cursor**) and containerized HTTPS `streamable-http` deployments behind Caddy (`ADR-027`, `F21`).

> **Related docs:**
> - VPS deployment, secrets & rotation → [Operations Runbook](runbook.md)
> - CLI, architecture boundaries & tests → [Developer & CLI Guide](guides/developer-guide.md)
> - Env variables & external credentials → [Environment Variables & External Credentials Guide](guides/environment-and-credentials.md)

---

## 1. Security & Architecture Overview

Macy enforces identical domain, tenancy, spend, and approval invariants across Streamlit UI, CLI, and MCP (`ADR-014`, `ADR-027`):

- **Services Boundary:** `engine/mcp/` imports `engine.services` and `engine.contracts` only — never `engine.db` directly (enforced by `lint-imports`).
- **Fail-Closed Authentication:**
  - `streamable-http` requires `MCP_AUTH_TOKEN` (validated in constant time via `hmac.compare_digest` on `Authorization: Bearer <token>`; returns HTTP `401` on missing/invalid tokens) and a pinned `MCP_ACTOR_EMAIL` that cannot be overridden via tool arguments. `GET /healthz` stays public for Docker healthchecks.
  - `stdio` resolves caller identity from explicit tool args (`actor_email` / `actor_user_id`), `MACY_USER_EMAIL`, or `MACY_ACTOR_USER_ID`. `MACY_TEST_USER_EMAIL` is forbidden in `staging`/`production`/`prod` (`FakeIdentityForbiddenError`), and `--allow-owner-fallback` is disabled by default and rejected in non-local environments.
- **No Silent Mock Fallback:** `--mock` / `MACY_MCP_STANDALONE=1` boots an isolated in-memory `acme-demo` store (`owner@example.com`) exclusively for local development and is rejected with `MOCK_FORBIDDEN` in `staging`/`production`/`prod`. Normal DB mode fails closed if PostgreSQL is unreachable.
- **Human-in-the-Loop Approvals & Staging Guard:**
  - **Gate 1 (`macy_decide_topic`):** Only `owner` matching `campaign.created_by` may approve/reject `'scored'` topics, enforcing weekly `articles_per_week` caps.
  - **Gate 2 (`macy_decide_draft`) & Publish (`macy_publish_draft`):** Require campaign-creator `owner`; when `APP_ENV=staging`, publishing always forces `mode="draft"`.
- **Zero `evals/` Runtime Dependency:** Claim grounding in `macy_verify_claims` executes inside runtime `engine/quality/claims.py`; `evals/` stays excluded from `infra/Dockerfile`.
- **No Raw HTTP Port Exposure in Prod:** In `infra/compose.prod.yaml`, `mcp:8765` binds only on the internal Docker network behind Caddy (`/mcp*` HTTPS reverse proxy).

---

## 2. Exposed Tools, Resources & Prompts

| Surface | Name / URI | Purpose |
|---|---|---|
| Discovery Tools | `macy_list_workspaces`, `macy_get_workspace_status` | List accessible workspaces; inspect budget caps, sources, integrations, and Gate 1/2 queues |
| Research & Gate 1 Tools | `macy_ingest_research_documents`, `macy_research_topic`, `macy_score_topics`, `macy_list_topics_awaiting_approval`, `macy_decide_topic` | Ingest SSRF-validated external docs (Agent-Driven Mode), gather source-backed research, score `'scored'` candidates, and approve/reject Gate 1 topics |
| Drafting, Import & Quality Tools | `macy_get_editorial_brief`, `macy_generate_draft`, `macy_import_draft`, `macy_adapt_draft`, `macy_verify_claims`, `macy_evaluate_draft` | Retrieve/create SEO briefs, draft articles with FK-linked `draft_claims`, import external `.md` drafts (Agent-Driven Mode), adapt drafts across personas by value (`adapt_brand`), verify claims, evaluate deterministic checks + rubric |
| Gate 2 & Publish Tools | `macy_list_drafts_awaiting_approval`, `macy_decide_draft`, `macy_publish_draft` | Review evaluated drafts, record creator decision (`approved`/`declined`), and publish via `PublisherPort` |
| SEO & GEO Audit Tools | `macy_audit_url`, `macy_crawl_site` | Audit a single public URL across 7 weighted SEO/GEO categories (`A+..F`) or run a bounded BFS site crawl (`free` `20`, `paid` `50`, hard cap `200`) |
| Resources (`application/json`) | `macy://workspaces/{workspace_id}/brand`, `macy://workspaces/{workspace_id}/weekly-report`, `macy://drafts/{draft_id}/claims` | Tenant-scoped brand rules, latest weekly report, and full draft + claims + quality bundle |
| Prompt | `macy_editorial_review` | Structured Gate 2 editorial review checklist referencing brand, claims, and weekly resources |

---

## 3. Prerequisites & Startup Check

Before connecting a client locally:

```sh
# 1. Non-production offline mock sanity check (no database needed)
APP_ENV=development uv run macy mcp --mock --check

# 2. Local PostgreSQL sanity check (requires db + migrate running)
DATABASE_URL=postgres://app:app_secret@localhost:5432/macy?sslmode=disable \
  uv run macy mcp --check
```

Note on local `stdio` output hygiene: `macy mcp` automatically redirects Python `sys.stdout` prints and root logging handlers to `sys.stderr`, reserving the true `stdout` stream strictly for JSON-RPC frames.

### Environment Variable Reference

| Variable | Required In | Purpose |
|---|---|---|
| `DATABASE_URL` | `stdio` DB & HTTP | PostgreSQL DSN for runtime MCP store (`ServiceStoreAdapter`) |
| `APP_ENCRYPTION_KEY` | DB / HTTP publishing | Fernet key(s) needed when reading or publishing encrypted `workspace_integrations` (`http_webhook`, LLM/search plugins) |
| `MACY_USER_EMAIL` / `MACY_ACTOR_USER_ID` | `stdio` | Caller email or UUID bound to MCP operations (`users` row must exist with workspace membership) |
| `MCP_ENABLED`, `COMPOSE_PROFILES` | Compose staging/prod | Set `MCP_ENABLED=true` and `COMPOSE_PROFILES=mcp` to activate the `mcp` service and deploy smoke checks |
| `MCP_AUTH_TOKEN` | `streamable-http` | Bearer token (`openssl rand -hex 32`, minimum 32 chars in staging/prod; must not contain `CHANGE_ME`) |
| `MCP_ACTOR_EMAIL` | `streamable-http` | Pinned Macy user email mapped to authenticated HTTP requests (must exist in `users` with workspace membership) |
| `SAFE_HTTP_TEST_ALLOW_HOSTS` | Compose E2E only | Comma-separated hostnames explicitly allowed over bridge networks when `APP_ENV=test` (never active outside tests) |

---

## 4. Client Configurations

Replace `/path/to/macy` with the absolute checkout path on your machine.

### Client Setup A — Local `stdio` (`--mock` offline demo, dev only)

Use `--mock` (`acme-demo` seeded dataset) to test tool calls and workflows without PostgreSQL or vendor API keys. Refused if `APP_ENV` is `staging`, `production`, or `prod`.

**Antigravity (`~/.gemini/antigravity/mcp_config.json`), Claude Desktop (`claude_desktop_config.json`), & Cursor (`.cursor/mcp.json`):**

```json
{
  "mcpServers": {
    "macy-mock": {
      "command": "uv",
      "args": ["--directory", "/path/to/macy", "run", "macy", "mcp", "--mock"],
      "env": {
        "APP_ENV": "development",
        "MACY_USER_EMAIL": "owner@example.com"
      }
    }
  }
}
```

**Claude Code (`.mcp.json` or CLI):**

```sh
claude mcp add macy-mock \
  -e APP_ENV=development \
  -e MACY_USER_EMAIL=owner@example.com \
  -- uv --directory /path/to/macy run macy mcp --mock
```

### Client Setup B — Local `stdio` (PostgreSQL-backed)

Connects directly to your local Compose Postgres instance (`infra/compose.yaml` + `infra/compose.override.yaml`). Pass `--allow-owner-fallback` only in local development if you want workspace-scoped calls to fall back to the workspace owner when `MACY_USER_EMAIL` is omitted.

```json
{
  "mcpServers": {
    "macy-local": {
      "command": "uv",
      "args": ["--directory", "/path/to/macy", "run", "macy", "mcp"],
      "env": {
        "APP_ENV": "development",
        "DATABASE_URL": "postgres://app:app_secret@localhost:5432/macy?sslmode=disable",
        "MACY_USER_EMAIL": "owner@example.com",
        "APP_ENCRYPTION_KEY": "<your-local-fernet-key>"
      }
    }
  }
}
```

For Claude Code:

```sh
claude mcp add macy-local \
  -e APP_ENV=development \
  -e DATABASE_URL=postgres://app:app_secret@localhost:5432/macy?sslmode=disable \
  -e MACY_USER_EMAIL=owner@example.com \
  -- uv --directory /path/to/macy run macy mcp
```

### Client Setup C — Remote HTTPS (`streamable-http` behind Caddy)

Connects to the opt-in `mcp` Compose service proxied at `https://<DOMAIN>/mcp` (`infra/Caddyfile`). Replace `<DOMAIN>` and `<MCP_AUTH_TOKEN>` with your staging/production host and secret token.

**Antigravity (`~/.gemini/antigravity/mcp_config.json`):**

```json
{
  "mcpServers": {
    "macy": {
      "serverUrl": "https://<DOMAIN>/mcp",
      "headers": {
        "Authorization": "Bearer <MCP_AUTH_TOKEN>"
      }
    }
  }
}
```

**Claude Code (`.mcp.json`):**

```json
{
  "mcpServers": {
    "macy": {
      "type": "http",
      "url": "https://<DOMAIN>/mcp",
      "headers": {
        "Authorization": "Bearer ${MCP_AUTH_TOKEN}"
      }
    }
  }
}
```

Or register directly from the shell:

```sh
claude mcp add --transport http macy "https://<DOMAIN>/mcp" \
  --header "Authorization: Bearer ${MCP_AUTH_TOKEN}"
```

**Cursor (`.cursor/mcp.json`):**

```json
{
  "mcpServers": {
    "macy": {
      "url": "https://<DOMAIN>/mcp",
      "headers": {
        "Authorization": "Bearer <MCP_AUTH_TOKEN>"
      }
    }
  }
}
```

**Claude Desktop (`claude_desktop_config.json`):**

Configure a **Custom Connector** in **Settings → Connectors** using `https://<DOMAIN>/mcp` with Bearer token `<MCP_AUTH_TOKEN>`, or bridge via `mcp-remote` if editing `claude_desktop_config.json` directly:

```json
{
  "mcpServers": {
    "macy": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://<DOMAIN>/mcp",
        "--header",
        "Authorization: Bearer <MCP_AUTH_TOKEN>"
      ]
    }
  }
}
```

---

## 5. Verification & Troubleshooting

Quick curl check against a running HTTPS MCP deployment:

```sh
# 1. Expect HTTP 401 when Authorization header is missing
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize"}' \
  "https://<DOMAIN>/mcp"

# 2. Full automated smoke verification (401 check + initialize + macy_list_workspaces)
MCP_AUTH_TOKEN="<token>" uv run python scripts/post_deploy_smoke.py --url "https://<DOMAIN>" --check-mcp
```

| Symptom / Error Code | Cause | Resolution |
|---|---|---|
| HTTP `401 unauthorized` on `/mcp` | Missing/mismatched `Authorization: Bearer` header or placeholder `CHANGE_ME` token | Verify client sends exact `Bearer <MCP_AUTH_TOKEN>` configured in host `.env` and restart `mcp` after token rotation |
| `AUTH_REQUIRED` in HTTP/stdio calls | `MCP_ACTOR_EMAIL` / `MACY_USER_EMAIL` not found in Postgres `users` table (or unset) | Provision user + membership via `macy add-owner <workspace-slug> <email>` |
| `MOCK_FORBIDDEN` at startup | `--mock` or `MACY_MCP_STANDALONE=1` used with `APP_ENV=staging\|production\|prod` | Remove `--mock` and configure PostgreSQL (`DATABASE_URL`) |
| `PERMISSION_DENIED` on Gate 1/2 or publish | Caller overrides `MCP_ACTOR_EMAIL`, lacks workspace membership, or is not campaign-creator `owner` | Use the creator's `MCP_ACTOR_EMAIL` / `MACY_USER_EMAIL` and verify `memberships` role is `owner` |
| `INVALID_STATE` on `macy_decide_topic` or `macy_decide_draft` | Item is not in `'scored'` (Gate 1) or draft does not belong to target workspace / expected state | Run `macy_score_topics` before Gate 1, and inspect draft state via `macy_list_drafts_awaiting_approval` |
| `BUDGET_CAP_EXCEEDED` | Workspace monthly USD spend + reservation exceeds `monthly_cap_usd` | Review spend via `macy_get_workspace_status` or raise the workspace cap in the Admin UI |
| `scripts/deploy.sh` aborts in preflight | `MCP_ENABLED=true` with missing/`CHANGE_ME` `MCP_AUTH_TOKEN` or missing `MCP_ACTOR_EMAIL` | Generate token (`openssl rand -hex 32`) and set a real `MCP_ACTOR_EMAIL` in `.env` |

---

## 6. Agent-Driven Mode: External Agent Compliance Loop (`macy_ingest_research_documents` + `macy_import_draft`)

Macy supports **Agent-Driven Mode** (an external AI agent conducts search/writing and uses Macy as the Editorial Compliance & Gate 2 Pipeline):

1. **Interactive Clients (Antigravity, Claude Code, Cursor):**
   - Call `macy_ingest_research_documents(workspace_id, topic, documents)` to register SSRF-validated external sources (`documents` + `research_records`).
   - Call `macy_import_draft(workspace_id, body_md=..., format_slug="blog-short"|"blog"|"blog-long", deterministic_only=True)` (or `file_path` on `stdio` transport; `streamable-http` requires `body_md`).
   - If `verdict == "REVISION"`, inspect `findings` (e.g. `word_count`, `prohibited_phrases`, `primary_keyword_placement`), revise the Markdown, and call `macy_import_draft` with the returned `brief_id` (`revision 1..2`).
   - Call `macy_evaluate_draft(workspace_id, draft_id, deterministic_only=...)` and halt at Gate 2 (`macy_decide_draft`) for owner approval.

2. **Headless Strands Agents Runner (`scripts/compliance_agent.py`):**
   ```sh
   uv sync --extra agent
   uv run python scripts/compliance_agent.py \
     --workspace-id 01900000-0000-7000-8000-000000000101 \
     --topic "Gestao de estoque em oticas" \
     --format blog-short \
     --mock
   ```

---

## 7. URL & Site SEO/GEO Audits over MCP (`macy_audit_url` + `macy_crawl_site`)

AI clients can trigger CMS-agnostic technical SEO + GEO audits across WordPress, Webflow, headless CMSs, or custom sites directly over MCP:
- **`macy_audit_url(workspace_id, url, check_external=False)`:** Fetches the public URL via `safe_http` and scores all 7 categories (`onpage`, `performance`, `usability`, `security`, `links`, `social`, `ai`/GEO), returning the weighted `0–100` score, `A+..F` letter grade, category breakdown, and persisted audit record ID (`audit_pages`).
- **`macy_crawl_site(workspace_id, start_url, max_pages=None, plan="free")`:** Runs a bounded BFS site crawl respecting `robots.txt`, `max_pages` plan caps (`20` free, `50` paid, hard cap `200`), and multipage detectors (broken internal/external links, redirect chains, duplicate titles/meta descriptions, and orphan sitemap pages), persisting results to `site_audits` and `audit_pages`.

---

## 8. Zero-DB Stateless `stdio` MCP Mode (`--stateless`, Path B, `M7`)

Run Macy as a pure local compliance and SEO audit MCP server without PostgreSQL or Docker:

```sh
macy mcp --transport stdio --stateless --check
macy mcp --transport stdio --stateless
```

Stateless mode exposes `macy_audit_url`, `macy_crawl_site`, `macy_evaluate_draft`, `macy_import_draft`, and `macy_adapt_draft` backed by `StatelessStoreAdapter`. Capability-disabled workspace tools automatically return `-32004 CAPABILITY_DISABLED`.
