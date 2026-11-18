# Design

## Context

See `proposal.md`. Brief generation (`engine/strategy/brief.py`, F12) looks up statistics from `role: data` and `role: management` sources (`ibge.gov.br`, `thevisioncouncil.org`, `euromonitor.com`, `cielo.com.br`, `sebrae.com.br`). Because `trafilatura` only parses HTML/XML and returns `None` on binary `application/pdf` responses, M3-03 evaluates an isolated `docling` Extractor plugin and optional `docling-serve-cpu` Compose service.

## Goals / Non-Goals

**Goals:**
- Implement a strict `ExtractorPort`-compliant `DoclingExtractor` (`spikes/trials/docling_plugin.py`) handling `application/pdf` only and rejecting non-PDF content (`html` parameter or `text/html` HTTP Content-Type) cleanly.
- Empirically measure `quay.io/docling-project/docling-serve-cpu:latest` against 3 optical/retail PDF fixtures (IBGE real August 2026 PMC excerpt pages 1/28/45, The Vision Council *Market inSights* summary, Euromonitor *World Market for Eyewear* summary): table cell accuracy, CPU latency, disk image size, idle RAM, and peak RSS RAM.
- Audit live seed `data` sources (`packs/industries/general/sources.yaml`) for un-gated open-web PDF availability and evaluate the `TECH-RADAR.md` `>= 3 briefs/month cite PDF data` threshold plus `ADR-012` single-VPS (`2 vCPU / 4 GB RAM / 40 GB disk`) headroom.
- Execute the M3-03 keep/drop gate cleanly: when live source count (`1/3 < 3`), monthly PDF utilization (`< 3 briefs/month`), and VPS footprint (`7.05 GiB` unpacked disk, `1.60 GiB` RAM) fail MVP criteria, remove the Compose profile and exclude from production entry points while archiving durable trial evidence in `spikes/trials/` and `tests/test_spike_trials.py`.

**Non-Goals:**
- Introducing PyTorch, HuggingFace models, or OCR dependencies into `pyproject.toml` runtime dependencies or `Dockerfile`.
- Retaining a heavy ML Compose container when < 3 live data sources publish open statistical PDFs.

## Decisions

### Decision 1: `application/pdf`-only `DoclingExtractor` with `docling-serve` REST API
- **Context:** Docling's ML dependencies (>2 GB Python wheels + TableFormer/RapidOCR weights) violate the lightweight `Dockerfile` baseline. Running `docling-serve-cpu` as an isolated container via HTTP (`/v1/convert/file`) preserves port cleanly behind `ExtractorPort` with `Settings` JSON Schema support.
- **Guardrails:** `DoclingExtractor.extract()` fetches target URLs through `safe_http.safe_request` (SSRF + size-cap enforcement), verifies `application/pdf` header / `%PDF-` signature, rejects HTML inputs cleanly with `fetch_status="unsupported_content_type"`, converts table grids + markdown output into structured numeric facts (`facts[]`) and `ResearchRecord.raw["tables"]`.
- **Internal service safety note:** On Docker networks where the optional container would be addressed as `http://docling:5001`, production `safe_http` blocks private bridge IPs and non-HTTPS URLs, requiring explicit opt-in (`allow_private_service_url`) if ever deployed on-box.

### Decision 2: Empirical Trial Benchmark & Verdict -> Drop from MVP Runtime
- **Measured Extraction Accuracy (3 PDF Fixtures, `docling-serve-cpu:latest`, `DOCLING_NUM_THREADS=2`):**
  1. **IBGE PMC August 2026 Excerpt (real `pmc_202608caderno.pdf` pages 1, 28, 45 — 66 KB):** `trafilatura` returned `None`. Docling extracted `Tabela 1` (Volume de Vendas, 18×13) and `Tabela 6` (Receita Nominal, 18×13) with multi-level merged Portuguese headers and comma decimals (including row `5. Artigos farmacêuticos, médicos, ortopédicos, de perfumaria e cosméticos`: `+0,7%` MoM / `+4,8%` YoY / `+5,3%` 12m volume; `+1,1%` MoM / `+9,5%` YoY / `+10,4%` 12m nominal revenue) in **34.07s** at **100% cell accuracy**.
  2. **The Vision Council (*Market inSights* public summary PDF — 2.7 KB):** `trafilatura` returned `None`. Docling extracted `7×5` category table (`$69.5B`, `+4.4%`, `312.8M` plano sunglasses, etc.) in **6.04s** at **100% cell accuracy**.
  3. **Euromonitor (*World Market for Eyewear* public summary PDF — 2.6 KB):** `trafilatura` returned `None`. Docling extracted `6×5` global/LATAM table (`$168.4B`, `68.5%`, LATAM `+6.8%`) in **4.05s** at **100% cell accuracy**.
- **Measured Operational & Seed-Source Reality (Why Verdict = DROP):**
  1. **Seed Data Source Availability (`1 / 3` open PDF sources):** Live HTTP probes confirm **IBGE** (`ftp.ibge.gov.br`, `pt-BR` only) serves un-gated `application/pdf` reports (`200 OK`); **The Vision Council** serves `200 text/html` summary pages (`2,084` chars extracted cleanly by `trafilatura`) with `0` public `.pdf` links (full reports behind member login); **Euromonitor** returns **`HTTP 403 Forbidden`** and gates full PDFs behind paid Passport subscriptions while `sources.yaml` explicitly restricts to public HTML summaries; **ICVA/Cielo** serves `200 text/html` releases; **Sebrae** serves `200 text/html` articles.
  2. **Monthly Utilization (`TECH-RADAR.md` criterion `>= 3 briefs/month`):** At MVP pilot cadence (`1–2` articles/week), only **1** `pt-BR` data source (and **0** `en-US` sources) publishes open statistical PDFs, falling below both `>= 3` live seed PDF data sources (`F12 AC5` / `MVP-PRD §10`) and `>= 3 briefs/month` citing PDF-only data.
  3. **Single-VPS Resource Envelope (`ADR-012`: `2 vCPU, 4 GB RAM, 40 GB disk`):** `quay.io/docling-project/docling-serve-cpu:latest` measured **2.07 GiB** compressed (**7.05 GiB** unpacked virtual size on disk), **19.35s** cold start, **820.8 MiB** idle RAM, and **1.595 GiB** active RSS after converting three 1–3 page PDFs (~40% of total VPS RAM alongside `db`, `app`, `worker`, `caddy`, `backup`, and `staging`).
- **Outcome:** Record **DROP for MVP** in `TECH-RADAR.md` and `ADR-003`. Remove the `docling` service/profile from Compose (verified empty in `compose.yaml`), exclude `docling` from production `content_engine.extractor` entry points in `pyproject.toml`, preserve reproducible trial code & fixtures in `spikes/trials/` + `tests/test_spike_trials.py`, and define explicit revisit criteria.

## Risks / Trade-offs

- [Risk: Occasional valuable IBGE PDF tables cannot be extracted automatically by `trafilatura`] → Mitigation: IBGE already publishes HTML press releases (`agenciadenoticias.ibge.gov.br`) and SIDRA endpoints readable by `trafilatura`, covering monthly PMC headlines without ML inference on the 4 GB VPS.
- [Risk: Future expansion adds multiple public PDF statistical portals] → Mitigation: Working `DoclingExtractor` plugin, Composer profile spec, and comparison harness remain preserved in `spikes/trials/` with a clear revisit trigger (`>= 3` open PDF data sources cited in `>= 3` briefs/month AND `>= 8 GB` RAM or external API).
