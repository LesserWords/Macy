# Design: Editorial Brief Generation, Evidence Grounding, and Revision Versioning

## Architecture Flow
1. **Context Loading**:
   - Load approved opportunity, source `research_records` row, primary `documents` row, campaign locale/market, workspace industry pack, `blog` format pack, and active current `brand_profiles` row (`is_current = true`).
2. **Evidence Retrieval (`ADR-022`, `F12 AC3`)**:
   - Gather existing workspace evidence documents plus search enabled workspace/pack sources with role `data` or `management` matching the campaign locale.
   - Search and extract supporting statistics via configured or offline `NewsSearchPort` and `ExtractorPort`, persisting extracted pages into `documents` scoped to `workspace_id`.
3. **Keyword Snapshot Resolution (`F11`, `M3-01`)**:
   - Extract seed terms from opportunity metadata, decision notes, and evidence context.
   - Query `seo.keywords_for` against the 30-day cache and configured keyword provider (or `FakeKeywordProvider` in offline mode), falling back to `is_estimate=True` estimates when no provider is configured.
4. **Structured LLM Synthesis & Provenance Gate (`ADR-002`, `F12 AC1–AC2`)**:
   - Construct stable cached system prompt (`prompts/brief.md` + industry pack + format pack + brand profile).
   - Pass dynamic JSON input (opportunity, decision note, revision findings, keyword snapshot, and available workspace evidence documents with UUIDs) through `llm.structured_call(stage="brief", model_cls=EditorialBrief, ...)`.
   - Validate in Python that every `required_facts[].source_id` is a valid UUID referencing an existing document row inside the same `workspace_id`, raising `InvalidEvidenceReferenceError` on invalid or cross-tenant IDs.
5. **Persistence & Versioning (`F12 AC4`, `F03 AC2`)**:
   - Lock the opportunity row within a database transaction, assign `version = MAX(version) + 1`, insert into `briefs` with pinned `brand_profile_id`, `prompt_version`, `keywords` JSON snapshot, and validated `EditorialBrief` payload, and emit a `brief.created` event.
