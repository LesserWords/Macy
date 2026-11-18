# quality Delta Specification

## ADDED Requirements

### Requirement: Runtime claim verification decoupled from benchmark harness
The runtime package `engine/quality/claims.py` SHALL verify factual claims against source text using deterministic numeric/quote guards and configured runtime `LLMPort` / `safe_http.safe_request` adapters (`ADR-026`) without importing `evals/` (`ADR-012`, `ADR-027`).

#### Scenario: Claim verification runs cleanly without evals directory
- **WHEN** `services.verify_claims` executes inside a minimal runtime container lacking `evals/` or `httpx2`
- **THEN** `engine/quality/claims.py` performs deterministic number/quote checks and runtime judge calls cleanly, returning structured verdicts (`supported`, `refuted`, `unverifiable`), confidence scores, and evidence quotes while logging usage
