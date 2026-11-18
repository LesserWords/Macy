# Delta for research

## MODIFIED Requirements

### Requirement: Document collection and structured extraction
The system SHALL poll feeds and site searches within the run window, canonicalize URLs, honour `robots.txt`, extract dates and language, and generate structured `ResearchRecord` rows with failure isolation.

#### Scenario: Canonical URL deduplication
- **WHEN** a page is fetched during collection
- **THEN** it is stored once per canonical URL in `documents` with `fetch_status`, `content_hash`, and the detected `lang` (`pt` or `en`), and subsequent references to the same canonical URL reuse the document row

#### Scenario: Trigram near-duplicate title detection
- **WHEN** two research records have title similarity exceeding the trigram threshold within the window
- **THEN** the later record is marked duplicate with `duplicate_of` pointing to the earlier record

#### Scenario: Missing publication date sets unverified status and caps confidence
- **WHEN** no publication date can be extracted from feed metadata or article metadata
- **THEN** `date_status` is set to `unverified` and the record's confidence is capped at `"medium"`

#### Scenario: Extraction failure or paywall does not abort the run
- **WHEN** extraction fails or the page is paywalled
- **THEN** `documents.fetch_status` records `extraction_failed` or `paywalled` (or `research_records.status` records `failed` on LLM extraction failure) and the run continues processing remaining items

#### Scenario: LLM extraction produces valid ResearchRecord with schema_version
- **WHEN** the LLM extraction step runs on an extracted document
- **THEN** a valid `ResearchRecord` payload (facts, entities, summary, source_type, verification_status) is stored in `research_records` with `schema_version` and `status = 'extracted'`

#### Scenario: Robots.txt disallow prevents fetching
- **WHEN** `robots.txt` for a target domain disallows fetching a URL
- **THEN** the page is not fetched and `fetch_status` is recorded as `blocked`
