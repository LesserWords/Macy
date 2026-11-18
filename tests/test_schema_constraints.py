import os
import uuid
from typing import Any

import psycopg
import pytest

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgres://app:app_secret@localhost:5432/macy?sslmode=disable"
)


def _create_user(cur: psycopg.Cursor[Any], email_prefix: str = "user") -> uuid.UUID:
    email = f"{email_prefix}-{uuid.uuid4().hex[:8]}@example.com"
    cur.execute(
        """
        INSERT INTO users (email, name, ui_locale)
        VALUES (%s, 'Test User', 'pt-BR')
        RETURNING id
        """,
        (email,),
    )
    row = cur.fetchone()
    assert row is not None
    assert isinstance(row[0], uuid.UUID)
    return row[0]


def _create_workspace(cur: psycopg.Cursor[Any], slug_prefix: str = "ws") -> uuid.UUID:
    slug = f"{slug_prefix}-{uuid.uuid4().hex[:8]}"
    cur.execute(
        """
        INSERT INTO workspaces (slug, name, industry_pack)
        VALUES (%s, 'Test Workspace', 'general')
        RETURNING id
        """,
        (slug,),
    )
    row = cur.fetchone()
    assert row is not None
    assert isinstance(row[0], uuid.UUID)
    return row[0]


def _create_campaign(
    cur: psycopg.Cursor[Any], workspace_id: uuid.UUID, user_id: uuid.UUID
) -> uuid.UUID:
    name = f"Campaign-{uuid.uuid4().hex[:8]}"
    cur.execute(
        """
        INSERT INTO campaigns (
            workspace_id, name, locale, market, articles_per_week,
            schedule_cron, monthly_cap_usd, run_cap_usd, config,
            config_schema_version, created_by
        )
        VALUES (
            %s, %s, 'pt-BR', 'BR', 5,
            '0 9 * * 1', 100.00, 10.00, '{"pillars": []}',
            1, %s
        )
        RETURNING id
        """,
        (workspace_id, name, user_id),
    )
    row = cur.fetchone()
    assert row is not None
    assert isinstance(row[0], uuid.UUID)
    return row[0]


def test_schema_v1_tables_exist(db_conn: psycopg.Connection[Any]) -> None:
    expected_tables = {
        "workspaces",
        "users",
        "memberships",
        "brand_profiles",
        "campaigns",
        "sources",
        "workspace_integrations",
        "webhook_subscriptions",
        "events",
        "webhook_deliveries",
        "usage_ledger",
        "llm_payloads",
        "runs",
        "documents",
        "research_records",
        "opportunities",
        "keyword_cache",
        "briefs",
    }
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        )
        existing_tables = {row[0] for row in cur.fetchall()}

    missing = expected_tables - existing_tables
    assert not missing, f"Missing Schema v1 tables: {missing}"


def test_app_role_cannot_update_or_delete_events(db_conn: psycopg.Connection[Any]) -> None:
    with db_conn.cursor() as cur:
        ws_id = _create_workspace(cur, "perm")

        # Inserting into events as app succeeds
        cur.execute(
            """
            INSERT INTO events (workspace_id, type, entity_type, entity_id, payload)
            VALUES (%s, 'test.event', 'workspace', %s, '{"hello":"world"}')
            RETURNING id
            """,
            (ws_id, ws_id),
        )
        row = cur.fetchone()
        assert row is not None
        event_id = row[0]

        # UPDATE on events must be denied for app role
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_info_update:
            cur.execute("UPDATE events SET type = 'tampered' WHERE id = %s", (event_id,))
        assert exc_info_update.value.sqlstate == "42501"

        # DELETE on events must be denied for app role
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as exc_info_delete:
            cur.execute("DELETE FROM events WHERE id = %s", (event_id,))
        assert exc_info_delete.value.sqlstate == "42501"


def test_composite_foreign_key_cross_tenant_rejection(db_conn: psycopg.Connection[Any]) -> None:
    with db_conn.cursor() as cur:
        ws_a = _create_workspace(cur, "ws-a")
        ws_b = _create_workspace(cur, "ws-b")
        user_id = _create_user(cur, "author")
        campaign_a_id = _create_campaign(cur, ws_a, user_id)

        # Inserting a run with ws_b but campaign from ws_a must fail composite FK
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as exc_info:
            cur.execute(
                """
                INSERT INTO runs (
                    workspace_id, campaign_id, trigger, status,
                    window_start, window_end, run_cap_usd, code_version, pack_version
                )
                VALUES (
                    %s, %s, 'scheduled', 'queued',
                    now(), now() + interval '1 day', 10.00, 'v1.0.0', 'general-v1'
                )
                """,
                (ws_b, campaign_a_id),
            )
        assert exc_info.value.sqlstate == "23503"

        # Inserting a run with matching ws_a and campaign_a_id must succeed
        cur.execute(
            """
            INSERT INTO runs (
                workspace_id, campaign_id, trigger, status,
                window_start, window_end, run_cap_usd, code_version, pack_version
            )
            VALUES (
                %s, %s, 'scheduled', 'queued',
                now(), now() + interval '1 day', 10.00, 'v1.0.0', 'general-v1'
            )
            RETURNING id
            """,
            (ws_a, campaign_a_id),
        )
        row = cur.fetchone()
        assert row is not None
        run_id = row[0]
        assert run_id is not None


def test_sources_unique_nulls_not_distinct(db_conn: psycopg.Connection[Any]) -> None:
    domain = f"news-{uuid.uuid4().hex[:8]}.com.br"
    with db_conn.cursor() as cur:
        # 1. First global source (workspace_id = NULL) succeeds
        cur.execute(
            """
            INSERT INTO sources (
                workspace_id, industry_pack, name, domain, url, fetch_mode,
                feed_url, tier, role, locales
            )
            VALUES (
                NULL, 'general', 'Global News', %s, 'https://' || %s, 'feed',
                'https://' || %s || '/feed', 1, 'news', ARRAY['pt-BR']
            )
            """,
            (domain, domain, domain),
        )

        # 2. Duplicate global source with workspace_id = NULL and same domain
        # violates UNIQUE NULLS NOT DISTINCT
        with pytest.raises(psycopg.errors.UniqueViolation) as exc_info_global:
            cur.execute(
                """
                INSERT INTO sources (
                    workspace_id, industry_pack, name, domain, url, fetch_mode,
                    feed_url, tier, role, locales
                )
                VALUES (
                    NULL, 'general', 'Global News Dupe', %s, 'https://' || %s, 'feed',
                    'https://' || %s || '/feed', 1, 'news', ARRAY['pt-BR']
                )
                """,
                (domain, domain, domain),
            )
        assert exc_info_global.value.sqlstate == "23505"

        # 3. Workspace-specific source with same domain succeeds (workspace overrides global)
        ws_id = _create_workspace(cur, "src-ws")
        cur.execute(
            """
            INSERT INTO sources (
                workspace_id, industry_pack, name, domain, url, fetch_mode,
                feed_url, tier, role, locales
            )
            VALUES (
                %s, 'general', 'Custom News', %s, 'https://' || %s, 'feed',
                'https://' || %s || '/feed', 1, 'news', ARRAY['pt-BR']
            )
            """,
            (ws_id, domain, domain, domain),
        )

        # 4. Duplicate within the same workspace fails
        with pytest.raises(psycopg.errors.UniqueViolation) as exc_info_ws:
            cur.execute(
                """
                INSERT INTO sources (
                    workspace_id, industry_pack, name, domain, url, fetch_mode,
                    feed_url, tier, role, locales
                )
                VALUES (
                    %s, 'general', 'Custom News Dupe', %s, 'https://' || %s, 'feed',
                    'https://' || %s || '/feed', 1, 'news', ARRAY['pt-BR']
                )
                """,
                (ws_id, domain, domain, domain),
            )
        assert exc_info_ws.value.sqlstate == "23505"


def test_runs_one_active_constraint(db_conn: psycopg.Connection[Any]) -> None:
    with db_conn.cursor() as cur:
        ws_id = _create_workspace(cur, "runs")
        user_id = _create_user(cur, "runner")
        campaign_id = _create_campaign(cur, ws_id, user_id)

        # 1. Insert first active run ('running')
        cur.execute(
            """
            INSERT INTO runs (
                workspace_id, campaign_id, trigger, status,
                window_start, window_end, run_cap_usd, code_version, pack_version
            )
            VALUES (
                %s, %s, 'scheduled', 'running',
                now(), now() + interval '1 day', 10.00, 'v1.0.0', 'general-v1'
            )
            RETURNING id
            """,
            (ws_id, campaign_id),
        )
        row1 = cur.fetchone()
        assert row1 is not None
        first_run_id = row1[0]

        # 2. Inserting a second active run ('queued') for the same campaign fails runs_one_active
        with pytest.raises(psycopg.errors.UniqueViolation) as exc_info:
            cur.execute(
                """
                INSERT INTO runs (
                    workspace_id, campaign_id, trigger, status,
                    window_start, window_end, run_cap_usd, code_version, pack_version
                )
                VALUES (
                    %s, %s, 'manual', 'queued',
                    now(), now() + interval '1 day', 10.00, 'v1.0.0', 'general-v1'
                )
                """,
                (ws_id, campaign_id),
            )
        assert exc_info.value.sqlstate == "23505"

        # 3. Transition first run to 'completed'
        cur.execute("UPDATE runs SET status = 'completed' WHERE id = %s", (first_run_id,))

        # 4. Now inserting a new active run ('queued') succeeds
        cur.execute(
            """
            INSERT INTO runs (
                workspace_id, campaign_id, trigger, status,
                window_start, window_end, run_cap_usd, code_version, pack_version
            )
            VALUES (
                %s, %s, 'manual', 'queued',
                now(), now() + interval '1 day', 10.00, 'v1.0.0', 'general-v1'
            )
            RETURNING id
            """,
            (ws_id, campaign_id),
        )
        row2 = cur.fetchone()
        assert row2 is not None
        second_run_id = row2[0]
        assert second_run_id is not None


def test_documents_canonical_url_and_status_constraints(
    db_conn: psycopg.Connection[Any],
) -> None:
    canonical = f"https://example.com/news/{uuid.uuid4().hex[:8]}"
    with db_conn.cursor() as cur:
        ws_a = _create_workspace(cur, "doc-ws-a")
        ws_b = _create_workspace(cur, "doc-ws-b")

        cur.execute(
            """
            INSERT INTO documents (
                workspace_id, url, canonical_url, title, date_status, fetch_status
            )
            VALUES (%s, %s, %s, 'Article Title', 'verified', 'ok')
            RETURNING id
            """,
            (ws_a, canonical + "?utm_source=x", canonical),
        )
        row = cur.fetchone()
        assert row is not None
        assert isinstance(row[0], uuid.UUID)

        # Duplicate canonical_url in the same workspace violates UNIQUE constraint
        with pytest.raises(psycopg.errors.UniqueViolation) as exc_dupe:
            cur.execute(
                """
                INSERT INTO documents (
                    workspace_id, url, canonical_url, title, date_status, fetch_status
                )
                VALUES (%s, %s, %s, 'Another Title', 'verified', 'ok')
                """,
                (ws_a, canonical, canonical),
            )
        assert exc_dupe.value.sqlstate == "23505"

        # Same canonical_url in a different workspace succeeds
        cur.execute(
            """
            INSERT INTO documents (
                workspace_id, url, canonical_url, title, date_status, fetch_status
            )
            VALUES (%s, %s, %s, 'WS B Title', 'unverified', 'ok')
            RETURNING id
            """,
            (ws_b, canonical, canonical),
        )
        assert cur.fetchone() is not None

        # Invalid date_status fails CHECK constraint
        with pytest.raises(psycopg.errors.CheckViolation) as exc_date:
            cur.execute(
                """
                INSERT INTO documents (
                    workspace_id, url, canonical_url, title, date_status, fetch_status
                )
                VALUES (%s, 'https://a.com', 'https://a.com/1', 'Title', 'bad_date', 'ok')
                """,
                (ws_a,),
            )
        assert exc_date.value.sqlstate == "23514"


def test_research_records_duplicate_and_composite_fk_constraints(
    db_conn: psycopg.Connection[Any],
) -> None:
    with db_conn.cursor() as cur:
        ws_a = _create_workspace(cur, "rr-ws-a")
        ws_b = _create_workspace(cur, "rr-ws-b")
        user_id = _create_user(cur, "rr-user")
        camp_a = _create_campaign(cur, ws_a, user_id)

        cur.execute(
            """
            INSERT INTO runs (
                workspace_id, campaign_id, trigger, status,
                window_start, window_end, run_cap_usd, code_version, pack_version
            )
            VALUES (
                %s, %s, 'manual', 'running',
                now(), now() + interval '1 day', 10.00, 'v1', 'p1'
            )
            RETURNING id
            """,
            (ws_a, camp_a),
        )
        run_row = cur.fetchone()
        assert run_row is not None
        run_a = run_row[0]

        cur.execute(
            """
            INSERT INTO documents (
                workspace_id, url, canonical_url, title, date_status, fetch_status
            )
            VALUES (%s, 'https://ex.com/1', %s, 'Doc 1', 'verified', 'ok')
            RETURNING id
            """,
            (ws_a, f"https://ex.com/{uuid.uuid4().hex}"),
        )
        doc1_row = cur.fetchone()
        assert doc1_row is not None
        doc_1 = doc1_row[0]

        cur.execute(
            """
            INSERT INTO documents (
                workspace_id, url, canonical_url, title, date_status, fetch_status
            )
            VALUES (%s, 'https://ex.com/2', %s, 'Doc 2', 'verified', 'ok')
            RETURNING id
            """,
            (ws_a, f"https://ex.com/{uuid.uuid4().hex}"),
        )
        doc2_row = cur.fetchone()
        assert doc2_row is not None
        doc_2 = doc2_row[0]

        # 1. Valid primary research_record (status='extracted', duplicate_of=NULL)
        cur.execute(
            """
            INSERT INTO research_records (
                workspace_id, run_id, document_id, status, schema_version, payload
            )
            VALUES (%s, %s, %s, 'extracted', 1, '{"headline": "H1"}')
            RETURNING id
            """,
            (ws_a, run_a, doc_1),
        )
        rr1_row = cur.fetchone()
        assert rr1_row is not None
        rr_1 = rr1_row[0]

        # 2. status='duplicate' with duplicate_of=NULL must fail CHECK constraint
        with pytest.raises(psycopg.errors.CheckViolation) as exc_dup_null:
            cur.execute(
                """
                INSERT INTO research_records (
                    workspace_id, run_id, document_id, duplicate_of, status, schema_version, payload
                )
                VALUES (%s, %s, %s, NULL, 'duplicate', 1, '{}')
                """,
                (ws_a, run_a, doc_2),
            )
        assert exc_dup_null.value.sqlstate == "23514"

        # 3. status='extracted' with duplicate_of IS NOT NULL must fail CHECK
        with pytest.raises(psycopg.errors.CheckViolation) as exc_nondup_ref:
            cur.execute(
                """
                INSERT INTO research_records (
                    workspace_id, run_id, document_id, duplicate_of, status, schema_version, payload
                )
                VALUES (%s, %s, %s, %s, 'extracted', 1, '{}')
                """,
                (ws_a, run_a, doc_2, rr_1),
            )
        assert exc_nondup_ref.value.sqlstate == "23514"

        # 4. status='duplicate' with duplicate_of=rr_1 succeeds
        cur.execute(
            """
            INSERT INTO research_records (
                workspace_id, run_id, document_id, duplicate_of, status, schema_version, payload
            )
            VALUES (%s, %s, %s, %s, 'duplicate', 1, '{}')
            RETURNING id
            """,
            (ws_a, run_a, doc_2, rr_1),
        )
        assert cur.fetchone() is not None

        # 5. Duplicate (run_id, document_id) violates UNIQUE (run_id, document_id)
        with pytest.raises(psycopg.errors.UniqueViolation) as exc_run_doc:
            cur.execute(
                """
                INSERT INTO research_records (
                    workspace_id, run_id, document_id, status, schema_version, payload
                )
                VALUES (%s, %s, %s, 'new', 1, '{}')
                """,
                (ws_a, run_a, doc_1),
            )
        assert exc_run_doc.value.sqlstate == "23505"

        # 6. Cross-tenant composite FK violation (ws_b referencing run_a/doc_3 in ws_a)
        cur.execute(
            """
            INSERT INTO documents (
                workspace_id, url, canonical_url, title, date_status, fetch_status
            )
            VALUES (%s, 'https://ex.com/3', %s, 'Doc 3', 'verified', 'ok')
            RETURNING id
            """,
            (ws_a, f"https://ex.com/{uuid.uuid4().hex}"),
        )
        doc3_row = cur.fetchone()
        assert doc3_row is not None
        doc_3 = doc3_row[0]

        with pytest.raises(psycopg.errors.ForeignKeyViolation) as exc_fk:
            cur.execute(
                """
                INSERT INTO research_records (
                    workspace_id, run_id, document_id, status, schema_version, payload
                )
                VALUES (%s, %s, %s, 'new', 1, '{}')
                """,
                (ws_b, run_a, doc_3),
            )
        assert exc_fk.value.sqlstate == "23503"


def _seed_brief_chain(
    cur: psycopg.Cursor[Any],
    prefix: str,
    user_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    ws_id = _create_workspace(cur, prefix)
    camp_id = _create_campaign(cur, ws_id, user_id)
    cur.execute(
        """
        INSERT INTO brand_profiles (
            workspace_id, version, is_current, schema_version, payload, created_by
        )
        VALUES (%s, 1, TRUE, 1, '{"voice": "Brand"}', %s) RETURNING id
        """,
        (ws_id, user_id),
    )
    brand_row = cur.fetchone()
    assert brand_row is not None
    cur.execute(
        """
        INSERT INTO runs (
            workspace_id, campaign_id, trigger, status,
            window_start, window_end, run_cap_usd, code_version, pack_version
        )
        VALUES (%s, %s, 'manual', 'running', now(), now() + interval '1 day', 10, 'v1', 'p1')
        RETURNING id
        """,
        (ws_id, camp_id),
    )
    run_row = cur.fetchone()
    assert run_row is not None
    cur.execute(
        """
        INSERT INTO documents (
            workspace_id, url, canonical_url, title, date_status, fetch_status
        )
        VALUES (%s, 'https://br.com/1', %s, 'Doc', 'verified', 'ok')
        RETURNING id
        """,
        (ws_id, f"https://{prefix}.com/{uuid.uuid4().hex}"),
    )
    doc_row = cur.fetchone()
    assert doc_row is not None
    cur.execute(
        """
        INSERT INTO research_records (
            workspace_id, run_id, document_id, status, schema_version, payload
        )
        VALUES (%s, %s, %s, 'verified', 1, '{}') RETURNING id
        """,
        (ws_id, run_row[0], doc_row[0]),
    )
    rr_row = cur.fetchone()
    assert rr_row is not None
    cur.execute(
        """
        INSERT INTO opportunities (
            workspace_id, run_id, record_id, scores, total, funnel_stage,
            confidence, angle, status, decided_by, decided_at
        )
        VALUES (%s, %s, %s, '{}', 85, 'mofu', 'high', 'Angle', 'approved', %s, now())
        RETURNING id
        """,
        (ws_id, run_row[0], rr_row[0], user_id),
    )
    opp_row = cur.fetchone()
    assert opp_row is not None
    return ws_id, brand_row[0], opp_row[0]


def test_briefs_version_uniqueness_and_cross_tenant_fk(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Verify briefs CHECK (version > 0), UNIQUE (opportunity_id, version), and composite FKs."""
    with db_conn.cursor() as cur:
        user_id = _create_user(cur, "br-user")
        ws_a, brand_a, opp_a = _seed_brief_chain(cur, "br-ws-a", user_id)
        ws_b, brand_b, opp_b = _seed_brief_chain(cur, "br-ws-b", user_id)

        # 1. version <= 0 violates CHECK (version > 0)
        with pytest.raises(psycopg.errors.CheckViolation) as exc_chk:
            cur.execute(
                """
                INSERT INTO briefs (
                    workspace_id, opportunity_id, version, brand_profile_id,
                    prompt_version, keywords, schema_version, payload
                )
                VALUES (%s, %s, 0, %s, 'brief.md:v1', '[]', 1, '{}')
                """,
                (ws_a, opp_a, brand_a),
            )
        assert exc_chk.value.sqlstate == "23514"

        # 2. Valid brief v1 in workspace A
        cur.execute(
            """
            INSERT INTO briefs (
                workspace_id, opportunity_id, version, brand_profile_id,
                prompt_version, keywords, schema_version, payload
            )
            VALUES (%s, %s, 1, %s, 'brief.md:v1', '[]', 1, '{"topic": "A"}')
            RETURNING id
            """,
            (ws_a, opp_a, brand_a),
        )
        assert cur.fetchone() is not None

        # 3. Duplicate (opportunity_id, version) raises UniqueViolation
        with pytest.raises(psycopg.errors.UniqueViolation) as exc_dup:
            cur.execute(
                """
                INSERT INTO briefs (
                    workspace_id, opportunity_id, version, brand_profile_id,
                    prompt_version, keywords, schema_version, payload
                )
                VALUES (%s, %s, 1, %s, 'brief.md:v1', '[]', 1, '{}')
                """,
                (ws_a, opp_a, brand_a),
            )
        assert exc_dup.value.sqlstate == "23505"

        # 4. Cross-tenant FK: (ws_b, opp_a) fails composite FK
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as exc_fk_opp:
            cur.execute(
                """
                INSERT INTO briefs (
                    workspace_id, opportunity_id, version, brand_profile_id,
                    prompt_version, keywords, schema_version, payload
                )
                VALUES (%s, %s, 2, %s, 'brief.md:v1', '[]', 1, '{}')
                """,
                (ws_b, opp_a, brand_b),
            )
        assert exc_fk_opp.value.sqlstate == "23503"

        # 5. Cross-tenant FK: (ws_a, brand_b) fails composite FK
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as exc_fk_brand:
            cur.execute(
                """
                INSERT INTO briefs (
                    workspace_id, opportunity_id, version, brand_profile_id,
                    prompt_version, keywords, schema_version, payload
                )
                VALUES (%s, %s, 2, %s, 'brief.md:v1', '[]', 1, '{}')
                """,
                (ws_a, opp_a, brand_b),
            )
        assert exc_fk_brand.value.sqlstate == "23503"

        # 6. Independent workspace B brief v1 succeeds
        cur.execute(
            """
            INSERT INTO briefs (
                workspace_id, opportunity_id, version, brand_profile_id,
                prompt_version, keywords, schema_version, payload
            )
            VALUES (%s, %s, 1, %s, 'brief.md:v1', '[]', 1, '{"topic": "B"}')
            RETURNING id
            """,
            (ws_b, opp_b, brand_b),
        )
        assert cur.fetchone() is not None


def _seed_draft_chain(
    cur: psycopg.Cursor[Any],
    prefix: str,
    user_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    ws_id, brand_id, opp_id = _seed_brief_chain(cur, prefix, user_id)
    cur.execute(
        """
        SELECT document_id
        FROM research_records r
        JOIN opportunities o ON o.record_id = r.id AND o.workspace_id = r.workspace_id
        WHERE o.workspace_id = %s AND o.id = %s
        """,
        (ws_id, opp_id),
    )
    doc_row = cur.fetchone()
    assert doc_row is not None
    cur.execute(
        """
        INSERT INTO briefs (
            workspace_id, opportunity_id, version, brand_profile_id,
            prompt_version, keywords, schema_version, payload
        )
        VALUES (%s, %s, 1, %s, 'brief.md:v1', '[]', 1, '{"topic": "T"}')
        RETURNING id
        """,
        (ws_id, opp_id, brand_id),
    )
    brief_row = cur.fetchone()
    assert brief_row is not None
    return ws_id, doc_row[0], brief_row[0]


def test_drafts_claims_and_quality_reports_constraints(
    db_conn: psycopg.Connection[Any],
) -> None:
    """Verify F13/F14 DB constraints on drafts, draft_claims, and quality_reports."""
    with db_conn.cursor() as cur:
        user_id = _create_user(cur, "dr-user")
        ws_a, doc_a, brief_a = _seed_draft_chain(cur, "dr-ws-a", user_id)
        ws_b, doc_b, brief_b = _seed_draft_chain(cur, "dr-ws-b", user_id)

        # 1. Valid draft revisions 0, 1 succeed
        draft_a_id: uuid.UUID | None = None
        for rev in (0, 1):
            cur.execute(
                """
                INSERT INTO drafts (
                    workspace_id, brief_id, revision, status,
                    prompt_version, schema_version, payload
                )
                VALUES (%s, %s, %s, 'draft', 'write_blog.md:v1', 1, '{"title": "D"}')
                RETURNING id
                """,
                (ws_a, brief_a, rev),
            )
            row = cur.fetchone()
            assert row is not None
            if rev == 0:
                draft_a_id = row[0]
        assert draft_a_id is not None

        # 2. Cross-tenant (ws_b, brief_a) at unused revision 2 violates composite FK -> 23503
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as exc_fk_brief:
            cur.execute(
                """
                INSERT INTO drafts (
                    workspace_id, brief_id, revision, status,
                    prompt_version, schema_version, payload
                )
                VALUES (%s, %s, 2, 'draft', 'write_blog.md:v1', 1, '{}')
                """,
                (ws_b, brief_a),
            )
        assert exc_fk_brief.value.sqlstate == "23503"

        # 3. Valid revision 2 in workspace A succeeds
        cur.execute(
            """
            INSERT INTO drafts (
                workspace_id, brief_id, revision, status,
                prompt_version, schema_version, payload
            )
            VALUES (%s, %s, 2, 'draft', 'write_blog.md:v1', 1, '{"title": "D2"}')
            """,
            (ws_a, brief_a),
        )

        # 4. Revision 3 violates CHECK (revision BETWEEN 0 AND 2) -> SQLSTATE 23514
        with pytest.raises(psycopg.errors.CheckViolation) as exc_rev3:
            cur.execute(
                """
                INSERT INTO drafts (
                    workspace_id, brief_id, revision, status,
                    prompt_version, schema_version, payload
                )
                VALUES (%s, %s, 3, 'draft', 'write_blog.md:v1', 1, '{}')
                """,
                (ws_a, brief_a),
            )
        assert exc_rev3.value.sqlstate == "23514"

        # 5. Duplicate (brief_id, revision) violates UNIQUE -> SQLSTATE 23505
        with pytest.raises(psycopg.errors.UniqueViolation) as exc_dup_rev:
            cur.execute(
                """
                INSERT INTO drafts (
                    workspace_id, brief_id, revision, status,
                    prompt_version, schema_version, payload
                )
                VALUES (%s, %s, 0, 'draft', 'write_blog.md:v1', 1, '{}')
                """,
                (ws_a, brief_a),
            )
        assert exc_dup_rev.value.sqlstate == "23505"

        # 5. A 'fact' claim without document_id is refused by DB -> SQLSTATE 23514 (F13 AC2)
        with pytest.raises(psycopg.errors.CheckViolation) as exc_fact_null:
            cur.execute(
                """
                INSERT INTO draft_claims (
                    workspace_id, draft_id, idx, kind, text, document_id
                )
                VALUES (%s, %s, 0, 'fact', 'Unsourced statistic', NULL)
                """,
                (ws_a, draft_a_id),
            )
        assert exc_fact_null.value.sqlstate == "23514"

        # 6. A 'fact' claim with valid workspace document_id and 'analysis' without doc succeed
        cur.execute(
            """
            INSERT INTO draft_claims (
                workspace_id, draft_id, idx, kind, text, document_id
            )
            VALUES
                (%s, %s, 0, 'fact', 'Sourced statistic', %s),
                (%s, %s, 1, 'analysis', 'Analytical takeaway', NULL),
                (%s, %s, 2, 'recommendation', 'Recommended action', NULL)
            """,
            (ws_a, draft_a_id, doc_a, ws_a, draft_a_id, ws_a, draft_a_id),
        )

        # 7. Cross-tenant document_id (ws_a, doc_b) violates composite FK -> SQLSTATE 23503
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as exc_fk_doc:
            cur.execute(
                """
                INSERT INTO draft_claims (
                    workspace_id, draft_id, idx, kind, text, document_id
                )
                VALUES (%s, %s, 3, 'fact', 'Cross-tenant citation', %s)
                """,
                (ws_a, draft_a_id, doc_b),
            )
        assert exc_fk_doc.value.sqlstate == "23503"

        # 8. quality_reports valid insert + invalid verdict CHECK + cross-tenant FK
        cur.execute(
            """
            INSERT INTO quality_reports (
                workspace_id, draft_id, verdict, score, checks, findings,
                evaluator_model, prompt_version
            )
            VALUES (%s, %s, 'pass', 92.5, '{"lang": true}', '[]', 'claude', 'evaluate.md:v1')
            """,
            (ws_a, draft_a_id),
        )
        with pytest.raises(psycopg.errors.CheckViolation) as exc_qr_verdict:
            cur.execute(
                """
                INSERT INTO drafts (
                    workspace_id, brief_id, revision, status,
                    prompt_version, schema_version, payload
                )
                VALUES (%s, %s, 0, 'draft', 'write_blog.md:v1', 1, '{}')
                RETURNING id
                """,
                (ws_b, brief_b),
            )
            draft_b_row = cur.fetchone()
            assert draft_b_row is not None
            cur.execute(
                """
                INSERT INTO quality_reports (
                    workspace_id, draft_id, verdict, checks, findings,
                    evaluator_model, prompt_version
                )
                VALUES (%s, %s, 'invalid_verdict', '{}', '[]', 'claude', 'evaluate.md:v1')
                """,
                (ws_b, draft_b_row[0]),
            )
        assert exc_qr_verdict.value.sqlstate == "23514"


def test_publications_publishing_constraints(db_conn: psycopg.Connection[Any]) -> None:
    """M4-05: publications allows nullable url, error column, draft FK, and unique draft+dest."""
    with db_conn.cursor() as cur:
        user_id = _create_user(cur, "pub-user")
        ws_a, _doc_a, brief_a = _seed_draft_chain(cur, "pub-a", user_id)
        ws_b = _create_workspace(cur, "pub-b")

        cur.execute(
            """
            INSERT INTO drafts (
                workspace_id, brief_id, revision, status,
                prompt_version, schema_version, payload
            )
            VALUES (%s, %s, 0, 'approved', 'write.md:v1', 1, '{}')
            RETURNING id
            """,
            (ws_a, brief_a),
        )
        draft_row = cur.fetchone()
        assert draft_row is not None
        draft_id = draft_row[0]

        # Failed publication with nullable url + error succeeds
        cur.execute(
            """
            INSERT INTO publications (
                workspace_id, draft_id, destination, mode, status, url, title, error
            )
            VALUES (%s, %s, 'http_webhook', 'draft', 'failed', NULL, 'Title', 'HTTP 500')
            """,
            (ws_a, draft_id),
        )

        # Duplicate (workspace_id, draft_id, destination) violates UNIQUE -> SQLSTATE 23505
        with pytest.raises(psycopg.errors.UniqueViolation) as exc_dup:
            cur.execute(
                """
                INSERT INTO publications (
                    workspace_id, draft_id, destination, mode, status, url, title
                )
                VALUES (
                    %s, %s, 'http_webhook', 'draft', 'published', 'https://x.com/1', 'Title'
                )
                """,
                (ws_a, draft_id),
            )
        assert exc_dup.value.sqlstate == "23505"

        # Cross-tenant (ws_b, draft_id) violates composite FK -> SQLSTATE 23503
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as exc_fk:
            cur.execute(
                """
                INSERT INTO publications (
                    workspace_id, draft_id, destination, mode, status, url, title
                )
                VALUES (
                    %s, %s, 'http_webhook', 'draft', 'published', 'https://b.com/1', 'Title'
                )
                """,
                (ws_b, draft_id),
            )
        assert exc_fk.value.sqlstate == "23503"
