# Design

## Context

See `proposal.md` for motivation and background. Macy has 18 optical retail seed sources defined in PRD §5.8 and seeded from `packs/industries/general/sources.yaml`. Milestone 1 requires a verification run over 30 days (`cli verify-sources`), explanatory resolution of dormant sources (Fenóptica, Fique de Olho), and demonstration that the M1 exit gate holds.

## Goals / Non-Goals

**Goals:**
- Implement `services.verify_sources()` and `cli.py verify-sources --days 30` that executes test fetches against all seed sources.
- Support both `feed` mode (via `feed_source` port) and `site_search` mode (via `news_search` port).
- Output a clear summary report per source reporting records found, dated %, and fetch status.
- Update `sources.yaml` with explicit notes on dormant event sources (`fenoptica.com.br`) and active trend portals (`fiquedeolho.com.br`).
- Run a 14-day research run for `pt-BR` and `en-US` campaigns and record manual inspection of 20 records.
- Document M1 exit gate evidence in `docs/plans/M1.md`.

**Non-Goals:**
- Schema changes or adding new database tables.
- Replacing Brave Search API for site search.

## Decisions

### Decision 1: `verify_sources` tests connectivity without dirtying production run history
- **Context:** The verification CLI evaluates source health and record quality across feed parsing and domain search.
- **Approach:** `verify_sources()` calls the appropriate plugin directly (using `RSSFeedSource` or `FakeFeedSource` for feeds and `BraveNewsSearch` or `FakeNewsSearch` for site search), computing stats in memory and returning a structured `SourceVerificationResult`. It can be run independently of pipeline runs.

### Decision 2: Explicitly annotate and disable dormant biennial trade fair sources
- **Context:** Fenóptica is a biennial trade fair in Fortaleza that publishes news only in the months preceding each edition; outside this event cycle, domain searches yield zero dated articles. Fique de Olho is an active trade fashion blog.
- **Approach:** Annotate Fenóptica in `sources.yaml` with `enabled: false` and explanatory note documenting its biennial cycle. Annotate Fique de Olho verifying its fashion/trends coverage. This satisfies F04 AC6 ("no seed source left unexplained").

## Risks / Trade-offs

- [Risk: Rate limiting during 30-day verification of 16 site_search sources] → Mitigation: verification runs use appropriate sleep/throttling between calls, or use recorded test fixtures in automated test suites.
- [Risk: Missing dates on corporate PR pages] → Mitigation: date status resolver inspects URLs and JSON-LD metadata, classifying records as `verified` or `unverified`.
