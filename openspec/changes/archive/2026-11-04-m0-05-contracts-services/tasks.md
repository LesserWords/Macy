## 1. Pydantic domain contracts and schema validation

- [x] 1.1 Write failing tests in `tests/test_contracts.py` for `CampaignConfig` (Hypothesis weights summing to 100), `BrandProfile` (prohibited phrases regex), and domain contracts with `schema_version` → verify: `uv run pytest tests/test_contracts.py` fails before models exist
- [x] 1.2 Implement `engine/contracts.py` with all 10 models and validators → verify: `uv run pytest tests/test_contracts.py` passes

## 2. Git packs loader and hash calculation

- [x] 2.1 Write failing tests in `tests/test_config.py` asserting pack loading, `pack_version` SHA-256 computation, and error on broken pack naming file and field → verify: tests fail
- [x] 2.2 Seed reference git packs (`packs/industries/general/`, `packs/formats/blog/`, `prompts/`) and implement `engine/config.py` → verify: tests pass and broken pack identifies file and field

## 3. Services skeleton and import-linter contracts

- [x] 3.1 Write failing tests in `tests/test_services.py` asserting `require_member()` raises on missing membership, and write import-linter test asserting `app`/`cli` cannot import `engine.db` → verify: tests fail
- [x] 3.2 Implement `engine/services.py` with `require_member()` and configure `pyproject.toml` import-linter contracts → verify: tests and `uv run lint-imports` pass

## 4. Seed OpenSpec capability specs from F01-F20

- [x] 4.1 Seed capability specs for features F01 through F20 in `openspec/specs/` matching feature acceptance criteria → verify: `openspec validate --all --strict --no-interactive` passes
