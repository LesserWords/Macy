# ruff: noqa
"""Vulture whitelist for dynamic entry points and framework hooks (ADR-006)."""

from typing import Any

from engine import config, contracts, db, services

_: Any = None

# Pydantic contract attributes and validators
_.validate_total = contracts.ScoringWeights.validate_total
_.goal = contracts.CampaignConfig.goal
_.cta_intensity_limits = contracts.CampaignConfig.cta_intensity_limits
_.pillars = contracts.CampaignConfig.pillars

_.voice = contracts.BrandProfile.voice
_.positioning = contracts.BrandProfile.positioning
_.capabilities = contracts.BrandProfile.capabilities
_.prohibited_claims = contracts.BrandProfile.prohibited_claims
_.cta_rules = contracts.BrandProfile.cta_rules
_.validate_non_empty_text = contracts.BrandProfile.validate_non_empty_text  # type: ignore[misc]
_.validate_list_entries = contracts.BrandProfile.validate_list_entries  # type: ignore[misc]

_.headline = contracts.ResearchRecord.headline
_.canonical_url = contracts.ResearchRecord.canonical_url
_.published_at = contracts.ResearchRecord.published_at
_.event_date = contracts.ResearchRecord.event_date
_.summary = contracts.ResearchRecord.summary
_.facts = contracts.ResearchRecord.facts
_.entities = contracts.ResearchRecord.entities
_.source_type = contracts.ResearchRecord.source_type
_.verification_status = contracts.ResearchRecord.verification_status

_.evidence = contracts.SubScore.evidence
_.is_estimate = contracts.SubScore.is_estimate

_.title = contracts.Opportunity.title
_.angle = contracts.Opportunity.angle
_.funnel_stage = contracts.Opportunity.funnel_stage
_.confidence = contracts.Opportunity.confidence

_.fact = contracts.RequiredFact.fact

_.brand_profile_id = contracts.EditorialBrief.brand_profile_id
_.brand_version = contracts.EditorialBrief.brand_version
_.topic = contracts.EditorialBrief.topic
_.target_audience = contracts.EditorialBrief.target_audience
_.primary_keyword = contracts.EditorialBrief.primary_keyword
_.secondary_keywords = contracts.EditorialBrief.secondary_keywords
_.format = contracts.EditorialBrief.format
_.outline = contracts.EditorialBrief.outline
_.required_facts = contracts.EditorialBrief.required_facts
_.cta_goal = contracts.EditorialBrief.cta_goal

_.text = contracts.DraftClaim.text
_.validate_fact_source = contracts.DraftClaim.validate_fact_source

_.meta_description = contracts.ContentDraft.meta_description
_.body_md = contracts.ContentDraft.body_md
_.faq = contracts.ContentDraft.faq
_.cta = contracts.ContentDraft.cta

_.criterion = contracts.Finding.criterion
_.severity = contracts.Finding.severity
_.detail = contracts.Finding.detail
_.responsible_module = contracts.Finding.responsible_module

_.verdict = contracts.QualityReport.verdict
_.findings = contracts.QualityReport.findings

_.draft_id = contracts.PublishRequest.draft_id
_.destination = contracts.PublishRequest.destination
_.mode = contracts.PublishRequest.mode
_.scheduled_at = contracts.PublishRequest.scheduled_at
_.validate_mode = contracts.PublishRequest.validate_mode

_.external_id = contracts.PublishedContent.external_id
_.status = contracts.PublishedContent.status
_.raw = contracts.PublishedContent.raw
_.validate_status = contracts.PublishedContent.validate_status

_.keyword = contracts.KeywordData.keyword
_.search_volume = contracts.KeywordData.search_volume
_.difficulty = contracts.KeywordData.difficulty
_.cpc_usd = contracts.KeywordData.cpc_usd
_.intent = contracts.KeywordData.intent

# Config pack models
_.locales = config.IndustryPack.locales
_.description = config.IndustryPack.description
_.weight = config.RubricCriterion.weight
_.critical = config.RubricCriterion.critical
_.min_words = config.FormatPack.min_words
_.max_words = config.FormatPack.max_words
_.structure = config.FormatPack.structure

from engine.integrations import fakes, ports

# Integrations plugins and ports
_.FakeLLM = fakes.FakeLLM
_.FakeFeedSource = fakes.FakeFeedSource
_.FakeNewsSearch = fakes.FakeNewsSearch
_.FakeExtractor = fakes.FakeExtractor
_.FakeKeywordProvider = fakes.FakeKeywordProvider
_.FakePublisher = fakes.FakePublisher
_.FakeAnalyticsSource = fakes.FakeAnalyticsSource

_.model_name = fakes.FakeLLM.Settings.model_name
_.default_response = fakes.FakeLLM.Settings.default_response
_.default_items_count = fakes.FakeFeedSource.Settings.default_items_count
_.auth_token = fakes.FakeFeedSource.Settings.auth_token
_.default_results_count = fakes.FakeNewsSearch.Settings.default_results_count
_.user_agent = fakes.FakeExtractor.Settings.user_agent
_.default_volume = fakes.FakeKeywordProvider.Settings.default_volume
_.default_cpc = fakes.FakeKeywordProvider.Settings.default_cpc
_.platform_name = fakes.FakePublisher.Settings.platform_name
_.endpoint_url = fakes.FakePublisher.Settings.endpoint_url
_.views_multiplier = fakes.FakeAnalyticsSource.Settings.views_multiplier
_.api_key = fakes.FakeLLM.Settings.api_key
_.model_config = fakes.FakeLLM.Settings.model_config

from engine.integrations import openrouter, trafilatura_extractor

_.OpenRouterLLM = openrouter.OpenRouterLLM
_.default_model = openrouter.OpenRouterLLM.Settings.default_model
_.models_by_stage = openrouter.OpenRouterLLM.Settings.models_by_stage
_.data_policy = openrouter.OpenRouterLLM.Settings.data_policy
_.base_url = openrouter.OpenRouterLLM.Settings.base_url
_.handle_starttag = trafilatura_extractor._MetadataAndLinkParser.handle_starttag

# Mocking and test attributes

from engine import pipeline, services
from engine.strategy import score

_.PIPELINE_STAGES = pipeline.PIPELINE_STAGES
_.process_next_queued_run = pipeline.process_next_queued_run
_.trigger_scheduled_runs = services.trigger_scheduled_runs
_.score_run = services.score_run
_.confidence_level = contracts.Opportunity.confidence_level
_.validate_funnel_stage = contracts.Opportunity.validate_funnel_stage
_.normalize_score = contracts.SubScore.normalize_score
_.report = services.report
_.get_opportunity_report_items = db.get_opportunity_report_items

from tests import test_live_e2e_j0_j4, test_post_deploy_smoke, test_webhooks

_.do_GET = test_post_deploy_smoke._SmokeHandler.do_GET
_.do_POST = test_webhooks.MockWebhookHandler.do_POST
_.log_message = test_webhooks.MockWebhookHandler.log_message
_.pytestmark = test_live_e2e_j0_j4.pytestmark

# M6-02 HTMLParser hooks and Pydantic contract fields
from engine.audit import page_checks

_.handle_endtag = page_checks._AuditHTMLParser.handle_endtag
_.handle_data = page_checks._AuditHTMLParser.handle_data
_.concurrency = contracts.CrawlConfig.concurrency
_.audited_at = contracts.PageAuditReport.audited_at
_.broken_external_links = contracts.SiteAuditReport.broken_external_links
_.duplicate_meta_descriptions = contracts.SiteAuditReport.duplicate_meta_descriptions
_.crawled_at = contracts.SiteAuditReport.crawled_at

# M7-01 capability entry-point plugins
from engine.capabilities import builtins as cap_builtins

_.CoreComplianceCapability = cap_builtins.CoreComplianceCapability
_.SeoAuditCapability = cap_builtins.SeoAuditCapability
_.EditorialPipelineCapability = cap_builtins.EditorialPipelineCapability
_.RouteMonitorsCapability = cap_builtins.RouteMonitorsCapability

# Test fixtures (uvicorn.Server.should_exit shutdown flag)
_.should_exit

# M10 typed draft schema
_.question = contracts.FAQItem.question
_.answer = contracts.FAQItem.answer
_.button_text = contracts.DraftCTA.button_text
_.intensity = contracts.DraftCTA.intensity
_._coerce_legacy = contracts.FAQItem._coerce_legacy
_._require_uuid_source = contracts.RequiredFact._require_uuid_source
_._normalize_brief_funnel_stage = contracts.EditorialBrief._normalize_brief_funnel_stage
