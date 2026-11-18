# Tasks: Editorial Brief Generation & Versioning (M3-02)

- [x] Add `briefs` migration (`db/migrations/20261109000000_create_briefs.sql`) with squawk validation and apply via `dbmate`
- [x] Author `prompts/brief.md` with deterministic hash versioning
- [x] Add regression cases 6–7 (`evals/cases/case_06.yaml`, `case_07.yaml`) and brief regression assertions in `evals/test_regression.py` (failing red on stub first)
- [x] Add tenant-scoped brief and evidence document queries in `engine/db.py`
- [x] Update `engine/llm.py` and `engine/integrations/fakes.py` so structured non-OpenRouter and fake completions receive serialized input and produce evidence-grounded `EditorialBrief` payloads
- [x] Implement `engine/strategy/brief.py` (`strategy.brief`) with `data`/`management` evidence lookup, `seo.keywords_for` snapshot, current brand version pinning (`F03 AC2`), `EditorialBrief` validation, and strict `document_id` rejection (`F12 AC1–AC3`)
- [x] Add `generate_brief`, `revise_brief` (`REVISION -> brief` creating `version + 1`), `get_latest_brief`, and `list_briefs` in `engine/services.py`
- [x] Update `app.py`, `locales/{pt_BR,en_US}/LC_MESSAGES/messages.*`, `tests/test_journey_j4_topic_approval.py`, and `tests/test_live_e2e_j0_j4.py` to generate and render briefs with keyword volumes or estimate labels
- [x] Verify all local quality gates (`ruff`, `mypy`, `complexipy`, `vulture`, `lint-imports`, `jscpd`, `squawk`, `check_translations.py`, `pytest`, `diff-cover --fail-under=100`, `openspec validate --all --strict`)
