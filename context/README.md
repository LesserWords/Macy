# Local Drop-In Context Directory (`./context/`)

Drop your private or domain-specific packs and brand fixtures into this folder without modifying tracked OSS files. Everything in `context/` (except this `README.md`) is git-ignored.

## Directory Layout

```text
context/
├── packs/
│   ├── industries/
│   │   └── <your-industry-slug>/
│   │       ├── profile.yaml
│   │       ├── sources.yaml
│   │       ├── audience.md
│   │       ├── challenges.md
│   │       ├── exclusions.md
│   │       └── terminology.md
│   └── formats/                    # Optional custom formats (built-in blog* formats are inherited automatically)
│       └── <your-format-slug>/
│           ├── format.yaml
│           └── rubric.yaml
└── fixtures/
    └── seed_<your_brand>.yaml
```

## Resolution Priority

Macy resolves packs and brand fixtures across three layers (highest priority wins per slug):
1. Explicit environment overlay (`MACY_PACKS_DIR`, `MACY_FIXTURES_DIR`, or `MACY_CONTEXT_DIR`)
2. Pocket drop-in mount (`${MACY_DATA_DIR:-/data}/context`, mounted from `./context` in `infra/compose.pocket.yaml`)
3. Built-in OSS packs & fixtures (`<repo>/packs` and `<repo>/fixtures`)
