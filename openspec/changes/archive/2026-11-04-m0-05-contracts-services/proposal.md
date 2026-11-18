# Proposal

## Why

Plan item **M0-05** (docs/plans/M0.md). Establish Pydantic contracts, git packs validation, services boundary, and seed OpenSpec capability specs from F01-F20. Features: F01, F03. ADRs: 009, 014, 020.

## What Changes

- Write `engine/contracts.py` with 10 core Pydantic v2 domain models (`ResearchRecord`, `Opportunity`, `EditorialBrief`, `ContentDraft`, `QualityReport`, `PublishRequest`, `PublishedContent`, `BrandProfile`, `CampaignConfig`, `KeywordData`), each carrying `schema_version`.
- Write `engine/config.py` to load and validate git packs (industry packs, format packs, prompts) and compute `pack_version` SHA-256 hash.
- Seed minimal reference git packs in `packs/industries/general/`, `packs/formats/blog/`, and `prompts/`.
- Write `engine/services.py` skeleton with `require_member()` and configure import-linter contracts in `pyproject.toml` (`app.py`/`cli.py` can only import `engine.services` and `engine.contracts`; raw SQL only in `engine.db`).
- Seed OpenSpec capability specs for F01 through F20 in `openspec/specs/`.

Out of scope: port plugins and fakes (M0-06), LLM gateway integration (M0-07), full UI screens (M1).

## Capabilities

### New Capabilities
- `contracts`: Pydantic domain contracts validation, git packs loader with hash versioning, and service layer boundary enforcement.

### Modified Capabilities
<!-- None -->

## Impact

- New: `engine/contracts.py`, `engine/config.py`, `engine/services.py`, `packs/`, `prompts/`, `tests/test_contracts.py`, `tests/test_config.py`, `tests/test_services.py`.
- Modified: `pyproject.toml`, `openspec/specs/`.
