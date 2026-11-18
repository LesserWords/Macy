# Design: Jev-Verified Claim Judge

## Context

`evals/claim_benchmark/harness.py` runs methods over labeled variants and scores catch, false pass, clean rejection and false alarms. All new code stays there. ADR-026 records why, including why Laya is deferred.

## Decisions

### 1. Freeze before measuring

The existing 80 articles become the **development set**, because they have already informed the design. Threshold constants, win rules and dataset hashes are committed before any new run. The holdout is opened only for the final 3 runs.

### 2. Decisions client

- One constant: `DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"`. The OpenAI SDK cannot call it, so use a direct `httpx2` POST with bearer auth.
- Body: `model: "typesafe/jev-1.13"`, `state`, `questions`, `provider: {"data_collection": "deny"}`.
- Parse `answers[name]` by `type`: `choice` with `confidence` and `probabilities`, `noul`, and `refusal`. A missing confidence counts as 0, as OpenRouter's cookbook does.
- Record `usage.cost` and the resolved `model` from each response.
- A `refusal` answer or a missing claim key sends only that claim to Haiku. HTTP errors count as a failed article.

### 3. Question shape

One request per source. `state` holds `source_text`; each claim is one question, named `claim_<n>`, so the source tokens are paid once. Questions in one request cannot see each other's answers, which suits independent claims.

```json
{
  "type": "choice",
  "instructions": "Compare this claim with source_text: <claim>",
  "criteria": {
    "supported": "source_text states the claim, including every number, date, name, role, direction, scope and cause.",
    "contradicted": "source_text states something incompatible with the claim.",
    "unverifiable": "source_text does not state the claim."
  }
}
```

If UTF-8 text in `state` would exceed 30,000 bytes, split at paragraph boundaries and take the most favorable label only when a chunk says `supported`; otherwise `unverifiable`.

### 4. Cascade

1. Haiku extracts claims, using the v2 extraction prompt.
2. Jev judges all claims.
3. Accept a claim as supported only if Jev says `supported` at confidence at or above the threshold **and** every number in it appears in the source.
4. Everything else goes to Haiku's v2 judge with the existing verbatim-quote guard.
5. Publish only if all claims end as supported.

The threshold is the lowest value in {0.70, 0.75, 0.80, 0.85, 0.90, 0.95} reaching 97% precision on the development set. OpenRouter's cookbook starting value is 0.8.

### 5. Evidence passage

Haiku-judged claims use their verified quote. Claims accepted by Jev get the source sentence with the highest token overlap, ties broken by shared numbers, computed in code at no cost. Audited for KR W7.

### 6. Prompts

`plain_tuned` is one prompt, given a fair chance: strict support rule, explicit "not checkable" handling of advice and opinion, attributed predictions treated as checkable, exact quotes required. `per_claim_v2` uses the same concepts split across extraction and judging. Concepts come from public fact-checking skills; text is our own. Both are tuned on development data only, with at most 3 prompt revisions each, each recorded in the commit history.

### 7. Holdout

Stored under `evals/claim_benchmark/holdout/`, gitignored for copyright. Only its SHA-256, counts and generation notes are committed. Errors are planted with the existing format, plus multiple-error variants and multiple sources. A person reviews every label.

### 8. Reports

Extend the run JSON with the fields required by D5. Jev's reliability table uses bins of width 0.1 against claim-level labels. A small `evaluate-kr` command reads 3 holdout reports and prints each KR as PASS or FAIL with the resulting decision, so the outcome is computed, not argued.

## Testing Seams

- Decisions request and parsing, at the HTTP boundary with a mock transport.
- Cascade routing, using a scripted decision client and scripted LLM.
- Evidence-passage selection.
- KR evaluation, on hand-worked report fixtures.
- Threshold selection, on hand-worked labels.

These seams need the owner's confirmation before tests are written. Live model output is not unit-tested.

## Alternatives

Noul per claim loses the supported/contradicted distinction. One request per claim pays for the source repeatedly. Jev 1.14 has the same price but is excluded so that one pinned model is measured. See ADR-026 for Laya and other options.
