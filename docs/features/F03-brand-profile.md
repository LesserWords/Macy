# F03 — Brand profile

**Status:** Implemented (M1-04 & M3-02 done: AC1–AC4 delivered) · **Milestone:** M1 · **Plan items:** M1-04 · **ADRs:** 009, 010 · **OpenSpec capability:** `brand`

## User story
As an owner, I maintain my brand's voice, capabilities, positioning, prohibited claims and CTA rules, so drafts sound like us and never make claims we can't back.

## Scope
- In:
  - `BrandProfile` contract;
  - edit screen;
  - versioning (a new version on every save, one current version);
  - Acme Demo seed from `fixtures/seed_example.yaml` and product catalog fixtures (see [Brand Fixtures Guide](../guides/brand-fixtures-guide.md)).
- Out: multiple brands per workspace, approval of brand changes.

## Acceptance criteria
- **AC1** WHEN the owner saves the brand, THEN a new `brand_profiles` row with `version+1` becomes current and the previous one stays readable.
- **AC2** WHEN a brief is created, THEN it records the exact `brand_profile_id` it used.
- **AC3** WHEN the brand profile fails `BrandProfile` validation (e.g. an invalid regex in prohibited phrases), THEN saving fails and names the field.
- **AC4** WHEN the seed command runs for a new workspace, THEN version 1 is created from the fixture.

## Data
`brand_profiles` (version, `is_current`, payload, `schema_version`)

## Interfaces
- `services.save_brand()`
- `services.current_brand()`
- `BrandProfile` contract

## Tests
| Seam | Test | E2E |
|---|---|---|
| contracts | `BrandProfile` validation (regex compile, CTA rules) | — |
| services | Versioning; only one current version (partial unique index) | — |
| UI | Edit and save the brand | part of **J1** |

## Dependencies
F01
