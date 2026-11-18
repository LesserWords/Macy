# Design: M4-01 — Writer and claim provenance

## Context
`F13` requires generating a structured `ContentDraft` from an `EditorialBrief` while enforcing claim provenance at both the Pydantic contract seam (`DraftClaim`) and the PostgreSQL relational seam (`draft_claims`). Up to two revision rounds (`revision` 0, 1, 2) are permitted per brief version (`CHECK (revision BETWEEN 0 AND 2)`).

## Architecture & Data Flow
1. **Prompt & Stable Caching Prefix (`ADR-002`):**
   - `get_write_prompt_and_version()` loads `prompts/write_blog.md` and computes `write_blog.md:<sha256[:16]>`.
   - `build_write_system_prompt()` combines `prompts/write_blog.md`, the pinned `BrandProfile` (`brand_profile_id` from the brief), the workspace `IndustryPack`, and the `blog` `FormatPack` (`min_words: 800`, `max_words: 2500`, `structure: [title, meta, slug, body, faq, cta]`) into the cached system message.
2. **Structured Call & Usage Ledger (`F13 AC1, AC5`):**
   - `llm.structured_call(stage="write", model_cls=ContentDraft, reasoning_effort="high", prompt_version=...)` executes the completion and writes a `usage_ledger` row with `stage="write"` and `prompt_version`, plus the request/response in `llm_payloads`.
3. **Database Provenance & Revision Invariants (`F13 AC2, AC3`, `ADR-010`):**
   - `db.create_next_draft_revision()` locks the parent `briefs` row (`FOR UPDATE`), computes `COALESCE(MAX(revision), -1) + 1`, inserts the `drafts` row, and inserts all `draft_claims` rows (`idx = 0..N-1`) within the same transaction.
   - If a `fact` claim lacks a `document_id`, PostgreSQL rejects it with `CheckViolation` (`SQLSTATE 23514`).
   - If a claim references a non-existent or cross-tenant `document_id`, PostgreSQL rejects it with `ForeignKeyViolation` (`SQLSTATE 23503`).
   - If a fourth attempt (`revision = 3`) is written for the same `brief_id`, PostgreSQL rejects it with `CheckViolation` (`SQLSTATE 23514`).
