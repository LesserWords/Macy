--
-- PostgreSQL database dump
--

\restrict W7RRb52dleszyN8liZ4WRKODUBJ6VUV54Sr4NUZormc1O67gU85Qe35Q6CDAt4Y

-- Dumped from database version 18.6
-- Dumped by pg_dump version 18.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner:
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: anonymise_user_events(uuid); Type: FUNCTION; Schema: public; Owner: owner
--

CREATE FUNCTION public.anonymise_user_events(target_user_id uuid) RETURNS bigint
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
DECLARE
  updated_rows bigint;
BEGIN
  UPDATE events
  SET payload = payload - ARRAY['email', 'name', 'user_email', 'user_name']
  WHERE (actor_user_id = target_user_id OR entity_id = target_user_id)
    AND (payload ?| ARRAY['email', 'name', 'user_email', 'user_name']);
  GET DIAGNOSTICS updated_rows = ROW_COUNT;
  RETURN updated_rows;
END;
$$;


ALTER FUNCTION public.anonymise_user_events(target_user_id uuid) OWNER TO owner;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: audit_pages; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.audit_pages (
    workspace_id uuid NOT NULL,
    id uuid DEFAULT uuidv7() NOT NULL,
    site_audit_id uuid,
    url text NOT NULL,
    status_code bigint NOT NULL,
    overall_score numeric(5,2) NOT NULL,
    grade text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT audit_pages_overall_score_check CHECK (((overall_score >= (0)::numeric) AND (overall_score <= (100)::numeric)))
);


ALTER TABLE public.audit_pages OWNER TO owner;

--
-- Name: brand_profiles; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.brand_profiles (
    workspace_id uuid NOT NULL,
    id uuid DEFAULT uuidv7() NOT NULL,
    version bigint NOT NULL,
    is_current boolean DEFAULT false NOT NULL,
    schema_version bigint NOT NULL,
    payload jsonb NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT brand_profiles_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT brand_profiles_version_check CHECK ((version > 0))
);


ALTER TABLE public.brand_profiles OWNER TO owner;

--
-- Name: briefs; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.briefs (
    workspace_id uuid NOT NULL,
    id uuid DEFAULT uuidv7() NOT NULL,
    opportunity_id uuid NOT NULL,
    version bigint NOT NULL,
    brand_profile_id uuid NOT NULL,
    prompt_version text NOT NULL,
    keywords jsonb NOT NULL,
    schema_version bigint NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT briefs_version_check CHECK ((version > 0))
);


ALTER TABLE public.briefs OWNER TO owner;

--
-- Name: campaigns; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.campaigns (
    workspace_id uuid NOT NULL,
    id uuid DEFAULT uuidv7() NOT NULL,
    name text NOT NULL,
    locale text NOT NULL,
    market text NOT NULL,
    articles_per_week bigint NOT NULL,
    schedule_cron text NOT NULL,
    monthly_cap_usd numeric(10,2) NOT NULL,
    run_cap_usd numeric(10,2) NOT NULL,
    config jsonb NOT NULL,
    config_schema_version bigint NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid,
    updated_at timestamp with time zone,
    CONSTRAINT campaigns_articles_per_week_check CHECK (((articles_per_week >= 1) AND (articles_per_week <= 20))),
    CONSTRAINT campaigns_check CHECK (((run_cap_usd > (0)::numeric) AND (run_cap_usd <= monthly_cap_usd))),
    CONSTRAINT campaigns_locale_check CHECK ((locale ~ '^[a-z]{2}-[A-Z]{2}$'::text)),
    CONSTRAINT campaigns_market_check CHECK ((market ~ '^[A-Z]{2}$'::text)),
    CONSTRAINT campaigns_monthly_cap_usd_check CHECK ((monthly_cap_usd > (0)::numeric)),
    CONSTRAINT campaigns_status_check CHECK ((status = ANY (ARRAY['active'::text, 'paused'::text, 'archived'::text])))
);


ALTER TABLE public.campaigns OWNER TO owner;

--
-- Name: documents; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.documents (
    workspace_id uuid NOT NULL,
    id uuid DEFAULT uuidv7() NOT NULL,
    source_id uuid,
    url text NOT NULL,
    canonical_url text NOT NULL,
    content_hash bytea,
    title text NOT NULL,
    lang text,
    published_at timestamp with time zone,
    date_status text NOT NULL,
    fetch_status text NOT NULL,
    http_status bigint,
    extracted_text text,
    text_purged_at timestamp with time zone,
    meta jsonb DEFAULT '{}'::jsonb NOT NULL,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT documents_date_status_check CHECK ((date_status = ANY (ARRAY['verified'::text, 'inferred'::text, 'unverified'::text]))),
    CONSTRAINT documents_fetch_status_check CHECK ((fetch_status = ANY (ARRAY['ok'::text, 'http_error'::text, 'blocked'::text, 'extraction_failed'::text, 'paywalled'::text])))
);


ALTER TABLE public.documents OWNER TO owner;

--
-- Name: draft_claims; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.draft_claims (
    workspace_id uuid NOT NULL,
    draft_id uuid NOT NULL,
    idx bigint NOT NULL,
    kind text NOT NULL,
    text text NOT NULL,
    document_id uuid,
    CONSTRAINT draft_claims_check CHECK (((kind <> 'fact'::text) OR (document_id IS NOT NULL))),
    CONSTRAINT draft_claims_idx_check CHECK ((idx >= 0)),
    CONSTRAINT draft_claims_kind_check CHECK ((kind = ANY (ARRAY['fact'::text, 'analysis'::text, 'recommendation'::text])))
);


ALTER TABLE public.draft_claims OWNER TO owner;

--
-- Name: drafts; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.drafts (
    workspace_id uuid NOT NULL,
    id uuid DEFAULT uuidv7() NOT NULL,
    brief_id uuid NOT NULL,
    revision bigint NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    prompt_version text NOT NULL,
    schema_version bigint NOT NULL,
    payload jsonb NOT NULL,
    decision_note text,
    decided_by uuid,
    decided_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT drafts_revision_check CHECK (((revision >= 0) AND (revision <= 2))),
    CONSTRAINT drafts_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'evaluating'::text, 'pass'::text, 'revision'::text, 'reject'::text, 'approved'::text, 'declined'::text, 'published'::text])))
);


ALTER TABLE public.drafts OWNER TO owner;

--
-- Name: events; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.events (
    workspace_id uuid NOT NULL,
    id uuid DEFAULT uuidv7() NOT NULL,
    type text NOT NULL,
    actor_user_id uuid,
    entity_type text NOT NULL,
    entity_id uuid NOT NULL,
    payload jsonb NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.events OWNER TO owner;

--
-- Name: gsc_url_inspection_cache; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.gsc_url_inspection_cache (
    workspace_id uuid NOT NULL,
    site_url text NOT NULL,
    inspected_url text NOT NULL,
    payload jsonb NOT NULL,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.gsc_url_inspection_cache OWNER TO owner;

--
-- Name: keyword_cache; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.keyword_cache (
    provider text NOT NULL,
    locale text NOT NULL,
    market text NOT NULL,
    keyword_norm text NOT NULL,
    data jsonb NOT NULL,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.keyword_cache OWNER TO owner;

--
-- Name: keyword_rankings; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.keyword_rankings (
    workspace_id uuid NOT NULL,
    id uuid DEFAULT uuidv7() NOT NULL,
    keyword text NOT NULL,
    domain text NOT NULL,
    locale text DEFAULT 'pt-BR'::text NOT NULL,
    market text DEFAULT 'BR'::text NOT NULL,
    "position" bigint,
    previous_position bigint,
    ranking_url text,
    provider text NOT NULL,
    checked_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.keyword_rankings OWNER TO owner;

--
-- Name: llm_payloads; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.llm_payloads (
    usage_id uuid NOT NULL,
    request jsonb NOT NULL,
    response jsonb NOT NULL
);


ALTER TABLE public.llm_payloads OWNER TO owner;

--
-- Name: memberships; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.memberships (
    workspace_id uuid NOT NULL,
    user_id uuid NOT NULL,
    role text DEFAULT 'owner'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT memberships_role_check CHECK ((role = 'owner'::text))
);


ALTER TABLE public.memberships OWNER TO owner;

--
-- Name: opportunities; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.opportunities (
    workspace_id uuid NOT NULL,
    id uuid DEFAULT uuidv7() NOT NULL,
    run_id uuid NOT NULL,
    record_id uuid NOT NULL,
    scores jsonb NOT NULL,
    total numeric(5,2) NOT NULL,
    rank bigint,
    funnel_stage text NOT NULL,
    confidence text NOT NULL,
    angle text NOT NULL,
    status text DEFAULT 'scored'::text NOT NULL,
    decided_by uuid,
    decided_at timestamp with time zone,
    decision_note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT opportunities_check CHECK (((status = ANY (ARRAY['approved'::text, 'rejected'::text])) = ((decided_by IS NOT NULL) AND (decided_at IS NOT NULL)))),
    CONSTRAINT opportunities_confidence_check CHECK ((confidence = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text]))),
    CONSTRAINT opportunities_funnel_stage_check CHECK ((funnel_stage = ANY (ARRAY['tofu'::text, 'mofu'::text, 'bofu'::text]))),
    CONSTRAINT opportunities_status_check CHECK ((status = ANY (ARRAY['scored'::text, 'approved'::text, 'rejected'::text, 'expired'::text]))),
    CONSTRAINT opportunities_total_check CHECK (((total >= (0)::numeric) AND (total <= (100)::numeric)))
);


ALTER TABLE public.opportunities OWNER TO owner;

--
-- Name: publications; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.publications (
    workspace_id uuid NOT NULL,
    id uuid DEFAULT uuidv7() NOT NULL,
    draft_id uuid,
    destination text DEFAULT 'sitemap'::text NOT NULL,
    mode text DEFAULT 'live'::text NOT NULL,
    status text DEFAULT 'published'::text NOT NULL,
    external_id text,
    url text,
    title text NOT NULL,
    scheduled_at timestamp with time zone,
    published_at timestamp with time zone,
    response jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    error text,
    CONSTRAINT publications_mode_check CHECK ((mode = ANY (ARRAY['draft'::text, 'scheduled'::text, 'live'::text]))),
    CONSTRAINT publications_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'published'::text, 'failed'::text])))
);


ALTER TABLE public.publications OWNER TO owner;

--
-- Name: quality_reports; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.quality_reports (
    workspace_id uuid NOT NULL,
    draft_id uuid NOT NULL,
    verdict text NOT NULL,
    score numeric(5,2),
    checks jsonb DEFAULT '{}'::jsonb NOT NULL,
    findings jsonb DEFAULT '[]'::jsonb NOT NULL,
    evaluator_model text NOT NULL,
    prompt_version text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT quality_reports_verdict_check CHECK ((verdict = ANY (ARRAY['pass'::text, 'revision'::text, 'reject'::text])))
);


ALTER TABLE public.quality_reports OWNER TO owner;

--
-- Name: research_records; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.research_records (
    workspace_id uuid NOT NULL,
    id uuid DEFAULT uuidv7() NOT NULL,
    run_id uuid NOT NULL,
    document_id uuid NOT NULL,
    duplicate_of uuid,
    status text DEFAULT 'new'::text NOT NULL,
    schema_version bigint NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT research_records_check CHECK (((status = 'duplicate'::text) = (duplicate_of IS NOT NULL))),
    CONSTRAINT research_records_status_check CHECK ((status = ANY (ARRAY['new'::text, 'extracted'::text, 'verified'::text, 'stale'::text, 'duplicate'::text, 'failed'::text])))
);


ALTER TABLE public.research_records OWNER TO owner;

--
-- Name: runs; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.runs (
    workspace_id uuid NOT NULL,
    id uuid DEFAULT uuidv7() NOT NULL,
    campaign_id uuid NOT NULL,
    trigger text NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    stage text,
    window_start timestamp with time zone NOT NULL,
    window_end timestamp with time zone NOT NULL,
    run_cap_usd numeric(10,2) NOT NULL,
    cost_usd numeric(12,6) DEFAULT 0 NOT NULL,
    code_version text NOT NULL,
    pack_version text NOT NULL,
    blocked_reason text,
    error jsonb,
    created_by uuid,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT runs_check CHECK ((window_end > window_start)),
    CONSTRAINT runs_stage_check CHECK ((stage = ANY (ARRAY['research'::text, 'score'::text, 'report'::text, 'keywords'::text, 'brief'::text, 'write'::text, 'evaluate'::text, 'publish'::text]))),
    CONSTRAINT runs_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'blocked'::text, 'completed'::text, 'failed'::text, 'cancelled'::text]))),
    CONSTRAINT runs_trigger_check CHECK ((trigger = ANY (ARRAY['scheduled'::text, 'manual'::text, 'e2e'::text])))
);


ALTER TABLE public.runs OWNER TO owner;

--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.schema_migrations (
    version character varying NOT NULL
);


ALTER TABLE public.schema_migrations OWNER TO owner;

--
-- Name: site_audits; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.site_audits (
    workspace_id uuid NOT NULL,
    id uuid DEFAULT uuidv7() NOT NULL,
    start_url text NOT NULL,
    pages_crawled bigint NOT NULL,
    max_pages bigint NOT NULL,
    overall_score numeric(5,2) NOT NULL,
    grade text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT site_audits_max_pages_check CHECK (((max_pages > 0) AND (max_pages <= 200))),
    CONSTRAINT site_audits_overall_score_check CHECK (((overall_score >= (0)::numeric) AND (overall_score <= (100)::numeric))),
    CONSTRAINT site_audits_pages_crawled_check CHECK ((pages_crawled >= 0))
);


ALTER TABLE public.site_audits OWNER TO owner;

--
-- Name: sources; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.sources (
    id uuid DEFAULT uuidv7() NOT NULL,
    workspace_id uuid,
    industry_pack text NOT NULL,
    name text NOT NULL,
    domain text NOT NULL,
    url text NOT NULL,
    feed_url text,
    fetch_mode text NOT NULL,
    tier bigint NOT NULL,
    role text NOT NULL,
    locales text[] NOT NULL,
    topics text[] DEFAULT '{}'::text[] NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    last_fetch_ok boolean,
    last_fetched_at timestamp with time zone,
    added_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT sources_check CHECK (((fetch_mode <> 'feed'::text) OR (feed_url IS NOT NULL))),
    CONSTRAINT sources_domain_check CHECK ((domain = lower(domain))),
    CONSTRAINT sources_fetch_mode_check CHECK ((fetch_mode = ANY (ARRAY['feed'::text, 'site_search'::text]))),
    CONSTRAINT sources_locales_check CHECK ((cardinality(locales) > 0)),
    CONSTRAINT sources_role_check CHECK ((role = ANY (ARRAY['news'::text, 'company'::text, 'data'::text, 'management'::text]))),
    CONSTRAINT sources_tier_check CHECK (((tier >= 1) AND (tier <= 3)))
);


ALTER TABLE public.sources OWNER TO owner;

--
-- Name: url_monitors; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.url_monitors (
    workspace_id uuid NOT NULL,
    id uuid DEFAULT uuidv7() NOT NULL,
    url text NOT NULL,
    schedule_cron text DEFAULT '0 9 * * *'::text NOT NULL,
    score_drop_threshold numeric(5,2) DEFAULT 5.00 NOT NULL,
    tls_expiry_warn_days bigint DEFAULT 14 NOT NULL,
    last_score numeric(5,2),
    last_checks jsonb,
    last_tls_days_left bigint,
    last_status text DEFAULT 'pending'::text NOT NULL,
    last_checked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.url_monitors OWNER TO owner;

--
-- Name: usage_ledger; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.usage_ledger (
    workspace_id uuid NOT NULL,
    id uuid DEFAULT uuidv7() NOT NULL,
    run_id uuid,
    stage text NOT NULL,
    kind text NOT NULL,
    vendor text NOT NULL,
    model text,
    generation_id text,
    input_tokens bigint,
    output_tokens bigint,
    cached_tokens bigint,
    units numeric(12,3),
    cost_usd numeric(12,6) NOT NULL,
    latency_ms bigint,
    prompt_version text,
    entity_type text,
    entity_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT usage_ledger_cost_usd_check CHECK ((cost_usd >= (0)::numeric)),
    CONSTRAINT usage_ledger_kind_check CHECK ((kind = ANY (ARRAY['llm'::text, 'api'::text])))
);


ALTER TABLE public.usage_ledger OWNER TO owner;

--
-- Name: users; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.users (
    id uuid DEFAULT uuidv7() NOT NULL,
    email text NOT NULL,
    name text,
    ui_locale text DEFAULT 'pt-BR'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_login_at timestamp with time zone,
    password_hash text,
    CONSTRAINT users_ui_locale_check CHECK ((ui_locale = ANY (ARRAY['pt-BR'::text, 'en-US'::text])))
);


ALTER TABLE public.users OWNER TO owner;

--
-- Name: webhook_deliveries; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.webhook_deliveries (
    workspace_id uuid NOT NULL,
    subscription_id uuid NOT NULL,
    event_id uuid NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    attempts bigint DEFAULT 0 NOT NULL,
    next_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
    last_http_status bigint,
    last_error text,
    delivered_at timestamp with time zone,
    CONSTRAINT webhook_deliveries_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'delivered'::text, 'retrying'::text, 'dead'::text])))
);


ALTER TABLE public.webhook_deliveries OWNER TO owner;

--
-- Name: webhook_subscriptions; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.webhook_subscriptions (
    workspace_id uuid NOT NULL,
    id uuid DEFAULT uuidv7() NOT NULL,
    url text NOT NULL,
    event_types text[] NOT NULL,
    secret bytea NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT webhook_subscriptions_event_types_check CHECK ((cardinality(event_types) > 0)),
    CONSTRAINT webhook_subscriptions_url_check CHECK ((url ~ '^https?://'::text))
);


ALTER TABLE public.webhook_subscriptions OWNER TO owner;

--
-- Name: workspace_integrations; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.workspace_integrations (
    workspace_id uuid NOT NULL,
    port text NOT NULL,
    plugin text NOT NULL,
    settings jsonb DEFAULT '{}'::jsonb NOT NULL,
    secrets bytea,
    key_version bigint DEFAULT 1 NOT NULL,
    last_test_ok boolean,
    last_tested_at timestamp with time zone,
    updated_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT workspace_integrations_port_check CHECK ((port = ANY (ARRAY['llm'::text, 'feed_source'::text, 'news_search'::text, 'extractor'::text, 'keyword_provider'::text, 'publisher'::text, 'analytics_source'::text])))
);


ALTER TABLE public.workspace_integrations OWNER TO owner;

--
-- Name: workspaces; Type: TABLE; Schema: public; Owner: owner
--

CREATE TABLE public.workspaces (
    id uuid DEFAULT uuidv7() NOT NULL,
    slug text NOT NULL,
    name text NOT NULL,
    industry_pack text NOT NULL,
    timezone text DEFAULT 'America/Sao_Paulo'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    config jsonb,
    CONSTRAINT workspaces_slug_check CHECK ((slug ~ '^[a-z0-9-]{3,40}$'::text))
);


ALTER TABLE public.workspaces OWNER TO owner;

--
-- Name: audit_pages audit_pages_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.audit_pages
    ADD CONSTRAINT audit_pages_pkey PRIMARY KEY (id);


--
-- Name: audit_pages audit_pages_workspace_id_id_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.audit_pages
    ADD CONSTRAINT audit_pages_workspace_id_id_key UNIQUE (workspace_id, id);


--
-- Name: brand_profiles brand_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.brand_profiles
    ADD CONSTRAINT brand_profiles_pkey PRIMARY KEY (id);


--
-- Name: brand_profiles brand_profiles_workspace_id_id_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.brand_profiles
    ADD CONSTRAINT brand_profiles_workspace_id_id_key UNIQUE (workspace_id, id);


--
-- Name: brand_profiles brand_profiles_workspace_id_version_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.brand_profiles
    ADD CONSTRAINT brand_profiles_workspace_id_version_key UNIQUE (workspace_id, version);


--
-- Name: briefs briefs_opportunity_id_version_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.briefs
    ADD CONSTRAINT briefs_opportunity_id_version_key UNIQUE (opportunity_id, version);


--
-- Name: briefs briefs_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.briefs
    ADD CONSTRAINT briefs_pkey PRIMARY KEY (id);


--
-- Name: briefs briefs_workspace_id_id_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.briefs
    ADD CONSTRAINT briefs_workspace_id_id_key UNIQUE (workspace_id, id);


--
-- Name: campaigns campaigns_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.campaigns
    ADD CONSTRAINT campaigns_pkey PRIMARY KEY (id);


--
-- Name: campaigns campaigns_workspace_id_id_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.campaigns
    ADD CONSTRAINT campaigns_workspace_id_id_key UNIQUE (workspace_id, id);


--
-- Name: campaigns campaigns_workspace_id_name_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.campaigns
    ADD CONSTRAINT campaigns_workspace_id_name_key UNIQUE (workspace_id, name);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: documents documents_workspace_id_canonical_url_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_workspace_id_canonical_url_key UNIQUE (workspace_id, canonical_url);


--
-- Name: documents documents_workspace_id_id_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_workspace_id_id_key UNIQUE (workspace_id, id);


--
-- Name: draft_claims draft_claims_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.draft_claims
    ADD CONSTRAINT draft_claims_pkey PRIMARY KEY (draft_id, idx);


--
-- Name: drafts drafts_brief_id_revision_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.drafts
    ADD CONSTRAINT drafts_brief_id_revision_key UNIQUE (brief_id, revision);


--
-- Name: drafts drafts_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.drafts
    ADD CONSTRAINT drafts_pkey PRIMARY KEY (id);


--
-- Name: drafts drafts_workspace_id_id_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.drafts
    ADD CONSTRAINT drafts_workspace_id_id_key UNIQUE (workspace_id, id);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: gsc_url_inspection_cache gsc_url_inspection_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.gsc_url_inspection_cache
    ADD CONSTRAINT gsc_url_inspection_cache_pkey PRIMARY KEY (workspace_id, site_url, inspected_url);


--
-- Name: keyword_cache keyword_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.keyword_cache
    ADD CONSTRAINT keyword_cache_pkey PRIMARY KEY (provider, locale, market, keyword_norm);


--
-- Name: keyword_rankings keyword_rankings_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.keyword_rankings
    ADD CONSTRAINT keyword_rankings_pkey PRIMARY KEY (id);


--
-- Name: llm_payloads llm_payloads_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.llm_payloads
    ADD CONSTRAINT llm_payloads_pkey PRIMARY KEY (usage_id);


--
-- Name: memberships memberships_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_pkey PRIMARY KEY (workspace_id, user_id);


--
-- Name: opportunities opportunities_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.opportunities
    ADD CONSTRAINT opportunities_pkey PRIMARY KEY (id);


--
-- Name: opportunities opportunities_run_id_record_id_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.opportunities
    ADD CONSTRAINT opportunities_run_id_record_id_key UNIQUE (run_id, record_id);


--
-- Name: opportunities opportunities_workspace_id_id_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.opportunities
    ADD CONSTRAINT opportunities_workspace_id_id_key UNIQUE (workspace_id, id);


--
-- Name: publications publications_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.publications
    ADD CONSTRAINT publications_pkey PRIMARY KEY (id);


--
-- Name: publications publications_workspace_draft_destination_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.publications
    ADD CONSTRAINT publications_workspace_draft_destination_key UNIQUE (workspace_id, draft_id, destination);


--
-- Name: publications publications_workspace_id_id_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.publications
    ADD CONSTRAINT publications_workspace_id_id_key UNIQUE (workspace_id, id);


--
-- Name: publications publications_workspace_id_url_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.publications
    ADD CONSTRAINT publications_workspace_id_url_key UNIQUE (workspace_id, url);


--
-- Name: quality_reports quality_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.quality_reports
    ADD CONSTRAINT quality_reports_pkey PRIMARY KEY (draft_id);


--
-- Name: research_records research_records_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.research_records
    ADD CONSTRAINT research_records_pkey PRIMARY KEY (id);


--
-- Name: research_records research_records_run_id_document_id_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.research_records
    ADD CONSTRAINT research_records_run_id_document_id_key UNIQUE (run_id, document_id);


--
-- Name: research_records research_records_workspace_id_id_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.research_records
    ADD CONSTRAINT research_records_workspace_id_id_key UNIQUE (workspace_id, id);


--
-- Name: runs runs_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_pkey PRIMARY KEY (id);


--
-- Name: runs runs_workspace_id_id_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_workspace_id_id_key UNIQUE (workspace_id, id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: site_audits site_audits_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.site_audits
    ADD CONSTRAINT site_audits_pkey PRIMARY KEY (id);


--
-- Name: site_audits site_audits_workspace_id_id_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.site_audits
    ADD CONSTRAINT site_audits_workspace_id_id_key UNIQUE (workspace_id, id);


--
-- Name: sources sources_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.sources
    ADD CONSTRAINT sources_pkey PRIMARY KEY (id);


--
-- Name: sources sources_workspace_id_domain_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.sources
    ADD CONSTRAINT sources_workspace_id_domain_key UNIQUE NULLS NOT DISTINCT (workspace_id, domain);


--
-- Name: url_monitors url_monitors_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.url_monitors
    ADD CONSTRAINT url_monitors_pkey PRIMARY KEY (id);


--
-- Name: url_monitors url_monitors_workspace_id_url_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.url_monitors
    ADD CONSTRAINT url_monitors_workspace_id_url_key UNIQUE (workspace_id, url);


--
-- Name: usage_ledger usage_ledger_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.usage_ledger
    ADD CONSTRAINT usage_ledger_pkey PRIMARY KEY (id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: webhook_deliveries webhook_deliveries_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.webhook_deliveries
    ADD CONSTRAINT webhook_deliveries_pkey PRIMARY KEY (subscription_id, event_id);


--
-- Name: webhook_subscriptions webhook_subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.webhook_subscriptions
    ADD CONSTRAINT webhook_subscriptions_pkey PRIMARY KEY (id);


--
-- Name: webhook_subscriptions webhook_subscriptions_workspace_id_id_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.webhook_subscriptions
    ADD CONSTRAINT webhook_subscriptions_workspace_id_id_key UNIQUE (workspace_id, id);


--
-- Name: workspace_integrations workspace_integrations_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.workspace_integrations
    ADD CONSTRAINT workspace_integrations_pkey PRIMARY KEY (workspace_id, port);


--
-- Name: workspaces workspaces_pkey; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.workspaces
    ADD CONSTRAINT workspaces_pkey PRIMARY KEY (id);


--
-- Name: workspaces workspaces_slug_key; Type: CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.workspaces
    ADD CONSTRAINT workspaces_slug_key UNIQUE (slug);


--
-- Name: audit_pages_ws_audit_idx; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX audit_pages_ws_audit_idx ON public.audit_pages USING btree (workspace_id, site_audit_id, created_at DESC);


--
-- Name: brand_one_current; Type: INDEX; Schema: public; Owner: owner
--

CREATE UNIQUE INDEX brand_one_current ON public.brand_profiles USING btree (workspace_id) WHERE is_current;


--
-- Name: briefs_ws_opp_ver_idx; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX briefs_ws_opp_ver_idx ON public.briefs USING btree (workspace_id, opportunity_id, version DESC);


--
-- Name: deliveries_due; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX deliveries_due ON public.webhook_deliveries USING btree (next_attempt_at) WHERE (status = ANY (ARRAY['pending'::text, 'retrying'::text]));


--
-- Name: documents_published; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX documents_published ON public.documents USING btree (workspace_id, published_at DESC);


--
-- Name: documents_title_trgm; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX documents_title_trgm ON public.documents USING gin (title public.gin_trgm_ops);


--
-- Name: draft_claims_ws_draft_idx; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX draft_claims_ws_draft_idx ON public.draft_claims USING btree (workspace_id, draft_id, idx);


--
-- Name: drafts_ws_brief_rev_idx; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX drafts_ws_brief_rev_idx ON public.drafts USING btree (workspace_id, brief_id, revision DESC);


--
-- Name: events_entity; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX events_entity ON public.events USING btree (entity_type, entity_id);


--
-- Name: events_ws_time; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX events_ws_time ON public.events USING btree (workspace_id, occurred_at DESC);


--
-- Name: gsc_inspection_ws_fetched_idx; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX gsc_inspection_ws_fetched_idx ON public.gsc_url_inspection_cache USING btree (workspace_id, fetched_at DESC);


--
-- Name: keyword_cache_fetched_at_idx; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX keyword_cache_fetched_at_idx ON public.keyword_cache USING btree (fetched_at);


--
-- Name: keyword_rankings_ws_kw_idx; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX keyword_rankings_ws_kw_idx ON public.keyword_rankings USING btree (workspace_id, domain, keyword, checked_at DESC);


--
-- Name: opp_report; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX opp_report ON public.opportunities USING btree (run_id, total DESC);


--
-- Name: pub_title_trgm; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX pub_title_trgm ON public.publications USING gin (title public.gin_trgm_ops);


--
-- Name: rr_queue; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX rr_queue ON public.research_records USING btree (run_id, status) WHERE (status = ANY (ARRAY['new'::text, 'extracted'::text]));


--
-- Name: runs_one_active; Type: INDEX; Schema: public; Owner: owner
--

CREATE UNIQUE INDEX runs_one_active ON public.runs USING btree (campaign_id) WHERE (status = ANY (ARRAY['queued'::text, 'running'::text]));


--
-- Name: site_audits_ws_created_idx; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX site_audits_ws_created_idx ON public.site_audits USING btree (workspace_id, created_at DESC);


--
-- Name: url_monitors_ws_checked_idx; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX url_monitors_ws_checked_idx ON public.url_monitors USING btree (workspace_id, last_checked_at DESC);


--
-- Name: usage_month; Type: INDEX; Schema: public; Owner: owner
--

CREATE INDEX usage_month ON public.usage_ledger USING btree (workspace_id, created_at);


--
-- Name: users_email_uq; Type: INDEX; Schema: public; Owner: owner
--

CREATE UNIQUE INDEX users_email_uq ON public.users USING btree (lower(email));


--
-- Name: audit_pages audit_pages_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.audit_pages
    ADD CONSTRAINT audit_pages_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id);


--
-- Name: audit_pages audit_pages_workspace_id_site_audit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.audit_pages
    ADD CONSTRAINT audit_pages_workspace_id_site_audit_id_fkey FOREIGN KEY (workspace_id, site_audit_id) REFERENCES public.site_audits(workspace_id, id);


--
-- Name: brand_profiles brand_profiles_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.brand_profiles
    ADD CONSTRAINT brand_profiles_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: brand_profiles brand_profiles_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.brand_profiles
    ADD CONSTRAINT brand_profiles_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id);


--
-- Name: briefs briefs_workspace_id_brand_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.briefs
    ADD CONSTRAINT briefs_workspace_id_brand_profile_id_fkey FOREIGN KEY (workspace_id, brand_profile_id) REFERENCES public.brand_profiles(workspace_id, id);


--
-- Name: briefs briefs_workspace_id_opportunity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.briefs
    ADD CONSTRAINT briefs_workspace_id_opportunity_id_fkey FOREIGN KEY (workspace_id, opportunity_id) REFERENCES public.opportunities(workspace_id, id);


--
-- Name: campaigns campaigns_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.campaigns
    ADD CONSTRAINT campaigns_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: campaigns campaigns_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.campaigns
    ADD CONSTRAINT campaigns_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.users(id);


--
-- Name: campaigns campaigns_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.campaigns
    ADD CONSTRAINT campaigns_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id);


--
-- Name: documents documents_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.sources(id);


--
-- Name: documents documents_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id);


--
-- Name: draft_claims draft_claims_workspace_id_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.draft_claims
    ADD CONSTRAINT draft_claims_workspace_id_document_id_fkey FOREIGN KEY (workspace_id, document_id) REFERENCES public.documents(workspace_id, id);


--
-- Name: draft_claims draft_claims_workspace_id_draft_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.draft_claims
    ADD CONSTRAINT draft_claims_workspace_id_draft_id_fkey FOREIGN KEY (workspace_id, draft_id) REFERENCES public.drafts(workspace_id, id) ON DELETE CASCADE;


--
-- Name: drafts drafts_decided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.drafts
    ADD CONSTRAINT drafts_decided_by_fkey FOREIGN KEY (decided_by) REFERENCES public.users(id);


--
-- Name: drafts drafts_workspace_id_brief_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.drafts
    ADD CONSTRAINT drafts_workspace_id_brief_id_fkey FOREIGN KEY (workspace_id, brief_id) REFERENCES public.briefs(workspace_id, id);


--
-- Name: events events_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES public.users(id);


--
-- Name: events events_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id);


--
-- Name: gsc_url_inspection_cache gsc_url_inspection_cache_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.gsc_url_inspection_cache
    ADD CONSTRAINT gsc_url_inspection_cache_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id);


--
-- Name: keyword_rankings keyword_rankings_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.keyword_rankings
    ADD CONSTRAINT keyword_rankings_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id);


--
-- Name: llm_payloads llm_payloads_usage_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.llm_payloads
    ADD CONSTRAINT llm_payloads_usage_id_fkey FOREIGN KEY (usage_id) REFERENCES public.usage_ledger(id) ON DELETE CASCADE;


--
-- Name: memberships memberships_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: memberships memberships_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id);


--
-- Name: opportunities opportunities_decided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.opportunities
    ADD CONSTRAINT opportunities_decided_by_fkey FOREIGN KEY (decided_by) REFERENCES public.users(id);


--
-- Name: opportunities opportunities_workspace_id_record_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.opportunities
    ADD CONSTRAINT opportunities_workspace_id_record_id_fkey FOREIGN KEY (workspace_id, record_id) REFERENCES public.research_records(workspace_id, id);


--
-- Name: opportunities opportunities_workspace_id_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.opportunities
    ADD CONSTRAINT opportunities_workspace_id_run_id_fkey FOREIGN KEY (workspace_id, run_id) REFERENCES public.runs(workspace_id, id);


--
-- Name: publications publications_workspace_draft_fk; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.publications
    ADD CONSTRAINT publications_workspace_draft_fk FOREIGN KEY (workspace_id, draft_id) REFERENCES public.drafts(workspace_id, id);


--
-- Name: publications publications_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.publications
    ADD CONSTRAINT publications_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id);


--
-- Name: quality_reports quality_reports_workspace_id_draft_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.quality_reports
    ADD CONSTRAINT quality_reports_workspace_id_draft_id_fkey FOREIGN KEY (workspace_id, draft_id) REFERENCES public.drafts(workspace_id, id) ON DELETE CASCADE;


--
-- Name: research_records research_records_workspace_id_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.research_records
    ADD CONSTRAINT research_records_workspace_id_document_id_fkey FOREIGN KEY (workspace_id, document_id) REFERENCES public.documents(workspace_id, id);


--
-- Name: research_records research_records_workspace_id_duplicate_of_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.research_records
    ADD CONSTRAINT research_records_workspace_id_duplicate_of_fkey FOREIGN KEY (workspace_id, duplicate_of) REFERENCES public.research_records(workspace_id, id);


--
-- Name: research_records research_records_workspace_id_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.research_records
    ADD CONSTRAINT research_records_workspace_id_run_id_fkey FOREIGN KEY (workspace_id, run_id) REFERENCES public.runs(workspace_id, id);


--
-- Name: runs runs_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: runs runs_workspace_id_campaign_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_workspace_id_campaign_id_fkey FOREIGN KEY (workspace_id, campaign_id) REFERENCES public.campaigns(workspace_id, id);


--
-- Name: site_audits site_audits_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.site_audits
    ADD CONSTRAINT site_audits_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id);


--
-- Name: sources sources_added_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.sources
    ADD CONSTRAINT sources_added_by_fkey FOREIGN KEY (added_by) REFERENCES public.users(id);


--
-- Name: sources sources_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.sources
    ADD CONSTRAINT sources_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id);


--
-- Name: url_monitors url_monitors_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.url_monitors
    ADD CONSTRAINT url_monitors_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id);


--
-- Name: usage_ledger usage_ledger_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.usage_ledger
    ADD CONSTRAINT usage_ledger_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id);


--
-- Name: usage_ledger usage_ledger_workspace_id_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.usage_ledger
    ADD CONSTRAINT usage_ledger_workspace_id_run_id_fkey FOREIGN KEY (workspace_id, run_id) REFERENCES public.runs(workspace_id, id);


--
-- Name: webhook_deliveries webhook_deliveries_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.webhook_deliveries
    ADD CONSTRAINT webhook_deliveries_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id);


--
-- Name: webhook_deliveries webhook_deliveries_workspace_id_subscription_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.webhook_deliveries
    ADD CONSTRAINT webhook_deliveries_workspace_id_subscription_id_fkey FOREIGN KEY (workspace_id, subscription_id) REFERENCES public.webhook_subscriptions(workspace_id, id);


--
-- Name: webhook_subscriptions webhook_subscriptions_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.webhook_subscriptions
    ADD CONSTRAINT webhook_subscriptions_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: webhook_subscriptions webhook_subscriptions_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.webhook_subscriptions
    ADD CONSTRAINT webhook_subscriptions_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id);


--
-- Name: workspace_integrations workspace_integrations_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.workspace_integrations
    ADD CONSTRAINT workspace_integrations_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.users(id);


--
-- Name: workspace_integrations workspace_integrations_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: owner
--

ALTER TABLE ONLY public.workspace_integrations
    ADD CONSTRAINT workspace_integrations_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id);


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: pg_database_owner
--

GRANT USAGE ON SCHEMA public TO app;
GRANT USAGE ON SCHEMA public TO retention;
GRANT USAGE ON SCHEMA public TO readonly;


--
-- Name: FUNCTION anonymise_user_events(target_user_id uuid); Type: ACL; Schema: public; Owner: owner
--

REVOKE ALL ON FUNCTION public.anonymise_user_events(target_user_id uuid) FROM PUBLIC;
GRANT ALL ON FUNCTION public.anonymise_user_events(target_user_id uuid) TO app;


--
-- Name: TABLE audit_pages; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.audit_pages TO app;
GRANT SELECT ON TABLE public.audit_pages TO readonly;


--
-- Name: TABLE brand_profiles; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.brand_profiles TO app;
GRANT SELECT ON TABLE public.brand_profiles TO readonly;


--
-- Name: TABLE briefs; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.briefs TO app;
GRANT SELECT ON TABLE public.briefs TO readonly;


--
-- Name: TABLE campaigns; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.campaigns TO app;
GRANT SELECT ON TABLE public.campaigns TO readonly;


--
-- Name: TABLE documents; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.documents TO app;
GRANT SELECT ON TABLE public.documents TO retention;
GRANT SELECT ON TABLE public.documents TO readonly;


--
-- Name: COLUMN documents.extracted_text; Type: ACL; Schema: public; Owner: owner
--

GRANT UPDATE(extracted_text) ON TABLE public.documents TO retention;


--
-- Name: COLUMN documents.text_purged_at; Type: ACL; Schema: public; Owner: owner
--

GRANT UPDATE(text_purged_at) ON TABLE public.documents TO retention;


--
-- Name: TABLE draft_claims; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.draft_claims TO app;
GRANT SELECT ON TABLE public.draft_claims TO readonly;


--
-- Name: TABLE drafts; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.drafts TO app;
GRANT SELECT ON TABLE public.drafts TO readonly;


--
-- Name: TABLE events; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT ON TABLE public.events TO app;
GRANT SELECT,DELETE ON TABLE public.events TO retention;
GRANT SELECT ON TABLE public.events TO readonly;


--
-- Name: TABLE gsc_url_inspection_cache; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.gsc_url_inspection_cache TO app;
GRANT SELECT ON TABLE public.gsc_url_inspection_cache TO readonly;


--
-- Name: TABLE keyword_cache; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.keyword_cache TO app;
GRANT SELECT ON TABLE public.keyword_cache TO readonly;


--
-- Name: TABLE keyword_rankings; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.keyword_rankings TO app;
GRANT SELECT ON TABLE public.keyword_rankings TO readonly;


--
-- Name: TABLE llm_payloads; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.llm_payloads TO app;
GRANT SELECT,DELETE ON TABLE public.llm_payloads TO retention;
GRANT SELECT ON TABLE public.llm_payloads TO readonly;


--
-- Name: TABLE memberships; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.memberships TO app;
GRANT SELECT ON TABLE public.memberships TO readonly;


--
-- Name: TABLE opportunities; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.opportunities TO app;
GRANT SELECT ON TABLE public.opportunities TO readonly;


--
-- Name: TABLE publications; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.publications TO app;
GRANT SELECT ON TABLE public.publications TO readonly;


--
-- Name: TABLE quality_reports; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.quality_reports TO app;
GRANT SELECT ON TABLE public.quality_reports TO readonly;


--
-- Name: TABLE research_records; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.research_records TO app;
GRANT SELECT ON TABLE public.research_records TO readonly;


--
-- Name: TABLE runs; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.runs TO app;
GRANT SELECT ON TABLE public.runs TO readonly;


--
-- Name: TABLE schema_migrations; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.schema_migrations TO app;
GRANT SELECT ON TABLE public.schema_migrations TO readonly;


--
-- Name: TABLE site_audits; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.site_audits TO app;
GRANT SELECT ON TABLE public.site_audits TO readonly;


--
-- Name: TABLE sources; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.sources TO app;
GRANT SELECT ON TABLE public.sources TO readonly;


--
-- Name: TABLE url_monitors; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.url_monitors TO app;
GRANT SELECT ON TABLE public.url_monitors TO readonly;


--
-- Name: TABLE usage_ledger; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.usage_ledger TO app;
GRANT SELECT ON TABLE public.usage_ledger TO readonly;
GRANT SELECT ON TABLE public.usage_ledger TO retention;


--
-- Name: TABLE users; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.users TO app;
GRANT SELECT ON TABLE public.users TO readonly;


--
-- Name: TABLE webhook_deliveries; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.webhook_deliveries TO app;
GRANT SELECT,DELETE ON TABLE public.webhook_deliveries TO retention;
GRANT SELECT ON TABLE public.webhook_deliveries TO readonly;


--
-- Name: TABLE webhook_subscriptions; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.webhook_subscriptions TO app;
GRANT SELECT ON TABLE public.webhook_subscriptions TO readonly;


--
-- Name: TABLE workspace_integrations; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.workspace_integrations TO app;
GRANT SELECT ON TABLE public.workspace_integrations TO readonly;


--
-- Name: TABLE workspaces; Type: ACL; Schema: public; Owner: owner
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.workspaces TO app;
GRANT SELECT ON TABLE public.workspaces TO readonly;


--
-- PostgreSQL database dump complete
--

\unrestrict W7RRb52dleszyN8liZ4WRKODUBJ6VUV54Sr4NUZormc1O67gU85Qe35Q6CDAt4Y
