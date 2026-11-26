import uuid

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError


def test_contracts_importable() -> None:
    from engine import contracts

    assert hasattr(contracts, "ResearchRecord")
    assert hasattr(contracts, "Opportunity")
    assert hasattr(contracts, "EditorialBrief")
    assert hasattr(contracts, "ContentDraft")
    assert hasattr(contracts, "QualityReport")
    assert hasattr(contracts, "PublishRequest")
    assert hasattr(contracts, "PublishedContent")
    assert hasattr(contracts, "BrandProfile")
    assert hasattr(contracts, "CampaignConfig")
    assert hasattr(contracts, "KeywordData")


def test_campaign_config_default_weights_sum_to_100() -> None:
    from engine.contracts import CampaignConfig

    config = CampaignConfig()
    weights = config.scoring_weights
    total = (
        weights.novelty
        + weights.audience_fit
        + weights.brand_fit
        + weights.competitor_gap
        + weights.trend
        + weights.urgency
    )
    assert total == 100
    assert config.schema_version == 1


def test_campaign_config_invalid_weights_rejected() -> None:
    from engine.contracts import CampaignConfig, ScoringWeights

    with pytest.raises(ValidationError, match="Weights must sum to 100"):
        CampaignConfig(
            scoring_weights=ScoringWeights(
                novelty=40,
                audience_fit=20,
                brand_fit=20,
                competitor_gap=15,
                trend=10,
                urgency=5,  # sums to 110
            )
        )


@given(
    novelty=st.integers(min_value=0, max_value=100),
    audience_fit=st.integers(min_value=0, max_value=100),
    brand_fit=st.integers(min_value=0, max_value=100),
    competitor_gap=st.integers(min_value=0, max_value=100),
    trend=st.integers(min_value=0, max_value=100),
    urgency=st.integers(min_value=0, max_value=100),
)
def test_hypothesis_campaign_config_weights(  # noqa: PLR0913
    novelty: int,
    audience_fit: int,
    brand_fit: int,
    competitor_gap: int,
    trend: int,
    urgency: int,
) -> None:
    from engine.contracts import CampaignConfig, ScoringWeights

    total = novelty + audience_fit + brand_fit + competitor_gap + trend + urgency
    if total == 100:
        config = CampaignConfig(
            scoring_weights=ScoringWeights(
                novelty=novelty,
                audience_fit=audience_fit,
                brand_fit=brand_fit,
                competitor_gap=competitor_gap,
                trend=trend,
                urgency=urgency,
            )
        )
        assert config.scoring_weights.total == 100
    else:
        with pytest.raises(ValidationError):
            CampaignConfig(
                scoring_weights=ScoringWeights(
                    novelty=novelty,
                    audience_fit=audience_fit,
                    brand_fit=brand_fit,
                    competitor_gap=competitor_gap,
                    trend=trend,
                    urgency=urgency,
                )
            )


def test_brand_profile_prohibited_phrases_regex_validation() -> None:
    from engine.contracts import BrandProfile

    # Valid regexes pass
    profile = BrandProfile(
        voice="Empathetic, clear, clinical-yet-accessible",
        positioning="Leading optical retail intelligence",
        capabilities=["Inventory control"],
        prohibited_claims=["100% guaranteed profit"],
        prohibited_phrases=[r"\bcheapest glasses\b", r"\bguaranteed cure\b"],
        cta_rules=["TOFU: educational only"],
    )
    assert len(profile.prohibited_phrases) == 2
    assert profile.schema_version == 1

    # Invalid regex raises ValidationError naming the field
    with pytest.raises(ValidationError) as exc_info:
        BrandProfile(
            voice="Valid Voice",
            positioning="Valid Positioning",
            prohibited_phrases=["[unclosed regex"],
        )
    assert "prohibited_phrases" in str(exc_info.value)

    # Empty voice or positioning raises ValidationError naming the field
    with pytest.raises(ValidationError) as exc_voice:
        BrandProfile(voice="   ", positioning="Valid Positioning")
    assert "voice" in str(exc_voice.value)

    with pytest.raises(ValidationError) as exc_pos:
        BrandProfile(voice="Valid Voice", positioning="")
    assert "positioning" in str(exc_pos.value)

    # Empty item in cta_rules raises ValidationError naming cta_rules
    with pytest.raises(ValidationError) as exc_cta:
        BrandProfile(
            voice="Valid Voice",
            positioning="Valid Positioning",
            cta_rules=["   "],
        )
    assert "cta_rules" in str(exc_cta.value)


def test_draft_claims_fact_requires_source_id() -> None:
    from engine.contracts import ContentDraft, DraftClaim

    # Analysis or recommendation without source_id passes
    draft = ContentDraft(
        title="Sample Title",
        meta_description="Sample Meta",
        slug="sample-title",
        body_md="# Content",
        cta={"copy": "Click here", "url": "https://example.com"},
        claims=[DraftClaim(text="Market looks bullish", kind="analysis")],
    )
    assert len(draft.claims) == 1

    # Fact without source_id fails
    with pytest.raises(ValidationError, match="Facts must have a source_id"):
        ContentDraft(
            title="Sample Title",
            meta_description="Sample Meta",
            slug="sample-title",
            body_md="# Content",
            cta={"copy": "Click here", "url": "https://example.com"},
            claims=[DraftClaim(text="72% of users prefer dark mode", kind="fact")],
        )

    # Fact with source_id succeeds
    sample_doc_id = "00000000-0000-0000-0000-000000000123"
    valid_fact_draft = ContentDraft(
        title="Sample Title",
        meta_description="Sample Meta",
        slug="sample-title",
        body_md="# Content",
        cta={"copy": "Click here", "url": "https://example.com"},
        claims=[
            DraftClaim(
                text="72% of users prefer dark mode",
                kind="fact",
                source_id=sample_doc_id,
            )
        ],
    )
    assert valid_fact_draft.claims[0].source_id == sample_doc_id


def test_all_contracts_serialization_and_schema_version() -> None:
    from engine.contracts import (
        ContentDraft,
        EditorialBrief,
        Finding,
        KeywordData,
        Opportunity,
        PublishedContent,
        PublishRequest,
        QualityReport,
        RequiredFact,
        ResearchRecord,
        ScoringWeights,
        SubScore,
    )

    r = ResearchRecord(
        headline="New Frame Material",
        source="Optometry Journal",
        url="https://optometry.example/article",
        summary="Summary of frame material",
    )
    assert r.schema_version == 1
    assert r.model_dump()["schema_version"] == 1

    opp = Opportunity(
        title="Titanium Frames Guide",
        angle="Durability vs Weight",
        funnel_stage="MOFU",
        confidence=0.85,
        novelty=SubScore(score=80.0, evidence="New alloy"),
        audience_fit=SubScore(score=90.0, evidence="High interest"),
        brand_fit=SubScore(score=85.0, evidence="Core catalog"),
        competitor_gap=SubScore(score=70.0, evidence="Few guides"),
        trend=SubScore(score=60.0, evidence="Growing"),
        urgency=SubScore(score=50.0, evidence="Season starts"),
    )
    assert opp.schema_version == 1
    weighted_total = opp.compute_weighted_total()
    assert weighted_total > 0
    # Custom weights
    custom_weights = ScoringWeights(
        novelty=20,
        audience_fit=20,
        brand_fit=20,
        competitor_gap=20,
        trend=10,
        urgency=10,
    )
    assert opp.compute_weighted_total(custom_weights) > 0

    brief = EditorialBrief(
        topic="Titanium Frames",
        target_audience="Eyewear buyers",
        primary_keyword="titanium frames",
        angle="Complete guide to durability",
        required_facts=[
            RequiredFact(
                fact="Titanium is 40% lighter",
                source_id="00000000-0000-0000-0000-000000000001",
            )
        ],
        cta_goal="Visit catalog",
    )
    assert brief.schema_version == 1

    report = QualityReport(
        verdict="PASS",
        findings=[
            Finding(
                criterion="Accuracy",
                severity="minor",
                detail="Check year",
                responsible_module="research",
            )
        ],
    )
    assert report.schema_version == 1

    draft = ContentDraft(
        title="Title",
        meta_description="Meta",
        slug="title",
        body_md="# Body",
        cta={"copy": "Explore", "url": "https://example.com"},
    )
    pub_req = PublishRequest(
        draft_id=uuid.uuid4(),
        draft=draft,
        locale="pt-BR",
        destination="http_webhook",
    )
    assert pub_req.schema_version == 1

    pub_res = PublishedContent(
        destination="http_webhook",
        external_id="ext-999",
        status="published",
    )
    assert pub_res.schema_version == 1

    kw = KeywordData(
        keyword="armacao de oculos de titanio",
        search_volume=1200,
        difficulty=35.0,
    )
    assert kw.schema_version == 1


def test_opportunity_confidence_and_funnel_stage_fallbacks() -> None:
    from engine.contracts import Opportunity, SubScore

    sub = SubScore(score=7.0, evidence="Evidence")
    opp_high = Opportunity(
        title="T",
        angle="A",
        funnel_stage="BOFU",
        confidence="high",
        novelty=sub,
        audience_fit=sub,
        brand_fit=sub,
        competitor_gap=sub,
        trend=sub,
        urgency=sub,
    )
    assert opp_high.confidence_level == "high"
    assert opp_high.funnel_stage == "bofu"

    opp_float_high = Opportunity(
        title="T",
        angle="A",
        funnel_stage="invalid_stage",
        confidence=0.85,
        novelty=sub,
        audience_fit=sub,
        brand_fit=sub,
        competitor_gap=sub,
        trend=sub,
        urgency=sub,
    )
    assert opp_float_high.confidence_level == "high"
    assert opp_float_high.funnel_stage == "mofu"

    opp_float_med = Opportunity(
        title="T",
        angle="A",
        funnel_stage=None,
        confidence=0.55,
        novelty=sub,
        audience_fit=sub,
        brand_fit=sub,
        competitor_gap=sub,
        trend=sub,
        urgency=sub,
    )
    assert opp_float_med.confidence_level == "medium"
    assert opp_float_med.funnel_stage == "mofu"

    opp_float_low = Opportunity(
        title="T",
        angle="A",
        funnel_stage="tofu",
        confidence=0.2,
        novelty=sub,
        audience_fit=sub,
        brand_fit=sub,
        competitor_gap=sub,
        trend=sub,
        urgency=sub,
    )
    assert opp_float_low.confidence_level == "low"
    assert opp_float_low.funnel_stage == "tofu"

    opp_invalid = Opportunity(
        title="T",
        angle="A",
        funnel_stage="mofu",
        confidence="not_a_valid_float_or_label",
        novelty=sub,
        audience_fit=sub,
        brand_fit=sub,
        competitor_gap=sub,
        trend=sub,
        urgency=sub,
    )
    assert opp_invalid.confidence_level == "medium"
