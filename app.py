"""Streamlit Admin UI entry point (ADR-014, F19)."""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from datetime import datetime
from typing import Any

import streamlit as st
from pydantic import ValidationError

from engine import auth, bootstrap, capabilities, i18n, migrations, observability, services
from engine.__main__ import start_embedded_worker
from engine.contracts import CampaignConfig, CrawlConfig, ScoringWeights
from engine.i18n import _


def _init_user_locale(user: dict[str, Any]) -> str:
    """Initialize active UI locale from session, user record, or HTTP headers."""
    if hasattr(st, "session_state") and "ui_locale" in st.session_state:
        loc = str(st.session_state.ui_locale)
        i18n.set_current_locale(loc)
        return loc

    accept_header: str | None = None
    with contextlib.suppress(Exception):
        accept_header = st.context.headers.get("accept-language")

    persisted_locale = str(user.get("ui_locale") or "pt-BR")
    resolved = i18n.resolve_initial_locale(accept_header, fallback=persisted_locale)
    i18n.set_current_locale(resolved)
    return resolved


def filter_nav_labels(
    nav_labels: dict[str, str],
    *,
    enabled_capabilities: list[str] | None,
) -> dict[str, str]:
    """Filter Admin UI sidebar items according to workspace enabled capabilities (ADR-029)."""
    allowed = capabilities.allowed_ui_screens(enabled_capabilities)
    return {key: label for key, label in nav_labels.items() if key in allowed}


def _render_sidebar(
    user: dict[str, Any],
    workspace: dict[str, Any],
    memberships: list[dict[str, Any]] | None = None,
) -> str:
    """Render sidebar chrome with navigation, language switcher, and user info."""
    auth.render_user_header(user, workspace)

    if memberships and len(memberships) > 1:
        ws_map = {
            str(m["workspace_id"]): str(m.get("workspace_name") or m.get("name") or m.get("slug"))
            for m in memberships
        }
        current_ws_id = str(workspace["workspace_id"])
        ws_keys = list(ws_map.keys())
        current_idx = ws_keys.index(current_ws_id) if current_ws_id in ws_keys else 0
        selected_ws_id = st.sidebar.selectbox(
            _("Workspace"),
            options=ws_keys,
            index=current_idx,
            format_func=lambda k: ws_map[k],
            key="workspace_selector",
        )
        if selected_ws_id != current_ws_id:
            st.session_state["active_workspace_id"] = selected_ws_id
            st.rerun()

    st.sidebar.divider()
    current_loc = i18n.get_current_locale()
    locale_options = ["pt-BR", "en-US"]
    selected_idx = 0 if current_loc == "pt-BR" else 1

    selected_loc = st.sidebar.selectbox(
        _("Language"),
        options=locale_options,
        index=selected_idx,
        format_func=lambda x: "Português (Brasil)" if x == "pt-BR" else "English (US)",
        key="language_selector",
    )

    if selected_loc != current_loc:
        services.set_user_ui_locale(
            user_id=user["user_id"],
            ui_locale=selected_loc,
            workspace_id=workspace["workspace_id"],
        )
        i18n.set_current_locale(selected_loc)
        st.rerun()

    st.sidebar.divider()
    nav_labels: dict[str, str] = {
        "dashboard": _("Dashboard"),
        "report": _("Weekly Report"),
        "draft_review": _("Draft Review"),
        "campaigns": _("Campaigns"),
        "brand_profile": _("Brand Profile"),
        "sources": _("Sources"),
        "integrations": _("Integrations"),
        "webhooks": _("Webhooks"),
        "runs": _("Runs"),
        "audit": "SEO & GEO Audit",
    }
    raw_caps = (workspace.get("config") or {}).get("enabled_capabilities")
    enabled_caps = raw_caps if isinstance(raw_caps, list) else None
    nav_labels = filter_nav_labels(nav_labels, enabled_capabilities=enabled_caps)
    selected_page = st.sidebar.radio(
        _("Navigation"),
        options=list(nav_labels.keys()),
        format_func=lambda k: nav_labels.get(k, k),
        key="main_nav",
    )
    return selected_page or "dashboard"


def _render_scoring_weights(
    weights_data: dict[str, Any],
    defaults: ScoringWeights,
) -> dict[str, int]:
    """Render scoring weight number inputs and return selected values."""
    st.markdown(f"**{_('Scoring Weights (must sum to 100)')}**")
    return {
        "novelty": st.number_input(
            _("Weight: Novelty"),
            min_value=0,
            max_value=100,
            value=int(weights_data.get("novelty", defaults.novelty)),
            step=1,
            key="weight_novelty",
        ),
        "audience_fit": st.number_input(
            _("Weight: Audience Fit"),
            min_value=0,
            max_value=100,
            value=int(weights_data.get("audience_fit", defaults.audience_fit)),
            step=1,
            key="weight_audience_fit",
        ),
        "brand_fit": st.number_input(
            _("Weight: Brand Fit"),
            min_value=0,
            max_value=100,
            value=int(weights_data.get("brand_fit", defaults.brand_fit)),
            step=1,
            key="weight_brand_fit",
        ),
        "competitor_gap": st.number_input(
            _("Weight: Competitor Gap"),
            min_value=0,
            max_value=100,
            value=int(weights_data.get("competitor_gap", defaults.competitor_gap)),
            step=1,
            key="weight_competitor_gap",
        ),
        "trend": st.number_input(
            _("Weight: Trend"),
            min_value=0,
            max_value=100,
            value=int(weights_data.get("trend", defaults.trend)),
            step=1,
            key="weight_trend",
        ),
        "urgency": st.number_input(
            _("Weight: Urgency"),
            min_value=0,
            max_value=100,
            value=int(weights_data.get("urgency", defaults.urgency)),
            step=1,
            key="weight_urgency",
        ),
    }


def _extract_campaign_defaults(existing: dict[str, Any] | None) -> dict[str, Any]:
    """Extract form defaults from an existing campaign row or contract defaults."""
    default_cfg = CampaignConfig()
    if existing is None:
        return {
            "id": None,
            "name": "Default Campaign",
            "locale": "pt-BR",
            "market": "BR",
            "volume": 1,
            "cron": "0 9 * * 1",
            "monthly_cap": "",
            "run_cap": "",
            "goal": default_cfg.goal,
            "weights_data": {},
            "default_weights": default_cfg.scoring_weights,
        }

    cfg_data = dict(existing.get("config") or {})
    return {
        "id": existing["id"],
        "name": str(existing["name"]),
        "locale": str(existing["locale"]),
        "market": str(existing["market"]),
        "volume": int(existing["articles_per_week"]),
        "cron": str(existing["schedule_cron"]),
        "monthly_cap": f"{existing['monthly_cap_usd']:.2f}",
        "run_cap": f"{existing['run_cap_usd']:.2f}",
        "goal": str(cfg_data.get("goal") or default_cfg.goal),
        "weights_data": dict(cfg_data.get("scoring_weights") or {}),
        "default_weights": default_cfg.scoring_weights,
    }


def _render_campaign_settings(user: dict[str, Any], workspace: dict[str, Any]) -> None:
    """Render Campaign Settings screen with live monthly cost estimation (F02, F17)."""
    st.subheader(_("Campaign Settings"))
    existing = services.get_active_campaign(
        workspace_id=workspace["workspace_id"],
        user_id=user["user_id"],
    )
    defaults = _extract_campaign_defaults(existing)

    name = st.text_input(_("Campaign Name"), value=defaults["name"], key="campaign_name")
    locale = st.text_input(_("Content Locale"), value=defaults["locale"], key="campaign_locale")
    market = st.text_input(_("Target Market"), value=defaults["market"], key="campaign_market")
    volume = int(
        st.number_input(
            _("Articles per Week"),
            min_value=1,
            max_value=20,
            value=defaults["volume"],
            step=1,
            key="campaign_articles_per_week",
        )
    )
    cron = st.text_input(
        _("Run Schedule (Cron)"), value=defaults["cron"], key="campaign_schedule_cron"
    )
    monthly_cap = st.text_input(
        _("Monthly Spend Cap (USD)"),
        value=defaults["monthly_cap"],
        key="campaign_monthly_cap",
    )
    run_cap = st.text_input(
        _("Per-Run Spend Cap (USD)"),
        value=defaults["run_cap"],
        key="campaign_run_cap",
    )
    goal = st.text_input(_("Campaign Goal"), value=defaults["goal"], key="campaign_goal")
    weights = _render_scoring_weights(defaults["weights_data"], defaults["default_weights"])

    est_cost = services.estimate_monthly_cost(volume)
    st.markdown(f"**{_('Estimated Monthly Cost')}:** {i18n.format_currency_usd(est_cost)}")

    if st.button(_("Save Campaign"), key="save_campaign_btn"):
        try:
            services.save_campaign(
                workspace_id=workspace["workspace_id"],
                user_id=user["user_id"],
                campaign_id=defaults["id"],
                name=name,
                locale=locale,
                market=market,
                articles_per_week=volume,
                schedule_cron=cron,
                monthly_cap_usd=monthly_cap,
                run_cap_usd=run_cap,
                config={"goal": goal, "scoring_weights": weights},
            )
            st.success(_("Campaign settings saved successfully."))
        except ValueError as exc:
            st.error(str(exc))


def _split_lines(raw: str) -> list[str]:
    """Split multiline input into trimmed non-empty lines."""
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _extract_brand_defaults(current: dict[str, Any] | None) -> dict[str, Any]:
    """Extract form defaults from the current brand profile row."""
    if current is None:
        return {
            "version": None,
            "voice": "",
            "positioning": "",
            "capabilities": "",
            "prohibited_claims": "",
            "prohibited_phrases": "",
            "cta_rules": "",
        }
    payload = dict(current.get("payload") or {})
    return {
        "version": int(current["version"]),
        "voice": str(payload.get("voice") or ""),
        "positioning": str(payload.get("positioning") or ""),
        "capabilities": "\n".join(str(x) for x in (payload.get("capabilities") or [])),
        "prohibited_claims": "\n".join(str(x) for x in (payload.get("prohibited_claims") or [])),
        "prohibited_phrases": "\n".join(str(x) for x in (payload.get("prohibited_phrases") or [])),
        "cta_rules": "\n".join(str(x) for x in (payload.get("cta_rules") or [])),
    }


def _render_fixture_loader(user: dict[str, Any], workspace: dict[str, Any]) -> None:
    fixtures = services.list_available_brand_fixtures()
    if not fixtures:
        return
    fixture_names = list(fixtures.keys())
    chosen_fixture = st.selectbox(
        _("Built-in / Context Fixture"),
        options=fixture_names,
        key="brand_fixture_select",
    )
    if st.button(_("Load Selected Brand Fixture"), key="load_brand_fixture_btn"):
        try:
            yaml_content = fixtures[chosen_fixture].read_text(encoding="utf-8")
            services.import_brand_fixture_yaml(
                workspace_id=workspace["workspace_id"],
                user_id=user["user_id"],
                yaml_text=yaml_content,
            )
            st.success(_("Brand fixture imported successfully."))
            st.rerun()
        except (ValueError, ValidationError) as exc:
            st.error(str(exc))


def _render_yaml_upload_export(
    user: dict[str, Any],
    workspace: dict[str, Any],
    current: dict[str, Any] | None,
) -> None:
    uploaded_yaml = st.file_uploader(
        _("Upload Brand YAML"),
        type=["yaml", "yml"],
        key="brand_yaml_upload",
    )
    if uploaded_yaml is not None and st.button(
        _("Import Uploaded Brand YAML"), key="import_brand_yaml_btn"
    ):
        try:
            services.import_brand_fixture_yaml(
                workspace_id=workspace["workspace_id"],
                user_id=user["user_id"],
                yaml_text=uploaded_yaml.getvalue().decode("utf-8"),
            )
            st.success(_("Brand fixture imported successfully."))
            st.rerun()
        except (ValueError, ValidationError, UnicodeDecodeError) as exc:
            st.error(str(exc))

    if current is not None:
        exported = services.export_brand_fixture_yaml(
            workspace_id=workspace["workspace_id"],
            user_id=user["user_id"],
        )
        st.download_button(
            _("Export Brand YAML"),
            data=exported,
            file_name=f"seed_{workspace['slug']}.yaml",
            mime="application/x-yaml",
            key="export_brand_yaml_btn",
        )


def _render_brand_profile(user: dict[str, Any], workspace: dict[str, Any]) -> None:
    """Render Brand Profile screen with versioning and per-field validation errors (F03)."""
    st.subheader(_("Brand Profile"))
    current = services.current_brand(
        workspace_id=workspace["workspace_id"],
        user_id=user["user_id"],
    )
    with st.expander(_("Import / Export Brand YAML"), expanded=False):
        _render_fixture_loader(user, workspace)
        _render_yaml_upload_export(user, workspace, current)

    defaults = _extract_brand_defaults(current)
    if defaults["version"] is not None:
        st.markdown(f"**{_('Current Version')}:** v{defaults['version']}")

    voice = st.text_area(_("Brand Voice"), value=defaults["voice"], key="brand_voice")
    positioning = st.text_area(
        _("Positioning"), value=defaults["positioning"], key="brand_positioning"
    )
    capabilities = st.text_area(
        _("Capabilities (one per line)"),
        value=defaults["capabilities"],
        key="brand_capabilities",
    )
    prohibited_claims = st.text_area(
        _("Prohibited Claims (one per line)"),
        value=defaults["prohibited_claims"],
        key="brand_prohibited_claims",
    )
    prohibited_phrases = st.text_area(
        _("Prohibited Phrases (regex, one per line)"),
        value=defaults["prohibited_phrases"],
        key="brand_prohibited_phrases",
    )
    cta_rules = st.text_area(
        _("CTA Rules (one per line)"),
        value=defaults["cta_rules"],
        key="brand_cta_rules",
    )

    if st.button(_("Save Brand Profile"), key="save_brand_btn"):
        try:
            saved = services.save_brand(
                workspace_id=workspace["workspace_id"],
                user_id=user["user_id"],
                profile={
                    "voice": voice,
                    "positioning": positioning,
                    "capabilities": _split_lines(capabilities),
                    "prohibited_claims": _split_lines(prohibited_claims),
                    "prohibited_phrases": _split_lines(prohibited_phrases),
                    "cta_rules": _split_lines(cta_rules),
                },
            )
            st.markdown(f"**{_('Current Version')}:** v{saved['version']}")
            st.success(_("Brand profile saved successfully."))
        except ValidationError as exc:
            for err in exc.errors():
                loc = err.get("loc")
                field_name = str(loc[0]) if loc else "profile"
                st.error(f"{field_name}: {err['msg']}")
        except ValueError as exc:
            st.error(str(exc))


def _split_csv(raw: str) -> list[str]:
    """Split comma-separated string into trimmed non-empty items."""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _extract_source_defaults(target: dict[str, Any] | None) -> dict[str, Any]:
    """Extract form defaults for a source row or new source."""
    if target is None:
        return {
            "id": None,
            "name": "",
            "domain": "",
            "url": "",
            "fetch_mode": "feed",
            "feed_url": "",
            "tier": 1,
            "role": "news",
            "locales": "pt-BR",
            "topics": "",
            "enabled": True,
        }
    return {
        "id": uuid.UUID(str(target["id"])),
        "name": str(target["name"]),
        "domain": str(target["domain"]),
        "url": str(target["url"]),
        "fetch_mode": str(target["fetch_mode"]),
        "feed_url": str(target.get("feed_url") or ""),
        "tier": int(target["tier"]),
        "role": str(target["role"]),
        "locales": ", ".join(str(x) for x in (target.get("locales") or ["pt-BR"])),
        "topics": ", ".join(str(x) for x in (target.get("topics") or [])),
        "enabled": bool(target.get("enabled", True)),
    }


def _sync_selected_source_state(selected_id: str, defaults: dict[str, Any]) -> None:
    """Sync session_state form inputs when the user switches the selected source."""
    if not hasattr(st, "session_state"):
        return
    if st.session_state.get("_last_selected_source_id") == selected_id:
        return
    st.session_state["_last_selected_source_id"] = selected_id
    st.session_state["source_name"] = defaults["name"]
    st.session_state["source_domain"] = defaults["domain"]
    st.session_state["source_url"] = defaults["url"]
    st.session_state["source_fetch_mode"] = defaults["fetch_mode"]
    st.session_state["source_feed_url"] = defaults["feed_url"]
    st.session_state["source_tier"] = defaults["tier"]
    st.session_state["source_role"] = defaults["role"]
    st.session_state["source_locales"] = defaults["locales"]
    st.session_state["source_topics"] = defaults["topics"]
    st.session_state["source_enabled"] = defaults["enabled"]


def _render_sources_list(
    sources: list[dict[str, Any]],
    user: dict[str, Any],
    workspace: dict[str, Any],
) -> None:
    """Render existing sources with enable/disable buttons."""
    for src in sources:
        status_label = _("Enabled") if src["enabled"] else _("Disabled")
        st.markdown(
            f"**{src['name']}** (`{src['domain']}`) — {src['role']} · "
            f"Tier {src['tier']} · {src['fetch_mode']} · {status_label}"
        )
        btn_label = _("Disable") if src["enabled"] else _("Enable")
        if st.button(btn_label, key=f"toggle_source_{src['id']}"):
            services.set_source_enabled(
                workspace_id=workspace["workspace_id"],
                user_id=user["user_id"],
                source_id=uuid.UUID(str(src["id"])),
                enabled=not bool(src["enabled"]),
            )
            st.rerun()


def _render_sources(user: dict[str, Any], workspace: dict[str, Any]) -> None:
    """Render Trusted Sources screen with role help, add/edit form, and toggle (F04)."""
    st.subheader(_("Trusted Sources"))
    st.markdown(
        _(
            "Role guide: **news** and **company** sources provide topic candidates; "
            "**data** and **management** sources provide statistics and operational "
            "context at brief time."
        )
    )

    sources = services.list_sources(
        workspace_id=workspace["workspace_id"],
        user_id=user["user_id"],
    )
    by_id: dict[str, dict[str, Any]] = {str(s["id"]): s for s in sources}
    options = ["", *by_id.keys()]
    selected_id = st.selectbox(
        _("Select Source to Edit"),
        options=options,
        format_func=lambda k: (
            "Add New Source" if not k else f"{by_id[k]['name']} ({by_id[k]['domain']})"
        ),
        key="source_edit_selector",
    )

    defaults = _extract_source_defaults(by_id.get(selected_id) if selected_id else None)
    _sync_selected_source_state(selected_id, defaults)

    name = st.text_input(_("Source Name"), value=defaults["name"], key="source_name")
    domain = st.text_input(_("Source Domain"), value=defaults["domain"], key="source_domain")
    url = st.text_input(_("Source URL"), value=defaults["url"], key="source_url")
    fetch_modes = ["feed", "site_search"]
    fetch_mode = st.selectbox(
        _("Fetch Mode"),
        options=fetch_modes,
        index=0 if defaults["fetch_mode"] == "feed" else 1,
        key="source_fetch_mode",
    )
    feed_url = st.text_input(_("Feed URL"), value=defaults["feed_url"], key="source_feed_url")
    tiers = [1, 2, 3]
    tier = int(
        st.selectbox(
            _("Source Tier"),
            options=tiers,
            index=tiers.index(defaults["tier"]) if defaults["tier"] in tiers else 0,
            key="source_tier",
        )
    )
    roles = ["news", "company", "data", "management"]
    role = st.selectbox(
        _("Source Role"),
        options=roles,
        index=roles.index(defaults["role"]) if defaults["role"] in roles else 0,
        key="source_role",
    )
    locales_raw = st.text_input(
        _("Source Locales (comma-separated)"),
        value=defaults["locales"],
        key="source_locales",
    )
    topics_raw = st.text_input(
        _("Source Topics (comma-separated)"),
        value=defaults["topics"],
        key="source_topics",
    )
    enabled = st.checkbox(_("Source Enabled"), value=defaults["enabled"], key="source_enabled")

    if st.button(_("Save Source"), key="save_source_btn"):
        try:
            services.save_source(
                workspace_id=workspace["workspace_id"],
                user_id=user["user_id"],
                source_id=defaults["id"],
                name=name,
                domain=domain,
                url=url,
                fetch_mode=fetch_mode,
                feed_url=feed_url or None,
                tier=tier,
                role=role,
                locales=_split_csv(locales_raw),
                topics=_split_csv(topics_raw),
                enabled=enabled,
            )
            st.success(_("Source saved successfully."))
        except ValueError as exc:
            st.error(str(exc))

    st.divider()
    _render_sources_list(sources, user, workspace)


def _render_stage_models_inputs(
    port: str,
    plugin: str,
    current_val: Any,
    default_val: Any,
) -> dict[str, str]:
    """Render per-stage LLM model text inputs and return the mapping (F05 AC5)."""
    st.markdown(f"**{_('LLM Models by Stage')}**")
    base_map: dict[str, str] = {}
    if isinstance(default_val, dict):
        base_map.update({str(k): str(v) for k, v in default_val.items()})
    if isinstance(current_val, dict):
        base_map.update({str(k): str(v) for k, v in current_val.items()})

    stage_models: dict[str, str] = {}
    for stage in services.LLM_STAGES:
        stage_models[stage] = st.text_input(
            _("Model for stage: {stage}").format(stage=stage),
            value=base_map.get(stage, ""),
            key=f"int_stage_{port}_{plugin}_{stage}",
        )
    return stage_models


def _render_single_schema_field(
    port: str,
    plugin: str,
    spec: dict[str, Any],
    saved_settings: dict[str, Any],
    masked_secrets: dict[str, str],
) -> tuple[bool, Any]:
    """Render one schema field widget and return (is_secret, value)."""
    fname = str(spec["name"])
    title = str(spec["title"])
    widget = str(spec["widget"])
    default_val = spec.get("default")
    cur_val = saved_settings.get(fname, default_val)

    if widget == "password":
        if fname in masked_secrets:
            st.markdown(f"**{title}:** `{masked_secrets[fname]}` ({_('Secret saved')})")
        nonce = (
            int(st.session_state.get("_secret_input_nonce", 0))
            if hasattr(st, "session_state")
            else 0
        )
        widget_key = (
            f"int_secret_{port}_{plugin}_{fname}"
            if nonce == 0
            else f"int_secret_{port}_{plugin}_{fname}_{nonce}"
        )
        secret_val = st.text_input(
            title,
            value="",
            type="password",
            key=widget_key,
        )
        return True, secret_val

    field_key = f"int_field_{port}_{plugin}_{fname}"
    if widget == "stage_models":
        return False, _render_stage_models_inputs(port, plugin, cur_val, default_val)
    if widget == "select":
        opts = list(spec.get("options") or [])
        idx = opts.index(cur_val) if cur_val in opts else 0
        return False, st.selectbox(title, options=opts, index=idx, key=field_key)
    if widget == "checkbox":
        return False, st.checkbox(title, value=bool(cur_val), key=field_key)
    if widget == "number":
        if spec.get("field_type") == "integer":
            return False, int(
                st.number_input(title, value=int(cur_val or 0), step=1, key=field_key)
            )
        return False, float(
            st.number_input(title, value=float(cur_val or 0.0), step=0.05, key=field_key)
        )
    if widget == "json":
        raw_json = json.dumps(cur_val if isinstance(cur_val, dict) else (default_val or {}))
        edited_json = st.text_area(title, value=raw_json, key=field_key)
        return False, json.loads(edited_json or "{}")
    return False, st.text_input(title, value=str(cur_val or ""), key=field_key)


def _rotate_secret_input_nonce(port: str, plugin: str, specs: list[dict[str, Any]]) -> None:
    """Purge old secret widget keys and bump nonce so password inputs reset cleanly (F05 AC2)."""
    if not hasattr(st, "session_state"):
        return
    old_nonce = int(st.session_state.get("_secret_input_nonce", 0))
    for spec in specs:
        if spec.get("is_secret"):
            old_key = (
                f"int_secret_{port}_{plugin}_{spec['name']}"
                if old_nonce == 0
                else f"int_secret_{port}_{plugin}_{spec['name']}_{old_nonce}"
            )
            with contextlib.suppress(Exception):
                del st.session_state[old_key]
    st.session_state["_secret_input_nonce"] = old_nonce + 1
    st.rerun()


def _render_integration_presets(user: dict[str, Any], workspace: dict[str, Any]) -> None:
    """Render quick-select stack preset controls above individual port forms (ADR-031)."""
    presets = services.list_integration_presets()
    preset_names = [str(p["name"]) for p in presets]
    labels_by_name = {str(p["name"]): f"{p['label']} — {p['name']}" for p in presets}
    st.markdown(f"#### {_('Stack Presets')}")
    selected_preset = st.selectbox(
        _("Quick Stack Preset"),
        options=preset_names,
        format_func=lambda n: labels_by_name.get(n, n),
        key="integration_preset_select",
    )
    selected_meta = next((p for p in presets if p["name"] == selected_preset), None)
    if selected_meta is not None:
        st.caption(str(selected_meta["description"]))
    if st.button(_("Apply Stack Preset"), key="apply_integration_preset_btn"):
        services.apply_integration_preset(
            workspace_id=workspace["workspace_id"],
            user_id=user["user_id"],
            preset_name=selected_preset,
        )
        st.success(_("Stack preset applied successfully."))


def _render_integrations(user: dict[str, Any], workspace: dict[str, Any]) -> None:
    """Render Integrations configuration screen from plugin Settings JSON Schema (F05)."""
    st.subheader(_("Integrations"))

    if hasattr(st, "session_state") and st.session_state.pop("_integration_saved_flash", False):
        st.success(_("Integration saved successfully."))

    _render_integration_presets(user, workspace)

    port_plugins = services.list_port_plugins()
    configured_list = services.list_integrations(
        workspace_id=workspace["workspace_id"],
        user_id=user["user_id"],
    )
    by_port: dict[str, dict[str, Any]] = {str(r["port"]): r for r in configured_list}

    ports = list(port_plugins.keys())
    selected_port = st.selectbox(_("Integration Port"), options=ports, key="integration_port")
    available_plugins = [p["plugin"] for p in port_plugins.get(selected_port, [])]

    current_row = by_port.get(selected_port)
    default_plugin_idx = (
        available_plugins.index(current_row["plugin"])
        if current_row and current_row["plugin"] in available_plugins
        else 0
    )
    selected_plugin = st.selectbox(
        _("Plugin"),
        options=available_plugins,
        index=default_plugin_idx,
        key="integration_plugin",
    )

    plugin_meta: dict[str, Any] = next(
        (p for p in port_plugins.get(selected_port, []) if p["plugin"] == selected_plugin),
        {"fields": []},
    )
    fields: list[dict[str, Any]] = plugin_meta["fields"]
    saved_settings = (
        dict(current_row["settings"])
        if current_row and current_row["plugin"] == selected_plugin
        else {}
    )
    masked_secrets = (
        dict(current_row["masked_secrets"])
        if current_row and current_row["plugin"] == selected_plugin
        else {}
    )

    form_settings: dict[str, Any] = {}
    form_secrets: dict[str, Any] = {}
    for spec in fields:
        is_sec, val = _render_single_schema_field(
            selected_port, selected_plugin, spec, saved_settings, masked_secrets
        )
        if is_sec:
            form_secrets[str(spec["name"])] = val
        else:
            form_settings[str(spec["name"])] = val

    if st.button(_("Save Integration"), key="save_integration_btn"):
        try:
            services.save_integration(
                workspace_id=workspace["workspace_id"],
                user_id=user["user_id"],
                port=selected_port,
                plugin=selected_plugin,
                settings=form_settings,
                secrets=form_secrets,
            )
            if hasattr(st, "session_state"):
                st.session_state["_integration_saved_flash"] = True
            _rotate_secret_input_nonce(selected_port, selected_plugin, fields)
        except (ValidationError, ValueError) as exc:
            st.error(str(exc))

    if st.button(_("Test Connection"), key="test_integration_btn"):
        try:
            res = services.test_integration(
                workspace_id=workspace["workspace_id"],
                user_id=user["user_id"],
                port=selected_port,
            )
            if res["ok"]:
                st.success(_("Connection test succeeded."))
        except Exception as exc:
            st.error(str(exc))


def _render_webhook_form(user: dict[str, Any], workspace: dict[str, Any]) -> None:
    st.markdown(f"#### {_('Add Webhook Subscription')}")
    url = st.text_input(
        _("Target URL"),
        placeholder="https://example.com/webhook",
        key="wh_url_input",
    )
    all_events = [
        "topics.awaiting_approval",
        "topic.decided",
        "brief.created",
        "run.completed",
        "run.failed",
        "budget.cap_reached",
        "draft.awaiting_approval",
        "draft.decided",
        "article.published",
    ]
    selected_events = st.multiselect(
        _("Event Types"),
        options=all_events,
        default=["topics.awaiting_approval", "topic.decided", "run.completed"],
        key="wh_events_multiselect",
    )
    enabled = st.checkbox(_("Enabled"), value=True, key="wh_enabled_checkbox")

    if st.button(_("Create Webhook Subscription"), key="wh_create_btn"):
        if not url:
            st.error(_("Target URL is required."))
            return
        if not selected_events:
            st.error(_("At least one event type must be selected."))
            return
        try:
            res = services.save_subscription(
                workspace_id=workspace["workspace_id"],
                user_id=user["user_id"],
                url=url,
                event_types=selected_events,
                enabled=enabled,
            )
            st.session_state["created_webhook_secret"] = res["secret"]
            st.success(_("Webhook subscription created!"))
            st.rerun()
        except Exception as exc:
            st.error(str(exc))


def _render_webhook_subscriptions_list(user: dict[str, Any], workspace: dict[str, Any]) -> None:
    subs = services.list_subscriptions(
        workspace_id=workspace["workspace_id"],
        user_id=user["user_id"],
    )
    if not subs:
        st.info(_("No webhook subscriptions configured yet."))
        return

    st.markdown(f"#### {_('Active Subscriptions')}")
    for sub in subs:
        with st.container():
            c1, c2, c3 = st.columns([3, 3, 1])
            with c1:
                st.markdown(f"**`{sub['url']}`**")
                events_str = ", ".join(sub.get("event_types", []))
                st.caption(f"{_('Events:')} {events_str}")
            with c2:
                status_label = _("Enabled") if sub.get("enabled") else _("Disabled")
                st.write(f"{_('Status:')} **{status_label}**")
            with c3:
                if st.button(_("Delete"), key=f"del_wh_{sub['id']}"):
                    services.delete_subscription(
                        workspace_id=workspace["workspace_id"],
                        subscription_id=sub["id"],
                        user_id=user["user_id"],
                    )
                    st.success(_("Subscription deleted."))
                    st.rerun()
            st.divider()


def _render_webhook_deliveries(user: dict[str, Any], workspace: dict[str, Any]) -> None:
    deliveries = services.list_deliveries(
        workspace_id=workspace["workspace_id"],
        user_id=user["user_id"],
        limit=50,
    )
    if not deliveries:
        st.info(_("No webhook deliveries recorded yet."))
        return

    for item in deliveries:
        with st.container():
            c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
            with c1:
                st.markdown(f"**`{item['event_type']}`** → `{item['subscription_url']}`")
                if item.get("last_error"):
                    st.caption(f"{_('Error:')} {item['last_error']}")
            with c2:
                status = item["status"]
                st.write(f"{_('Status:')} **{status.upper()}**")
                if item.get("last_http_status"):
                    st.caption(f"HTTP {item['last_http_status']}")
            with c3:
                st.write(f"{_('Attempts:')} {item.get('attempts', 0)}")
                if item.get("delivered_at"):
                    st.caption(f"{_('Delivered:')} {item['delivered_at']}")
            with c4:
                btn_key = f"resend_{item['subscription_id']}_{item['event_id']}"
                if st.button(_("Re-send"), key=btn_key):
                    try:
                        services.resend(
                            workspace_id=workspace["workspace_id"],
                            subscription_id=item["subscription_id"],
                            event_id=item["event_id"],
                            user_id=user["user_id"],
                            execute_immediately=True,
                        )
                        st.success(_("Delivery re-sent."))
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
            st.divider()


def _render_webhooks(user: dict[str, Any], workspace: dict[str, Any]) -> None:
    st.subheader(_("Outbound Webhooks"))
    st.write(
        _(
            "Subscribe external HTTP endpoints to engine lifecycle events. "
            "Requests are signed using Standard Webhooks HMAC-SHA256."
        )
    )

    if st.session_state.get("created_webhook_secret"):
        secret = st.session_state["created_webhook_secret"]
        st.warning(_("Signing Secret (copy now, it will not be shown again):"))
        st.code(secret)
        if st.button(_("I have saved the secret"), key="ack_secret_btn"):
            del st.session_state["created_webhook_secret"]
            st.rerun()

    tab_subs, tab_logs = st.tabs([_("Subscriptions"), _("Delivery Log")])
    with tab_subs:
        _render_webhook_form(user, workspace)
        st.divider()
        _render_webhook_subscriptions_list(user, workspace)
    with tab_logs:
        _render_webhook_deliveries(user, workspace)


def _render_runs(user: dict[str, Any], workspace: dict[str, Any]) -> None:
    """Render run history and trigger execution (F18 AC1-AC5, F17 AC2-AC4)."""
    st.subheader(_("Runs & Orchestration"))
    campaign = services.get_active_campaign(
        workspace_id=workspace["workspace_id"],
        user_id=user["user_id"],
    )
    if not campaign:
        st.warning(_("No active campaign configured for this workspace."))
        return

    camp_id = campaign["id"]
    camp_name = campaign.get("name", "Default Campaign")
    st.markdown(f"**{_('Campaign')}:** {camp_name}")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"**{_('Run Schedule (Cron)')}:** `{campaign.get('schedule_cron', 'N/A')}`")
    with col2:
        st.markdown(
            f"**{_('Per-Run Spend Cap (USD)')}:** "
            f"{i18n.format_currency_usd(float(campaign.get('run_cap_usd', 0)))}"
        )
    with col3:
        st.markdown(
            f"**{_('Monthly Spend Cap (USD)')}:** "
            f"{i18n.format_currency_usd(float(campaign.get('monthly_cap_usd', 0)))}"
        )

    st.divider()

    if st.button(_("Run now"), key="btn_run_now", type="primary"):
        from engine.budget import MonthlyCapReached
        from engine.pipeline import ActiveRunExistsError

        try:
            run_id = services.run_now(
                workspace_id=workspace["workspace_id"],
                campaign_id=camp_id,
                user_id=user["user_id"],
            )
            st.success(_("Run triggered successfully: {run_id}").format(run_id=run_id))
        except ActiveRunExistsError:
            st.warning(_("An active run is already queued or running for this campaign."))
        except MonthlyCapReached:
            st.error(_("Monthly budget cap reached. New runs are refused."))
        except Exception as exc:
            st.error(str(exc))

    st.markdown(f"### {_('Run History')}")
    runs = services.list_runs(
        workspace_id=workspace["workspace_id"],
        user_id=user["user_id"],
        campaign_id=camp_id,
    )

    if not runs:
        st.info(_("No runs recorded yet."))
        return

    table_rows: list[dict[str, Any]] = []
    for r in runs:
        cost_val = float(r.get("cost_usd") or 0.0)
        table_rows.append(
            {
                _("Run ID"): str(r["id"])[:8] + "...",
                _("Trigger"): r.get("trigger", "manual"),
                _("Status"): r.get("status", "unknown"),
                _("Stage"): r.get("stage", "research"),
                _("Cost"): i18n.format_currency_usd(cost_val),
                _("Created"): str(r.get("created_at") or "")[:19],
            }
        )

    st.dataframe(table_rows, use_container_width=True)

    latest_run = runs[0]

    def _on_view_report_clicked(run_id_str: str) -> None:
        st.session_state["selected_report_run_id"] = run_id_str
        st.session_state["main_nav"] = "report"

    st.button(
        _("View Weekly Report"),
        key="btn_view_report_from_runs",
        on_click=_on_view_report_clicked,
        args=(str(latest_run["id"]),),
    )
    st.divider()
    _render_cost_view(user, workspace, campaign_id=camp_id)


def _render_cost_view(
    user: dict[str, Any],
    workspace: dict[str, Any],
    *,
    campaign_id: uuid.UUID | None = None,
) -> None:
    """Render spend-to-date vs caps, cost per run, and cost per published article (F17 AC5, AC6)."""
    summary = services.cost_summary(
        workspace_id=workspace["workspace_id"],
        user_id=user["user_id"],
        campaign_id=campaign_id,
    )
    st.markdown(f"### {_('Cost & Spend Overview')}")

    m_spend = i18n.format_currency_usd(float(summary["monthly_spend_usd"]))
    m_cap = i18n.format_currency_usd(float(summary["monthly_cap_usd"]))
    m_rem = i18n.format_currency_usd(float(summary["monthly_remaining_usd"]))
    r_cap = i18n.format_currency_usd(float(summary["run_cap_usd"]))
    avg_run = i18n.format_currency_usd(float(summary["average_cost_per_run_usd"]))
    avg_art = i18n.format_currency_usd(float(summary["average_cost_per_published_article_usd"]))
    pilot_cap = i18n.format_currency_usd(float(summary["pilot_target_usd"]))

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**{_('Current Month Spend')}:** {m_spend} / {m_cap}")
        st.markdown(f"**{_('Remaining Monthly Budget')}:** {m_rem}")
        st.markdown(f"**{_('Per-Run Spend Cap (USD)')}:** {r_cap}")
    with c2:
        st.markdown(f"**{_('Average Cost per Run')}:** {avg_run}")
        st.markdown(f"**{_('Average Cost per Published Article')}:** {avg_art}")
        st.markdown(f"**{_('Pilot Target per Article')}:** < {pilot_cap}")

    st.markdown(f"#### {_('Cost per Run')}")
    runs_list = summary["runs"]
    if not runs_list:
        st.caption(_("No runs recorded yet."))
    else:
        run_lines = [
            (
                f"| {_('Run ID')} | {_('Trigger')} | {_('Status')} | "
                f"{_('Calls')} | {_('Published Articles')} | {_('Cost')} |"
            ),
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for r in runs_list:
            rid = str(r["run_id"])[:8] + "..."
            trig = str(r.get("trigger") or "manual")
            stat = str(r.get("status") or "unknown")
            calls = int(r.get("calls") or 0)
            pubs = int(r.get("published_articles_count") or 0)
            rcost = i18n.format_currency_usd(float(r.get("cost_usd") or 0.0))
            run_lines.append(f"| `{rid}` | {trig} | {stat} | {calls} | {pubs} | {rcost} |")
        st.markdown("\n".join(run_lines))

    st.markdown(f"#### {_('Cost per Published Article')}")
    articles_list = summary["published_articles"]
    if not articles_list:
        st.caption(_("No published articles recorded yet."))
        return

    art_lines = [
        (f"| {_('Title')} | {_('Destination')} | {_('Cost')} | {_('Pilot Target')} |"),
        "| --- | --- | --- | --- |",
    ]
    for art in articles_list:
        title = str(art.get("title") or "").replace("|", "\\|")
        dest = str(art.get("destination") or "")
        acost = i18n.format_currency_usd(float(art.get("cost_usd") or 0.0))
        target_ok = f"< {pilot_cap}" if art.get("under_pilot_target") else f">= {pilot_cap}"
        art_lines.append(f"| {title} | `{dest}` | {acost} | {target_ok} |")
    st.markdown("\n".join(art_lines))


def _render_opportunity_card(
    item: dict[str, Any],
    user: dict[str, Any],
    workspace: dict[str, Any],
) -> None:
    """Render single opportunity card in weekly report (F09 AC3, AC4, F10 AC1-AC5)."""
    with st.container():
        is_rec = item.get("is_recommended", False)
        rank_num = item.get("rank", 1)
        if is_rec:
            st.markdown(f"#### :star: {_('Recommended Topic (#{rank})').format(rank=rank_num)}")
        else:
            st.markdown(f"#### {_('Topic Opportunity (#{rank})').format(rank=rank_num)}")

        st.markdown(f"**{item.get('title', 'Opportunity')}**")

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown(f"**{_('Angle')}:** {item.get('angle', 'N/A')}")
        with c2:
            st.markdown(f"**{_('Funnel Stage')}:** {str(item.get('funnel_stage', '')).upper()}")
        with c3:
            total_val = float(item.get("total") or 0.0)
            st.markdown(f"**{_('Total Score')}:** `{total_val:.1f}/100`")
        with c4:
            st.markdown(f"**{_('Confidence')}:** {str(item.get('confidence', '')).title()}")

        pub_at = item.get("published_at")
        date_str = i18n.format_date(pub_at) if pub_at else _("N/A")
        if item.get("is_verified_date"):
            date_label = f"{date_str} ({_('Verified')})"
        else:
            date_label = f"{date_str} ({_('Unverified')})"

        d1, d2 = st.columns(2)
        with d1:
            st.markdown(f"**{_('Publication Date')}:** {date_label}")
        with d2:
            orig_url = item.get("original_url", "")
            if orig_url:
                st.markdown(f"**{_('Source')}:** [{_('View Original')}]({orig_url})")

        st.markdown(f"**{_('Summary')}:** {item.get('summary', '')}")

        scores = item.get("scores") or {}
        crit_keys = (
            "novelty",
            "audience_fit",
            "brand_fit",
            "competitor_gap",
            "trend",
            "urgency",
        )
        with st.expander(_("Evaluation Scores & Evidence")):
            for crit_key in crit_keys:
                crit = scores.get(crit_key) or {}
                sc_val = crit.get("score", 0.0)
                ev_text = crit.get("evidence", "No evidence recorded.")
                est_label = f" {_('(Estimated)')}" if crit.get("is_estimate") else ""
                crit_name = crit_key.replace("_", " ").title()
                st.markdown(f"- **{crit_name}**: `{sc_val}/10`{est_label} — {ev_text}")

        # Gate 1 Decision UI (F10 AC1-AC5)
        _render_opportunity_actions(item, user, workspace)

        st.divider()


def _use_offline_brief_mode(workspace_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    if os.getenv("APP_ENV") != "test":
        return False
    configured = services.list_integrations(workspace_id=workspace_id, user_id=user_id)
    has_llm = any(r.get("port") == "llm" for r in configured) or bool(
        os.getenv("OPENROUTER_API_KEY")
    )
    return not has_llm


def _format_brief_keywords(brief_row: dict[str, Any]) -> list[str]:
    keywords = brief_row.get("keywords") or []
    payload = brief_row.get("payload") or {}
    lines: list[str] = []
    if keywords:
        for kw in keywords:
            if not isinstance(kw, dict):
                continue
            term = str(kw.get("keyword", "")).strip()
            if not term:
                continue
            vol = int(kw.get("search_volume") or 0)
            est_badge = f" {_('(Estimated)')}" if kw.get("is_estimate") else ""
            vol_label = f"{vol:,}" if vol > 0 else "0"
            lines.append(f"- `{term}` — **{_('Search Volume')}:** `{vol_label}`{est_badge}")
    if not lines and payload.get("primary_keyword"):
        lines.append(
            f"- `{payload['primary_keyword']}` — **{_('Search Volume')}:** `0` {_('(Estimated)')}"
        )
    return lines


def _render_brief_panel(brief_row: dict[str, Any]) -> None:
    payload = brief_row.get("payload") or {}
    version = int(brief_row.get("version") or 1)
    brand_ver = int(payload.get("brand_version") or 1)
    fmt = str(payload.get("format") or "blog")

    with st.expander(_("Editorial Brief (v{version})").format(version=version), expanded=True):
        st.caption(f"**{_('Brand Version')}:** v{brand_ver} · **{_('Format')}:** `{fmt}`")
        st.markdown(f"**{_('Topic')}:** {payload.get('topic', '')}")
        st.markdown(f"**{_('Target Audience')}:** {payload.get('target_audience', '')}")
        st.markdown(f"**{_('Angle')}:** {payload.get('angle', '')}")

        kw_lines = _format_brief_keywords(brief_row)
        if kw_lines:
            st.markdown(f"**{_('Target Keywords')}:**")
            for line in kw_lines:
                st.markdown(line)

        outline = payload.get("outline") or []
        if outline:
            st.markdown(f"**{_('Outline')}:**")
            for idx, heading in enumerate(outline, start=1):
                st.markdown(f"{idx}. {heading}")

        st.markdown(f"**{_('CTA Goal')}:** {payload.get('cta_goal', '')}")

        facts = payload.get("required_facts") or []
        if facts:
            st.markdown(f"**{_('Verified Facts')}:**")
            for fact_item in facts:
                if isinstance(fact_item, dict):
                    src_short = str(fact_item.get("source_id", ""))[:8]
                    st.markdown(f"- {fact_item.get('fact', '')} (`{src_short}...`)")


def _open_draft_review(draft_id_str: str) -> None:
    st.session_state["selected_review_draft_id"] = draft_id_str
    st.session_state["main_nav"] = "draft_review"


def _render_approved_brief_draft_controls(
    item: dict[str, Any],
    latest_brief: dict[str, Any],
    user: dict[str, Any],
    workspace: dict[str, Any],
) -> None:
    latest_draft = item.get("latest_draft")
    if latest_draft is not None and latest_draft.get("status") in {
        "pass",
        "approved",
        "declined",
        "published",
    }:
        st.button(
            _("Open Draft Review"),
            key=f"open_draft_review_{item['id']}",
            on_click=_open_draft_review,
            args=(str(latest_draft["id"]),),
        )
        return

    if latest_draft is not None and latest_draft.get("status") == "reject":
        st.warning(_("Latest draft rejected by evaluator; topic returned to pool."))
        return

    if st.button(_("Write & Evaluate Draft"), key=f"write_draft_{item['id']}"):
        try:
            outcome = services.produce_and_evaluate_draft(
                workspace_id=workspace["workspace_id"],
                brief_id=latest_brief["id"],
                user_id=user["user_id"],
                offline=_use_offline_brief_mode(workspace["workspace_id"], user["user_id"]),
            )
            st.session_state["selected_review_draft_id"] = str(outcome["final_draft_id"])
            st.rerun()
        except Exception as exc:
            st.error(str(exc))


def _render_approved_brief_section(
    item: dict[str, Any],
    user: dict[str, Any],
    workspace: dict[str, Any],
) -> None:
    latest_brief = item.get("latest_brief")
    if latest_brief is not None:
        _render_brief_panel(latest_brief)
        _render_approved_brief_draft_controls(item, latest_brief, user, workspace)
        return

    brand_row = services.current_brand(
        workspace_id=workspace["workspace_id"],
        user_id=user["user_id"],
    )
    if brand_row is None:
        st.caption(_("Configure a Brand Profile to generate an editorial brief."))
        return

    if st.button(_("Generate Brief"), key=f"gen_brief_{item['id']}"):
        try:
            services.generate_brief(
                workspace_id=workspace["workspace_id"],
                opportunity_id=item["id"],
                user_id=user["user_id"],
                offline=_use_offline_brief_mode(workspace["workspace_id"], user["user_id"]),
            )
            st.rerun()
        except Exception as exc:
            st.error(str(exc))


def _handle_approve_click(
    opp_id: uuid.UUID,
    user: dict[str, Any],
    workspace: dict[str, Any],
    note: str | None,
) -> None:
    try:
        services.decide_topic(
            workspace_id=workspace["workspace_id"],
            opportunity_id=opp_id,
            decision="approved",
            user_id=user["user_id"],
            note=note,
        )
    except Exception as exc:
        st.error(str(exc))
        return

    brand_row = services.current_brand(
        workspace_id=workspace["workspace_id"],
        user_id=user["user_id"],
    )
    if brand_row is not None:
        try:
            services.generate_brief(
                workspace_id=workspace["workspace_id"],
                opportunity_id=opp_id,
                user_id=user["user_id"],
                offline=_use_offline_brief_mode(workspace["workspace_id"], user["user_id"]),
            )
        except Exception as brief_exc:
            st.warning(str(brief_exc))
    st.success(_("Topic approved! Brief generation queued."))
    st.rerun()


def _render_opportunity_actions(
    item: dict[str, Any],
    user: dict[str, Any],
    workspace: dict[str, Any],
) -> None:
    """Render Gate 1 Decision UI actions and status alerts (F10 AC1-AC5, F12 AC1)."""
    status = item.get("status", "scored")
    if status == "approved":
        st.success(_("Topic Approved"))
        if item.get("decision_note"):
            st.caption(f"**{_('Decision Note')}:** {item['decision_note']}")
        _render_approved_brief_section(item, user, workspace)
        return

    if status == "rejected":
        st.error(_("Topic Rejected"))
        if item.get("decision_note"):
            st.caption(f"**{_('Decision Note')}:** {item['decision_note']}")
        return

    if status == "expired":
        st.info(_("Topic Expired (Not decided in previous run)"))
        return

    note_key = f"note_{item['id']}"
    note_val = st.text_input(
        _("Decision Note (optional)"),
        key=note_key,
        placeholder=_("e.g. Focus on practical tips"),
    )
    b1, b2, _spacer = st.columns([1, 1, 3])
    with b1:
        if st.button(_("Approve"), key=f"approve_{item['id']}", type="primary"):
            _handle_approve_click(
                item["id"],
                user,
                workspace,
                note_val.strip() if note_val else None,
            )
    with b2:
        if st.button(_("Reject"), key=f"reject_{item['id']}"):
            try:
                services.decide_topic(
                    workspace_id=workspace["workspace_id"],
                    opportunity_id=item["id"],
                    decision="rejected",
                    user_id=user["user_id"],
                    note=note_val.strip() if note_val else None,
                )
                st.warning(_("Topic rejected."))
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

        st.divider()


def _render_weekly_report(user: dict[str, Any], workspace: dict[str, Any]) -> None:
    """Render weekly opportunity report (F09 AC3-AC5)."""
    st.subheader(_("Weekly Opportunity Report"))
    campaign = services.get_active_campaign(
        workspace_id=workspace["workspace_id"],
        user_id=user["user_id"],
    )
    if not campaign:
        st.warning(_("No active campaign configured for this workspace."))
        return

    runs = services.list_runs(
        workspace_id=workspace["workspace_id"],
        user_id=user["user_id"],
        campaign_id=campaign["id"],
    )
    if not runs:
        st.info(_("No runs recorded yet. Trigger a run from the Runs page to generate reports."))
        return

    run_options = {
        str(r["id"]): f"{str(r['id'])[:8]}... ({r.get('status', 'unknown')})" for r in runs
    }
    session_run_id = st.session_state.get("selected_report_run_id")
    default_idx = 0
    if session_run_id and session_run_id in run_options:
        default_idx = list(run_options.keys()).index(session_run_id)

    selected_run_id_str = st.selectbox(
        _("Select Run"),
        options=list(run_options.keys()),
        index=default_idx,
        format_func=lambda rid: run_options.get(rid, rid),
        key="report_run_selector",
    )
    selected_run_id = uuid.UUID(selected_run_id_str)

    report_data = services.report(
        workspace_id=workspace["workspace_id"],
        run_id=selected_run_id,
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric(_("Articles / Week"), report_data.get("articles_per_week", 1))
    with c2:
        st.metric(_("Recommended Topics"), report_data.get("recommended_count", 0))
    with c3:
        st.metric(_("Total Opportunities"), report_data.get("total_count", 0))

    items = report_data.get("items", [])
    if not items:
        st.info(_("No opportunities scored for this run."))
        return

    for item in items:
        _render_opportunity_card(item, user, workspace)


def _draft_status_label(status: str) -> str:
    labels = {
        "pass": _("Awaiting Approval"),
        "approved": _("Draft Approved"),
        "declined": _("Draft Declined"),
        "published": _("Draft Published"),
    }
    return labels.get(status, status)


def _format_claim_date_status(raw_status: Any) -> str:
    norm = str(raw_status or "").strip().lower()
    if norm == "verified":
        return _("Verified")
    if norm == "unverified":
        return _("Unverified")
    return _("N/A")


def _handle_draft_decision(
    *,
    draft_id: uuid.UUID,
    decision: str,
    user: dict[str, Any],
    workspace: dict[str, Any],
    note: str | None,
) -> None:
    try:
        services.decide_draft(
            workspace_id=workspace["workspace_id"],
            draft_id=draft_id,
            decision=decision,
            user_id=user["user_id"],
            note=note,
        )
        st.session_state["selected_review_draft_id"] = str(draft_id)
        st.rerun()
    except Exception as exc:
        st.error(str(exc))


def _handle_draft_publish(
    *,
    draft_id: uuid.UUID,
    user: dict[str, Any],
    workspace: dict[str, Any],
    retry: bool = False,
) -> None:
    fn = services.retry_publication if retry else services.publish_draft
    try:
        fn(
            workspace_id=workspace["workspace_id"],
            draft_id=draft_id,
            user_id=user["user_id"],
        )
        st.session_state["selected_review_draft_id"] = str(draft_id)
        st.rerun()
    except Exception as exc:
        st.error(str(exc))


def _render_publication_details(publication: dict[str, Any]) -> None:
    pub_status = str(publication.get("status") or "")
    dest = str(publication.get("destination") or "")
    mode = str(publication.get("mode") or "draft")
    st.markdown(
        f"**{_('Destination')}:** `{dest}` · "
        f"**{_('Publish Mode')}:** `{mode}` · "
        f"**{_('Publication Status')}:** `{pub_status}`"
    )
    pub_url = publication.get("url")
    if pub_url:
        st.markdown(f"**{_('Published URL')}:** [{pub_url}]({pub_url})")
    if pub_status == "failed":
        err_msg = str(publication.get("error") or _("Unknown error"))
        st.error(f"{_('Publication failed')}: {err_msg}")


def _render_publication_status(
    bundle: dict[str, Any],
    user: dict[str, Any],
    workspace: dict[str, Any],
) -> None:
    draft_id = bundle["draft"]["id"]
    draft_id_str = str(draft_id)
    publication = bundle.get("publication")
    has_publisher = bool(bundle.get("has_publisher"))

    if publication is not None:
        _render_publication_details(publication)
        if not has_publisher:
            return
        if str(publication.get("status") or "") == "failed":
            if st.button(
                _("Retry Publish"),
                key=f"draft_retry_publish_{draft_id_str}",
                type="primary",
            ):
                _handle_draft_publish(draft_id=draft_id, user=user, workspace=workspace, retry=True)
            return
        if st.button(_("Re-publish"), key=f"draft_republish_{draft_id_str}"):
            _handle_draft_publish(draft_id=draft_id, user=user, workspace=workspace, retry=False)
        return

    if has_publisher and st.button(
        _("Publish Now"), key=f"draft_publish_now_{draft_id_str}", type="primary"
    ):
        _handle_draft_publish(draft_id=draft_id, user=user, workspace=workspace, retry=False)


def _render_draft_decision_controls(
    bundle: dict[str, Any],
    user: dict[str, Any],
    workspace: dict[str, Any],
) -> None:
    draft = bundle["draft"]
    status = str(draft.get("status") or "")
    note = draft.get("decision_note")
    if status == "approved":
        st.success(_("Draft Approved"))
        if note:
            st.caption(f"**{_('Decision Note')}:** {note}")
        _render_publication_status(bundle, user, workspace)
        return
    if status == "declined":
        st.error(_("Draft Declined"))
        if note:
            st.caption(f"**{_('Decision Note')}:** {note}")
        return
    if status == "published":
        st.info(_("Draft Published"))
        if note:
            st.caption(f"**{_('Decision Note')}:** {note}")
        _render_publication_status(bundle, user, workspace)
        return

    draft_id_str = str(draft["id"])
    note_val = st.text_input(
        _("Decision Note (optional)"),
        key=f"draft_note_{draft_id_str}",
        placeholder=_("e.g. Verified numbers and brand tone"),
    )
    clean_note = note_val.strip() if note_val else None
    b1, b2, _spacer = st.columns([1, 1, 3])
    with b1:
        if st.button(_("Approve"), key=f"draft_approve_{draft_id_str}", type="primary"):
            _handle_draft_decision(
                draft_id=draft["id"],
                decision="approved",
                user=user,
                workspace=workspace,
                note=clean_note,
            )
    with b2:
        if st.button(_("Decline"), key=f"draft_decline_{draft_id_str}"):
            _handle_draft_decision(
                draft_id=draft["id"],
                decision="declined",
                user=user,
                workspace=workspace,
                note=clean_note,
            )


def _render_draft_article_preview(draft: dict[str, Any]) -> None:
    payload = draft.get("payload") or {}
    title = str(payload.get("title") or "Draft")
    slug = str(payload.get("slug") or "")
    meta_desc = str(payload.get("meta_description") or "")
    body_md = str(payload.get("body_md") or "")
    faq = payload.get("faq") or []
    cta_raw = payload.get("cta")

    with st.expander(_("Article Preview"), expanded=True):
        st.markdown(f"### {title}")
        st.markdown(f"**{_('Slug')}:** `{slug}`")
        st.markdown(f"**{_('Meta Description')}:** {meta_desc}")
        st.markdown(body_md)
        if faq:
            st.markdown(f"**{_('FAQ')}:**")
            for pair in faq:
                if isinstance(pair, dict):
                    q_txt = pair.get("question") or pair.get("q") or ""
                    a_txt = pair.get("answer") or pair.get("a") or ""
                    st.markdown(f"- **{q_txt}** — {a_txt}")
        if isinstance(cta_raw, dict):
            parts = [str(cta_raw.get(k) or "").strip() for k in ("headline", "body", "button_text")]
            cta_text = " — ".join(p for p in parts if p)
            if cta_text:
                st.markdown(f"**{_('Call to Action')}:** {cta_text}")
        elif cta_raw:
            st.markdown(f"**{_('Call to Action')}:** {cta_raw}")


def _render_draft_claims_table(claims: list[dict[str, Any]]) -> None:
    st.markdown(f"#### {_('Claims & Sources')}")
    if not claims:
        st.caption(_("No claims recorded for this draft."))
        return

    lines = [
        (
            f"| {_('Claim')} | {_('Kind')} | {_('Source')} | "
            f"{_('URL')} | {_('Publication Date')} | {_('Date Status')} |"
        ),
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for claim in claims:
        claim_text = str(claim.get("text") or "").replace("|", "\\|").replace("\n", " ")
        kind_raw = str(claim.get("kind") or "fact").lower()
        kind_label = _("Fact") if kind_raw == "fact" else _("Analysis")
        doc_title = str(claim.get("document_title") or _("Unlinked Source")).replace("|", "\\|")
        doc_url = str(claim.get("document_url") or "").strip()
        url_cell = f"[{doc_url}]({doc_url})" if doc_url else _("N/A")
        pub_at = claim.get("document_published_at")
        pub_str = i18n.format_date(pub_at) if pub_at else _("N/A")
        status_str = _format_claim_date_status(claim.get("document_date_status"))
        lines.append(
            f"| {claim_text} | {kind_label} | {doc_title} | {url_cell} | {pub_str} | {status_str} |"
        )
    st.markdown("\n".join(lines))


def _render_draft_quality_section(quality_report: dict[str, Any] | None) -> None:
    st.markdown(f"#### {_('Quality Evaluation')}")
    if quality_report is None:
        st.info(_("No quality evaluation recorded for this draft yet."))
        return

    score_val = float(quality_report.get("score") or 0.0)
    verdict = str(quality_report.get("verdict") or "pass").upper()
    model = str(quality_report.get("evaluator_model") or "N/A")
    st.markdown(
        f"**{_('Evaluator Verdict')}:** `{verdict}` · "
        f"**{_('Score')}:** `{score_val:.1f}/100` · "
        f"**{_('Model')}:** `{model}`"
    )

    checks = quality_report.get("checks") or {}
    st.markdown(f"**{_('Deterministic Checks')}:**")
    if isinstance(checks, dict) and checks:
        for check_key, check_data in checks.items():
            if not isinstance(check_data, dict):
                continue
            label = "PASS" if check_data.get("passed") else "FAIL"
            sev = str(check_data.get("severity") or "major")
            detail = str(check_data.get("detail") or "")
            name = str(check_data.get("check") or check_key)
            suffix = f" — {detail}" if detail else ""
            st.markdown(f"- `[{label}]` **{name}** (`{sev}`){suffix}")
    else:
        st.caption(_("No deterministic checks recorded."))

    findings = quality_report.get("findings") or []
    st.markdown(f"**{_('Evaluator Findings')}:**")
    if findings:
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            sev = str(finding.get("severity") or "minor").upper()
            crit = str(finding.get("criterion") or "quality")
            mod = str(finding.get("responsible_module") or "write")
            detail = str(finding.get("detail") or "")
            st.markdown(f"- `[{sev}]` **{crit}** (`{mod}`): {detail}")
    else:
        st.caption(_("No evaluator findings recorded."))


def _render_draft_revision_history(history: list[dict[str, Any]]) -> None:
    st.markdown(f"#### {_('Revision History')}")
    if not history:
        st.caption(_("No revision history found."))
        return

    lines = [
        (
            f"| {_('Revision')} | {_('Brief Version')} | {_('Status')} | "
            f"{_('Evaluator Verdict')} | {_('Score')} | {_('Created')} |"
        ),
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in history:
        rev = int(entry.get("revision") or 0)
        bver = int(entry.get("brief_version") or 1)
        st_label = _draft_status_label(str(entry.get("status") or "draft"))
        verdict = str(entry.get("quality_verdict") or "—").upper()
        sc = entry.get("quality_score")
        sc_str = f"{float(sc):.1f}/100" if sc is not None else "—"
        created = entry.get("created_at")
        dt_str = i18n.format_date(created) if created else _("N/A")
        lines.append(f"| r{rev} | v{bver} | {st_label} | `{verdict}` | `{sc_str}` | {dt_str} |")
    st.markdown("\n".join(lines))


def _render_draft_cost_breakdown(
    breakdown: list[dict[str, Any]],
    total_cost: Any,
) -> None:
    st.markdown(f"#### {_('Run Cost Breakdown')}")
    st.markdown(f"**{_('Total Run Cost')}:** {i18n.format_currency_usd(float(total_cost or 0))}")
    if not breakdown:
        st.caption(_("No stage costs recorded for this run yet."))
        return

    lines = [
        (
            f"| {_('Stage')} | {_('Calls')} | {_('Input Tokens')} | "
            f"{_('Output Tokens')} | {_('Cost')} |"
        ),
        "| --- | --- | --- | --- | --- |",
    ]
    for row in breakdown:
        stage = str(row.get("stage") or "unknown")
        calls = int(row.get("calls") or 0)
        in_tok = int(row.get("input_tokens") or 0)
        out_tok = int(row.get("output_tokens") or 0)
        cost_str = i18n.format_currency_usd(float(row.get("cost_usd") or 0))
        lines.append(f"| `{stage}` | {calls} | {in_tok:,} | {out_tok:,} | {cost_str} |")
    st.markdown("\n".join(lines))


def _render_draft_review_card(
    bundle: dict[str, Any],
    user: dict[str, Any],
    workspace: dict[str, Any],
) -> None:
    draft = bundle["draft"]
    ctx = bundle["context"]
    if bundle.get("surfaced_after_max_revisions"):
        st.warning(_("Surfaced after 2 revisions for owner decision."))

    st.caption(
        f"**{_('Campaign Name')}:** {ctx.get('campaign_name', '')} · "
        f"**{_('Brief Version')}:** v{int(ctx.get('brief_version') or 1)} · "
        f"**{_('Revision')}:** r{int(draft.get('revision') or 0)} · "
        f"**{_('Status')}:** {_draft_status_label(str(draft.get('status') or ''))}"
    )
    _render_draft_decision_controls(bundle, user, workspace)
    _render_draft_article_preview(draft)
    _render_draft_claims_table(bundle.get("claims") or [])
    _render_draft_quality_section(bundle.get("quality_report"))
    _render_draft_revision_history(bundle.get("revision_history") or [])
    _render_draft_cost_breakdown(
        bundle.get("cost_breakdown") or [],
        bundle.get("total_run_cost_usd"),
    )


def _render_draft_review(user: dict[str, Any], workspace: dict[str, Any]) -> None:
    """Render Gate 2 Draft Review screen (F15 AC1-AC5)."""
    st.subheader(_("Draft Review"))
    reviews = services.list_draft_reviews(
        workspace_id=workspace["workspace_id"],
        user_id=user["user_id"],
        include_decided=True,
    )
    if not reviews:
        st.info(
            _(
                "No drafts awaiting review or decided yet. "
                "Generate a draft from an approved brief on the Weekly Report."
            )
        )
        return

    pending_count = sum(1 for r in reviews if r.get("status") == "pass")
    approved_count = sum(1 for r in reviews if r.get("status") == "approved")
    published_count = sum(1 for r in reviews if r.get("status") == "published")
    declined_count = sum(1 for r in reviews if r.get("status") == "declined")
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric(_("Awaiting Approval"), pending_count)
    with m2:
        st.metric(_("Approved"), approved_count)
    with m3:
        st.metric(_("Published"), published_count)
    with m4:
        st.metric(_("Declined"), declined_count)

    options = {
        str(r["id"]): (
            f"{str((r.get('payload') or {}).get('title') or 'Draft')[:60]} "
            f"(r{int(r.get('revision') or 0)} · {_draft_status_label(str(r.get('status') or ''))})"
        )
        for r in reviews
    }
    selected_session = st.session_state.get("selected_review_draft_id")
    default_idx = (
        list(options.keys()).index(selected_session)
        if selected_session and selected_session in options
        else 0
    )
    chosen_id_str = st.selectbox(
        _("Select Draft"),
        options=list(options.keys()),
        index=default_idx,
        format_func=lambda did: options.get(did, did),
        key="draft_review_selector",
    )
    if not chosen_id_str:
        return

    bundle = services.get_draft_review_bundle(
        workspace_id=workspace["workspace_id"],
        draft_id=uuid.UUID(chosen_id_str),
        user_id=user["user_id"],
    )
    _render_draft_review_card(bundle, user, workspace)


def _render_dashboard(
    user: dict[str, Any] | None = None,
    workspace: dict[str, Any] | None = None,
) -> None:
    """Render system overview dashboard."""
    st.subheader(_("System Overview"))
    st.markdown(f"**{_('Current Date')}:** {i18n.format_date(datetime.now())}")
    st.markdown(f"**{_('Estimated Monthly Cap')}:** {i18n.format_currency_usd(150.00)}")
    if user is not None and workspace is not None:
        st.divider()
        _render_cost_view(user, workspace)


def _render_audit(
    user: dict[str, Any],
    workspace: dict[str, Any],
) -> None:
    """Render CMS-agnostic URL & Site SEO/GEO Audit tab (M6-02 D4)."""
    st.subheader("SEO & GEO Audit")
    target_url = st.text_input("URL", value="https://", key="audit_target_url")
    plan = st.selectbox("Plan Tier", options=["free", "paid"], key="audit_plan_tier")
    max_pages = int(
        st.number_input("Max Pages", min_value=1, max_value=200, value=20, key="audit_max_pages")
    )
    if st.button("Run URL Audit", key="run_url_audit_btn") and target_url.startswith("http"):
        res = services.run_url_audit(
            url=target_url,
            workspace_id=workspace["workspace_id"],
            user_id=user["user_id"],
        )
        st.metric("Score", f"{res['overall_score']} ({res['grade']})")
        st.json(res)
    if st.button("Run Site Crawl", key="run_site_crawl_btn") and target_url.startswith("http"):
        cfg = CrawlConfig(
            plan="paid" if plan == "paid" else "free",
            max_pages=max_pages,
        )
        crawl_res = services.run_site_crawl(
            start_url=target_url,
            config=cfg,
            workspace_id=workspace["workspace_id"],
            user_id=user["user_id"],
        )
        st.metric(
            "Crawl Score",
            f"{crawl_res['overall_score']} ({crawl_res['grade']}) - {crawl_res['pages_crawled']}p",
        )
        st.json(crawl_res)


def _render_selected_page(
    selected_page: str,
    user: dict[str, Any],
    workspace: dict[str, Any],
) -> None:
    page_renderers = {
        "report": _render_weekly_report,
        "draft_review": _render_draft_review,
        "campaigns": _render_campaign_settings,
        "brand_profile": _render_brand_profile,
        "sources": _render_sources,
        "integrations": _render_integrations,
        "webhooks": _render_webhooks,
        "runs": _render_runs,
        "audit": _render_audit,
    }
    renderer = page_renderers.get(selected_page)
    if renderer is not None:
        renderer(user, workspace)
    else:
        _render_dashboard(user, workspace)


def _resolve_active_membership(memberships: list[dict[str, Any]]) -> dict[str, Any]:
    active_membership = memberships[0]
    if len(memberships) <= 1:
        return active_membership
    saved_id = getattr(st, "session_state", {}).get("active_workspace_id")
    ws_ids = [str(m["workspace_id"]) for m in memberships]
    if saved_id in ws_ids:
        return next(m for m in memberships if str(m["workspace_id"]) == saved_id)
    if hasattr(st, "session_state"):
        st.session_state["active_workspace_id"] = str(active_membership["workspace_id"])
    return active_membership


def _wizard_locale_selector() -> None:
    supported = list(i18n.SUPPORTED_LOCALES)
    current = str(getattr(st, "session_state", {}).get("ui_locale", i18n.DEFAULT_LOCALE))
    if current not in supported:
        current = i18n.DEFAULT_LOCALE
    chosen = st.selectbox("Idioma / Language", options=supported, index=supported.index(current))
    if chosen != current and hasattr(st, "session_state"):
        st.session_state["ui_locale"] = chosen
    i18n.set_current_locale(chosen)


def _render_wizard_step_one(wizard_data: dict[str, Any]) -> None:
    st.subheader(_("Step 1 of 3: Account & Capabilities"))
    email = st.text_input(
        _("Owner Email"),
        value=str(wizard_data.get("email", "")),
        key="wizard_email",
    )
    password = st.text_input(
        _("Admin Password"),
        value=str(wizard_data.get("password", "")),
        type="password",
        key="wizard_password",
    )
    ws_name = st.text_input(
        _("Workspace Name"),
        value=str(wizard_data.get("ws_name", "Pocket Workspace")),
        key="wizard_ws_name",
    )
    ws_slug = st.text_input(
        _("Workspace Slug"),
        value=str(wizard_data.get("ws_slug", "pocket-ws")),
        key="wizard_ws_slug",
    )
    available_packs = services.list_available_industry_packs()
    default_pack = str(wizard_data.get("industry_pack") or services.get_default_industry_pack())
    if available_packs:
        pack_idx = available_packs.index(default_pack) if default_pack in available_packs else 0
        selected_pack = st.selectbox(
            _("Industry Pack"),
            options=available_packs,
            index=pack_idx,
            key="wizard_industry_pack",
        )
    else:
        selected_pack = default_pack
    all_caps = list(capabilities.discover_capabilities().keys())
    default_caps = list(wizard_data.get("capabilities") or ["core-compliance", "seo-audit"])
    selected_caps = st.multiselect(
        _("Enabled Capabilities"),
        options=all_caps,
        default=[c for c in default_caps if c in all_caps],
        key="wizard_capabilities",
    )
    if st.button(_("Next"), key="wizard_next_1"):
        if not email.strip() or not ws_name.strip() or not ws_slug.strip():
            st.error(_("Please enter owner email, workspace name, and slug."))
            return
        wizard_data.update(
            {
                "email": email.strip().lower(),
                "password": password,
                "ws_name": ws_name.strip(),
                "ws_slug": ws_slug.strip().lower(),
                "industry_pack": selected_pack,
                "capabilities": list(selected_caps) or ["core-compliance"],
            }
        )
        st.session_state["wizard_step"] = 2
        st.rerun()


def _render_wizard_step_two(wizard_data: dict[str, Any]) -> None:
    st.subheader(_("Step 2 of 3: Integration Stack"))
    presets = services.list_integration_presets()
    preset_names = [str(p["name"]) for p in presets]
    current_preset = str(wizard_data.get("preset", "pocket_free"))
    idx = preset_names.index(current_preset) if current_preset in preset_names else 0
    selected_preset = st.selectbox(
        _("Stack Preset"),
        options=preset_names,
        index=idx,
        key="wizard_preset",
    )
    port_overrides: dict[str, dict[str, Any]] = {}
    if selected_preset == "local_ollama":
        ollama_url = st.text_input(
            _("Ollama Base URL (optional)"),
            value=str(wizard_data.get("ollama_url", "http://localhost:11434/v1")),
            key="wizard_ollama_url",
        )
        if ollama_url.strip():
            port_overrides["llm"] = {"settings": {"base_url": ollama_url.strip()}}
        wizard_data["ollama_url"] = ollama_url.strip()
    elif selected_preset == "pro_cloud":
        api_key = st.text_input(
            _("API Key (optional)"),
            value="",
            type="password",
            key="wizard_pro_api_key",
        )
        if api_key.strip():
            port_overrides["llm"] = {"secrets": {"api_key": api_key.strip()}}

    col_back, col_next = st.columns(2)
    with col_back:
        if st.button(_("Back"), key="wizard_back_2"):
            st.session_state["wizard_step"] = 1
            st.rerun()
    with col_next:
        if st.button(_("Next"), key="wizard_next_2"):
            wizard_data["preset"] = selected_preset
            wizard_data["port_overrides"] = port_overrides
            st.session_state["wizard_step"] = 3
            st.rerun()


def _complete_wizard_setup(wizard_data: dict[str, Any], site_url: str) -> None:
    password_val = str(wizard_data.get("password") or "") or None
    boot = services.bootstrap_onboarding(
        email=str(wizard_data["email"]),
        password=password_val,
        workspace_slug=str(wizard_data["ws_slug"]),
        workspace_name=str(wizard_data["ws_name"]),
        industry_pack=str(wizard_data.get("industry_pack") or services.get_default_industry_pack()),
        enabled_capabilities=wizard_data.get("capabilities"),
        preset_name=str(wizard_data.get("preset") or "pocket_free"),
        port_overrides=wizard_data.get("port_overrides") or None,
    )
    if site_url.strip():
        services.autoseed_from_url(
            workspace_id=boot["workspace"]["workspace_id"],
            user_id=boot["user"]["user_id"],
            site_url=site_url.strip(),
        )
    if hasattr(st, "session_state"):
        st.session_state["local_auth_email"] = boot["user"]["email"]
        st.session_state.pop("wizard_step", None)
        st.session_state.pop("wizard_data", None)
    st.rerun()


def _render_wizard_step_three(wizard_data: dict[str, Any]) -> None:
    st.subheader(_("Step 3 of 3: Website Auto-Seed"))
    site_url = st.text_input(
        _("Website URL (optional)"),
        value=str(wizard_data.get("site_url", "")),
        key="wizard_site_url",
    )
    col_back, col_done = st.columns(2)
    with col_back:
        if st.button(_("Back"), key="wizard_back_3"):
            wizard_data["site_url"] = site_url.strip()
            st.session_state["wizard_step"] = 2
            st.rerun()
    with col_done:
        if st.button(_("Complete Setup"), key="wizard_complete"):
            try:
                _complete_wizard_setup(wizard_data, site_url)
            except Exception as exc:
                st.error(str(exc))


def _render_setup_wizard() -> None:
    """Render bilingual 3-step First-Boot Setup Wizard when zero workspaces exist (ADR-031)."""
    _wizard_locale_selector()
    st.title(_("Macy Setup Wizard"))
    if hasattr(st, "session_state"):
        step = int(st.session_state.setdefault("wizard_step", 1))
        wizard_data: dict[str, Any] = st.session_state.setdefault("wizard_data", {})
    else:
        step = 1
        wizard_data = {}

    if step == 1:
        _render_wizard_step_one(wizard_data)
    elif step == 2:
        _render_wizard_step_two(wizard_data)
    else:
        _render_wizard_step_three(wizard_data)


def main() -> None:
    """Streamlit UI main function."""
    bootstrap.ensure_pocket_secrets()
    migrations.run_startup_migrations()
    start_embedded_worker()
    observability.ensure_sentry_initialized()
    if services.needs_first_boot_wizard():
        _render_setup_wizard()
        return
    identity = auth.get_current_identity()
    if identity is None:
        auth.render_login_screen()
        return

    user = identity["user"]
    memberships = identity["memberships"]

    if not user or not memberships:
        auth.render_access_denied(identity["email"])
        return

    _init_user_locale(user)
    active_membership = _resolve_active_membership(memberships)

    workspace = services.get_workspace_for_member(
        workspace_id=active_membership["workspace_id"],
        user_id=user["user_id"],
    )
    ws_id_str = str(workspace["workspace_id"])
    observability.set_observability_tags(workspace_id=ws_id_str)

    selected_page = _render_sidebar(user, workspace, memberships)

    st.title(_("Workspace: {name}").format(name=workspace["name"]))
    st.write(_("Welcome back, {email}!").format(email=user["email"]))
    try:
        _render_selected_page(selected_page, user, workspace)
    except Exception as exc:
        observability.capture_exception(
            exc,
            workspace_id=ws_id_str,
            extra={"page": selected_page},
        )
        raise


if __name__ == "__main__":
    main()
