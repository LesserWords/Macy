# ADR-019: Git + GitHub + GitHub Actions; small PRs on a protected main branch

**Status:** Accepted · **Date:** 2026-11-03 · **Deciders:** Product owner
**Related:** ADR-006, ADR-018, ADR-020 · [plans/README.md](../plans/README.md)

## Context
You want Git with GitHub CI. Delivery should be predictable, with every change verified before it merges.

## Options considered
| Option | Fit |
|---|---|
| **A. Trunk-based: short-lived branches, 1 plan item = 1 PR, protected `main`, required checks** | Small, reviewable, always deployable |
| B. Long-lived feature branches / GitFlow | Painful merges; late integration |

## Decision
A.
- **Branch protection on `main`:**
  - required checks: `check` (all gates), `test`, `e2e-offline`, `openspec-validate`, `compose-config`, `squawk`;
  - linear history; squash merge.
- **PRs:**
  - Branch name: `m<milestone>-<nn>-<slug>`.
  - Commit subjects: Conventional Commits.
  - The PR template carries the plan item's checks (see [templates/pr-plan.md](../templates/pr-plan.md)).
  - Size: ≤ ~400 changed lines excluding tests and fixtures; split if larger.
- **Workflows:**
  - `ci.yml` runs on every PR.
  - `e2e-live.yml` runs nightly, on `workflow_dispatch`, and before a release tag.
  - `deploy.yml` deploys to staging on merge to `main` from M2, and to prod on a `v*` tag from M5.
- **Dependencies:** Dependabot for Python, Docker and Actions.
- **Secrets:** never committed. A secret-scanning pre-commit hook (gitleaks) blocks them.

## Consequences
- Easier: continuous integration, and every merge is deployable to staging.
- Harder: discipline about PR size, and feature flags are needed for screens that are partly built.

## Revisit when
More developers join: add CODEOWNERS and required reviewers.

## Action items
- [x] M0-01: repo, branch protection, PR template, `ci.yml`, gitleaks
- [x] M2-06: `deploy.yml` (staging)
- [x] M5-01: release tagging + prod deploy (`v*` tag workflow + post-deploy smoke & rollback implemented; live VPS activation awaits `PROD_DEPLOY_ENABLED=true` & host secrets)
