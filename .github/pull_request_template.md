<!-- Copy to .github/pull_request_template.md in M0-01 -->
## <PR id> — <title>

**Plan:** docs/plans/M?.md#<pr-id> · **Feature(s):** F?? · **ADR(s):** ??? · **OpenSpec change:** `<change-id>`

### What / why
<!-- 2–4 lines -->

### Steps (from plan) — tick with evidence
- [ ] Step 1 → verify: <command or observation> — result: …
- [ ] Step 2 → verify: …

### Universal checks (all required)
- [ ] OpenSpec change proposed; scenarios ↔ tests listed in `tasks.md`; `openspec validate` green
- [ ] Tests written first (red commit precedes green commit, or red output pasted below)
- [ ] `scripts/check.sh` green locally (lint, types, complexity, dead code, duplication, boundaries, tenant scoping, squawk, translations)
- [ ] diff coverage 100 % on `engine/` + `plugins/`; branch coverage ≥ 90 %
- [ ] Offline E2E green; user-facing change adds/updates its journey step
- [ ] Regression evals green (only if `prompts/` or `packs/` changed)
- [ ] Migration reviewed: `db/schema.sql` diff makes sense; squawk clean (only if `db/` changed)
- [ ] No secrets in diff; no vendor names/keys in env or Compose files
- [ ] UI strings translated in pt-BR and en-US (only if UI changed)
- [ ] Docs updated: feature status, ADR if a decision changed, runbook if ops changed
- [ ] PR ≤ ~400 changed lines excluding tests/fixtures (else split)

### Evidence
<!-- red test output, screenshots, eval results, cost of eval run -->
