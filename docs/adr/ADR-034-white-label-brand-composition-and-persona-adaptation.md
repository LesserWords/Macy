# ADR-034: White-Label Brand Composition & Pass-by-Value Persona Adaptation

**Status:** Accepted · **Date:** 2026-11-26 · **Deciders:** Project Maintainer
**Related:** features F01, F03, F12, F13, F14, F21, F23 · ADR-009, ADR-027, ADR-028, ADR-032

## Context
Multi-brand and SaaS organizations frequently operate a **core horizontal product** (for example, `Core ERP` for general retail and wholesale) alongside one or more **white-label vertical forks** (`Vertical ERP` tailored to a specialized industry such as specialty clinics or boutique retail) that share roughly 70% of core software and compliance capabilities while publishing to separate blogs targeting distinct buyer personas.

Two concrete architectural failure modes arise without first-class support:
1. **Alias & capability drift across brand fixtures:** When a vertical `BrandProfile` embeds parent product aliases inside free-form `positioning` or `cta_rules` prose without a canonical brand field, brief synthesis, article writing, and LLM evaluation alternate unpredictably between parent and vertical brand names—even though the target blog requires strict vertical branding and only bare-name exclusions block regressions cleanly.
2. **Duplicate setup or fragile manual rewrites across blogs:** Because Macy isolates workspaces completely (`ADR-009`), duplicating shared ERP rules across multiple brand YAML fixtures causes drift, while publishing an overlapping ERP/fiscal article on two separate blogs currently lacks a clean way to re-angle examples, persona terminology, SEO metadata (`title`, `slug`, `meta_description`), FAQ, and CTA without risking duplicate-content cannibalization or introducing runtime coupling between tenants.

At the same time, cross-blog reuse **must not create direct runtime dependence** between workspaces: deleting, pausing, or changing Brand A's workspace must never break Brand B's workspace, foreign keys must remain strictly tenant-scoped (`ADR-010`), and proprietary brand fixtures/overlays must remain strictly decoupled from the public OSS codebase (`ADR-032`).

## Options considered
| Option | Coupling | Maintenance | SEO & Brand Safety | Fit |
|---|---|---|---|---|
| **A. Decoupled Workspaces + Load-Time YAML `extends` Composition + Pass-by-Value `adapt_brand` Rewrite** (chosen) | **None at runtime** (independent PostgreSQL tenants) | Low — shared base rules edited once in YAML, materialized on seed | High — persona adaptation + canonical naming + deterministic regex wall + similarity check | **Best** — preserves `ADR-009` tenant isolation, `ADR-032` OSS overlay, and Path B `--stateless` parity |
| B. Cross-Workspace Parent/Child Foreign Keys (`workspaces.parent_workspace_id`) | High — breaches tenant isolation boundary (`ADR-009`) | Medium — schema migrations & cascading cache invalidation | Medium — accidental parent mutation risks corrupting child brand outputs | Poor |
| C. Single Workspace Publishing to Multiple Blogs | High — shared sitemap dedup blocks overlapping topics; mixed brand profiles | Low initially, breaks at scale | Poor — severe risk of brand leakage and duplicate content across domains | Poor |
| D. Pure Regex Find-and-Replace Between Brand Names | None | Very low | Poor — creates near-duplicate blog posts triggering SEO cannibalization while leaving mismatched persona examples | Poor |

## Decision
Implement **Option A** through three strictly layered, runtime-decoupled capabilities:

1. **Canonical Identity Fields on `BrandProfile` (`engine/contracts.py`):**
   - Extend `BrandProfile` (`schema_version = 1`, backward compatible) with optional metadata fields:
     - `brand_name: str | None = None` — exact canonical product/brand name expected in copy and CTAs.
     - `target_persona: str | None = None` — concise buyer persona definition for editorial framing.
     - `canonical_domain: str | None = None` — canonical blog/product hostname for prompt/CTA grounding.
2. **Acyclic Load-Time YAML `extends` Fixture Composition (`engine/config.py`):**
   - Support an optional `extends` key in brand fixture YAML files (e.g. `extends: _base_core_erp.yaml`) resolved across the existing 3-layer cascading fixtures root (`ADR-032`), plus additive/subtractive merge directives (`capabilities_add`, `capabilities_remove`, `prohibited_phrases_add`, `prohibited_claims_add`).
   - Enforce deterministic cycle detection and non-existent parent errors (`PackValidationError` / `ValueError`), strip composition keys before `BrandProfile.model_validate()`, and materialize the merged dictionary into the target workspace's own `brand_profiles` row at `seed-brand` / import time. Zero inheritance links exist in PostgreSQL.
3. **Canonical Brand Naming Prompts (`prompts/brief.md`, `prompts/write_blog.md`, `prompts/evaluate.md`):**
   - Instruct brief synthesis, writing, and isolated rubric evaluation to treat `brand_profile.brand_name` (when set) as the canonical product name and treat substitution of parent or sibling white-label names as a `brand_voice_alignment` defect.
4. **Pass-by-Value Persona Adaptation Mode (`engine/production/rewrite.py`, `prompts/rewrite_draft.md`):**
   - Add `mode="adapt_brand"` alongside existing `"improve"`, `"simplify"`, and `"seo_optimize"` modes in `rewrite_draft()`.
   - `adapt_brand` accepts a source `ContentDraft` (or imported Markdown file) by value plus the target `BrandProfile` and `IndustryPack`, transforming angle, title, slug, meta description, persona examples, product mentions, FAQ, and CTA for the target vertical while preserving empirical facts.
5. **Tenant-Safe Provenance Re-Binding & Anti-Cannibalization Guard (`engine/quality/checks.py`, `engine/services.py`):**
   - Expose `adapt_draft_for_workspace()` in `engine/services.py`: takes an input draft by value, re-ingests/matches any cited external public URLs in the **target** workspace's own `documents` table (remapping `claims[].source_id` to target-workspace document UUIDs or clearing missing links for re-verification—never referencing the source workspace's rows), runs `rewrite_draft(mode="adapt_brand")`, evaluates compliance, and stages the result at Gate 2 (`awaiting_approval`) in the target workspace.
   - Add deterministic `check_adaptation_uniqueness(source_draft, adapted_draft, max_similarity=0.65)` to `engine/quality/checks.py` requiring distinct `slug`/`title` and flagging excessive textual overlap across domains.
   - Document symmetric `prohibited_phrases` regex walls (e.g., `\bCore ERP\b` inside a `Vertical ERP` fixture passes `"Vertical ERP"` while deterministically blocking bare `"Core ERP"`).
6. **Multi-Mode Surface Parity (`engine/cli.py`, `engine/mcp/`, `app.py`):**
   - Expose `macy adapt-draft` in CLI (including `--stateless` Path B support), `macy_adapt_draft` in the MCP server (with `capability="editorial-pipeline"` gating), and an **"Adapt for Another Workspace"** action in the Admin UI Draft Review screen (enforcing creator/owner membership on both read and write workspaces through `services.py`).

## Consequences
- **Easier:** Multi-brand operators maintain core business-rule capabilities once in YAML base fixtures, eliminate cross-brand name leaks deterministically for `$0`, and adapt high-performing posts across sibling blogs in one command for roughly half the token cost of a full from-scratch pipeline (`~$0.13` vs. `~$0.18–$0.35`, or `$0.00` offline/stateless/`local_ollama`).
- **Harder:** Seeded workspaces must re-run `seed-brand` when base YAML templates change (explicitly desired so running campaigns are never silently mutated mid-flight); adapting a post re-ingests public cited URLs in the target workspace rather than sharing a single document row.

## Revisit when
- Cross-workspace adaptation volume exceeds ~50 articles/week per installation and justifies an asynchronous batch queue or automated translation across locales (`pt-BR` ↔ `en-US`) inside `adapt_brand`.

## Action items
- [ ] `M11-01` — `BrandProfile` identity fields, acyclic YAML `extends` fixture composition, and canonical naming prompts (`docs/plans/M11.md#m11-01`).
- [ ] `M11-02` — `adapt_brand` rewrite mode, anti-cannibalization deterministic check, and tenant-safe `services.adapt_draft_for_workspace()` (`docs/plans/M11.md#m11-02`).
- [ ] `M11-03` — Multi-mode CLI (`adapt-draft` + `--stateless`), MCP (`macy_adapt_draft`), Admin UI action, offline E2E, and documentation sweep (`docs/plans/M11.md#m11-03`).
