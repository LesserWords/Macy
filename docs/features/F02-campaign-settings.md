# F02 — Campaign settings (locale, volume, schedule, strategy, spend caps)

**Status:** Done · **Milestone:** M1 · **Plan items:** M1-03 · **ADRs:** 004, 015, 016 · **OpenSpec capability:** `campaigns`

## User story
As an owner, I choose the language and market, how many articles per week, when the run happens, the scoring strategy and the spend limits, all without a developer.

## Scope
- In: create and edit a campaign; validation; estimated monthly cost shown before saving.
- Out: several active campaigns per locale (allowed by the schema, but not exposed in the UI yet), scheduled future changes.

## Acceptance criteria
- **AC1** WHEN the owner saves locale `pt-BR`, market `BR`, articles/week 1–20, a valid cron schedule and caps where run ≤ monthly, THEN the campaign is saved and a `settings.changed` event is written with the before and after values.
- **AC2** WHEN the locale is not supported by an active plugin, THEN saving fails and the message names the plugin.
- **AC3** WHEN a spend cap is missing, ≤ 0, or the run cap is greater than the monthly cap, THEN saving fails.
- **AC4** WHEN scoring weights are edited, THEN they must sum to 100; the defaults come from `CampaignConfig`.
- **AC5** WHEN settings change, THEN the next run uses the new values and a run already in progress is unaffected (it uses a snapshot).
- **AC6** WHEN the owner changes the volume, THEN the estimated monthly cost updates before saving.

## Data
`campaigns` (locale, market, `articles_per_week`, `schedule_cron`, caps, config, `created_by`)

## Interfaces
- `services.save_campaign()`
- `CampaignConfig` contract
- `UnsupportedLocale` raised by plugins

## Tests
| Seam | Test | E2E |
|---|---|---|
| contracts | `CampaignConfig` validation (Hypothesis: weights, caps) | — |
| services | Save, validate, audit event, snapshot semantics | — |
| UI | Set pt-BR, then en-US, volume 1 and caps | **J1** |

## Dependencies
F01, M0-06 (plugin registry, for the locale check)
