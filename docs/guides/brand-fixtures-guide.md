# Brand Fixtures & Private Context Overlay Guide

This guide explains how Macy brand fixtures (`seed_*.yaml`) and vertical industry packs work, and how to separate private domain data from the open-source core.

## Overview

Macy ships clean, domain-neutral starter files in the public repository:

- **Starter Brand Fixture:** [`fixtures/seed_example.yaml`](../../fixtures/seed_example.yaml) (`acme-demo`)
- **Starter Industry Packs:** [`packs/industries/general/`](../../packs/industries/general/) (default) and [`packs/industries/b2b-saas/`](../../packs/industries/b2b-saas/)

To run Macy with proprietary brand positioning, customer personas, domain guardrails, or vertical-specific sources without committing them to Git, place your private files in the git-ignored `./context/` folder (or pass `MACY_CONTEXT_DIR` / `MACY_PACKS_DIR` / `MACY_FIXTURES_DIR`).

## 3-Layer Overlay Precedence

At runtime, Macy resolves industry packs and brand fixtures from three locations in priority order (merged by slug/filename, highest priority wins):

1. **Explicit Environment Path:** `MACY_PACKS_DIR`, `MACY_FIXTURES_DIR`, or `MACY_CONTEXT_DIR/{packs,fixtures}`
2. **Docker Pocket Overlay:** `${MACY_DATA_DIR:-/data}/context/{packs,fixtures}` (mounted from host `./context:/data/context:ro` in `infra/compose.pocket.yaml`)
3. **Built-in OSS Repository Assets:** `<repo>/packs` and `<repo>/fixtures`

```
context/
├── packs/
│   └── industries/
│       └── my-vertical/
│           ├── pack.yaml
│           ├── scoring.yaml
│           ├── briefing.yaml
│           ├── writing.yaml
│           ├── evaluation.yaml
│           └── sources.yaml
└── fixtures/
    └── seed_my_brand.yaml
```

Format packs (`packs/formats/blog/format.yaml`) remain in the built-in `packs/` tree and are inherited automatically when your overlay only defines `packs/industries/<slug>/`.

## Brand Fixture Schema (`seed_<slug>.yaml`)

All brand fixture files loaded via `seed-brand`, the Setup Wizard, or the Admin YAML Import share this schema:

| Field | Required | Description |
| :--- | :---: | :--- |
| `schema_version` | Yes | Schema version (`1`) |
| `brand_name` | No | Exact canonical product/brand name expected across briefs, drafts, CTAs, and evaluations (`ADR-034`) |
| `target_persona` | No | Target buyer persona summary for editorial framing and adaptation |
| `canonical_domain` | No | Canonical product/blog hostname (e.g., `vertical.example.com`) |
| `voice` | Yes | Narrative voice, tone, and stylistic guidance |
| `positioning` | Yes | Core value proposition and market differentiation |
| `capabilities` | Yes | Verified product capabilities (the only features drafts may claim) |
| `prohibited_claims` | Yes | Forbidden promises or unsupported guarantees |
| `prohibited_phrases` | Yes | Valid Python regex patterns enforced as `critical` deterministic gates |
| `cta_rules` | Yes | Funnel-stage CTA guidance (`TOFU:`, `MOFU:`, `BOFU:`) |

Refer to [`fixtures/seed_example.yaml`](../../fixtures/seed_example.yaml) as a starting template.

### White-Label Composition (`extends`) & Symmetric Brand Walls (`ADR-034`)

When managing a core horizontal product alongside vertical white-label forks that share core capabilities, extract shared rules into a base fixture (e.g., `_base_core_erp.yaml`) and compose child fixtures via load-time `extends`:

```yaml
extends: _base_core_erp.yaml
brand_name: "Vertical Specialty ERP"
target_persona: "Specialty clinic & lab managers"
canonical_domain: "vertical.example.com"
positioning: "Vertical Specialty ERP built for specialty clinics."
capabilities_remove:
  - "Generic wholesale invoicing"
capabilities_add:
  - "Specialty lab work-order tracking"
prohibited_phrases_add:
  - "\\bCore ERP\\b"
```

- `extends` is resolved **only at load/seed time** (`engine/config.py:load_brand_fixture_dict`) across the 3-layer fixture roots and materialized into each workspace's independent `brand_profiles` snapshot (`ADR-009` zero cross-tenant coupling).
- Word-boundary regexes like `\bCore ERP\b` block bare parent mentions while cleanly allowing longer vertical names such as `Vertical Specialty ERP` or `Core Specialty ERP`.

## Loading & Exporting Fixtures

### Via CLI

```bash
# Bootstrap workspace using a private overlay fixture and pack
uv run macy create-workspace my-brand --industry my-vertical --language pt-BR
uv run macy seed-brand my-brand --fixture context/fixtures/seed_my_brand.yaml
uv run macy seed-sources my-brand --industry my-vertical
```

Or configure defaults via environment variables:

```bash
export MACY_CONTEXT_DIR=./context
export MACY_DEFAULT_INDUSTRY_PACK=my-vertical
export MACY_USER_AGENT="MacyContentEngine/1.0 (+https://example.org)"
```

### Via Web UI (Admin Import / Export & Setup Wizard)

1. Open `/workspace/<slug>/admin` and locate the **Brand YAML Import / Export** panel.
2. Export the current workspace fixture to back it up or edit offline, or upload a `.yaml` / `.yml` fixture to apply it to the workspace.
3. On workspace creation or seed/source seeding, Macy persists an immutable `industry_pack_snapshot` inside `workspaces.config`. Subsequent scoring, briefing, writing, and quality-evaluation jobs use the DB snapshot if overlay files are later absent or unmounted.

## Related Documentation

- [Developer Guide](developer-guide.md)
- [Operations Runbook](../runbook.md)
- [ADR-032: OSS Context Overlay & Public Readiness](../adr/ADR-032-oss-context-overlay-and-public-readiness.md)
- [Feature F23: OSS Public Readiness & Context Overlay](../features/F23-oss-public-readiness-and-context-overlay.md)
